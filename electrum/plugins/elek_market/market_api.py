#!/usr/bin/env python
# Elektron Market-api client (network layer only).
#
# Principals (Kampagne #61):
#   * fetches FX snapshot: GET  <endpoint>/fx/rates.json
#   * fetches orderbook:   POST <endpoint>/rpc  {"method":"orderbook", ...}
#   * the elek-web proxy injects userpass server-side, so this module never
#     handles market secrets.
#   * the market layer stays read-only; money-moving calls (buy/setprice,
#     enable/my_balance) target the user's OWN client-kdf (KdfClient only).
#
import json
from decimal import Decimal, localcontext

import aiohttp


def _verified_ssl():
    """TLS config for HTTPS endpoints (e.g. https://elektron-market.onrender.com).

    On Android the bundled OpenSSL has no usable default CA directory, so
    aiohttp's plain `ssl=True` fails with ClientConnectorCertificateError.
    Verify against certifi's bundle instead (electrum core ships it -
    electrum/interface.py:74 does the same for its own sockets)."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return True


class MarketApiError(Exception):
    ROOT = "market-api"

    def __init__(self, code=None, body=None):
        self.code = code
        self.body = body
        super().__init__(f"market-api {code}: {str(body)[:200]}")


# SEPOLIAETH legacy-`enable` extras, copied verbatim from the #77 cross-swap
# e2e harness (scratches/77eth/p77_enable.py / p77_fed_seedfix.py) - these
# exact values carried the both-sides-Finished ETH<->UTXO swaps. EIP-55
# casing is mandatory for the contract address (eth.rs rejects otherwise);
# coins-live.json does NOT carry urls/swap_contract_address, so the app must
# merge these in on enable.
SEPOLIA_SWAP_CONTRACT = "0xeA6D65434A15377081495a9E7C5893543E7c32cB"
SEPOLIA_RPC_URLS = [
    "https://ethereum-sepolia-rpc.publicnode.com",
    "https://sepolia.drpc.org",
]


def sepolia_enable_params() -> dict:
    return {
        "urls": list(SEPOLIA_RPC_URLS),
        "swap_contract_address": SEPOLIA_SWAP_CONTRACT,
    }


# ---- #73 mainnet activation constants (branch elektron/61-market-mainnet) -
# Same provenance rule as the SEPOLIA block above: every value carries live
# evidence, nothing guessed.

# UTXO electrum-activation servers. The app ships no native RPC node, so
# UTXO coins MUST use the legacy `electrum` method with a servers array -
# legacy `enable` forces UtxoRpcMode::Native (utxo.rs from_legacy_req; kdf
# has NO conf-based electrum fallback). ELEK: the public Elektron electrs
# (fork chains/mainnet/servers.json; TCP-live-probed 2026-10-13; phone #85
# used electrs.elektron-net.org with the correct fork genesis). BTC: cipig
# cluster - coins-mainnet.json lists electrum1+electrum3, but electrum3
# refuses TCP from this network (both 2026-10-13 probes); electrum2 is
# TCP-live AND chain-identical (both answered ElectrumX 2.0.0, height
# 971208), so electrum1+electrum2 are the two live servers.
ELEK_ELECTRUM_SERVERS = ("electrs.elektron-net.org:50002",
                         "electrs2.elektron-net.org:50002")
BTC_ELECTRUM_SERVERS = ("btc.electrum1.cipig.net:10000",
                        "btc.electrum2.cipig.net:10000")

# ETH mainnet enable extras: RPC twins of the SEPOLIA set (both alive with
# eth_chainId "0x1", probed 2026-10-13) + the komodo mainnet swap contract,
# whose EIP-55 casing is byte-identical in kdf eth.rs:227 (etherscan
# comment) and eth_tests.rs:730, recomputed via keccak (2026-10-13).
ETH_MAINNET_SWAP_CONTRACT = "0x8500AFc0bc5214728082163326C2FF0C73f4a871"
ETH_MAINNET_RPC_URLS = [
    "https://ethereum-rpc.publicnode.com",
    "https://eth.drpc.org",
]

# Coins tradeable in the app's MAINNET market (the #91 mandate: btc, eth,
# elek on the #73 branch split).
MAINNET_COINS = ("ELEK", "BTC", "ETH")


def eth_mainnet_enable_params() -> dict:
    return {
        "urls": list(ETH_MAINNET_RPC_URLS),
        "swap_contract_address": ETH_MAINNET_SWAP_CONTRACT,
    }


def electrum_servers(ticker: str) -> list:
    """Legacy `electrum` activation servers for a mainnet UTXO ticker
    (#73): [{url, protocol}]; ETH has none (eth.rs activates by urls)."""
    urls = {"ELEK": ELEK_ELECTRUM_SERVERS,
            "BTC": BTC_ELECTRUM_SERVERS}.get(ticker.upper()) or ()
    return [{"url": u, "protocol": "TCP"} for u in urls]


# Coins tradeable in the app's TESTNET market (the #91 mandate: btc, eth,
# elek). Testnet pairs stay the DEFAULT; mainnet pairs live above (branch
# split #73).
TESTNET_COINS = ("tELEK", "tBTC", "SEPOLIAETH")


def dec_str(x) -> str:
    """Decimal string without float artifacts (no 0.35000000000000003)."""
    d = x if isinstance(x, Decimal) else Decimal(str(x))
    s = format(d.normalize(), "f")
    return s or "0"


def dec_mul(a, b) -> str:
    return dec_str(Decimal(dec_str(a)) * Decimal(dec_str(b)))


def dec_div(a, b, prec: int = 12) -> str:
    """a/b with bounded precision (keeps reciprocals human-shaped:
    dec_div('1', '3') == '0.333333333333')."""
    with localcontext() as ctx:
        ctx.prec = prec
        return dec_str(Decimal(dec_str(a)) / Decimal(dec_str(b)))


class MarketApi:

    def __init__(self, endpoint: str, timeout: float = 12.0):
        self._endpoint = ""
        self._timeout = None
        self._session: aiohttp.ClientSession | None = None
        self.set_endpoint(endpoint, timeout)

    def set_endpoint(self, endpoint: str, timeout: float = 12.0):
        self._endpoint = (endpoint or "").rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout, connect=5.0)
        # drop the old session's transport so we re-dial against the new host;
        # the new session is created lazily on first request.
        self._session = None

    @property
    def endpoint(self) -> str:
        return self._endpoint

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                connector=aiohttp.TCPConnector(ssl=_verified_ssl()),
                headers={"Accept": "application/json"})
        return self._session

    async def close(self):
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def get_rates_raw(self) -> dict:
        url = self._endpoint + "/fx/rates.json"
        session = await self._get_session()
        try:
            async with session.get(url) as reply:
                text = await reply.text()
                if reply.status != 200:
                    raise MarketApiError(reply.status, text)
                return json.loads(text)
        except MarketApiError:
            raise
        except Exception as e:
            raise MarketApiError("net", f"{type(e).__name__}: {e}") from e

    def rates_to_pairs(self, snapshot: dict) -> dict:
        if not isinstance(snapshot, dict):
            raise MarketApiError(-1, "rates snapshot is not an object")
        out = {}
        if snapshot.get("usd") is not None:
            out["USD"] = snapshot["usd"]
        if snapshot.get("eur") is not None:
            out["EUR"] = snapshot["eur"]
        if snapshot.get("source"):
            out["source"] = snapshot["source"]
        if snapshot.get("market") is not None:
            out["market"] = snapshot["market"]
        return out

    async def get_rates(self) -> dict:
        return self.rates_to_pairs(await self.get_rates_raw())

    async def get_orderbook(self, base: str, rel: str) -> dict:
        url = self._endpoint + "/rpc"
        session = await self._get_session()
        payload = {"method": "orderbook", "base": base, "rel": rel}
        try:
            async with session.post(url, json=payload) as reply:
                text = await reply.text()
                if reply.status != 200:
                    raise MarketApiError(reply.status, text)
                return json.loads(text)
        except MarketApiError:
            raise
        except Exception as e:
            raise MarketApiError("net", f"{type(e).__name__}: {e}") from e

    @staticmethod
    def _num(x) -> str:
        """Book quantity as decimal string: legacy books return plain
        strings/numbers, mmrpc-2.0 books return fractions like
        {"numer": N, "denom": D} (memory #83: Buch-Preise = fraction-Dicts)."""
        if isinstance(x, dict):
            return dec_div(x.get("numer", 0), x.get("denom", 1))
        return str(x)

    @staticmethod
    def parse_orderbook(ob: dict) -> dict:
        if not isinstance(ob, dict):
            raise MarketApiError(-2, "orderbook is not an object")
        if "error" in ob:
            raise MarketApiError(-3, ob["error"])
        asks = []
        for a in ob.get("asks") or []:
            asks.append({
                "price": MarketApi._num(a.get("price")),
                "max_volume": MarketApi._num(a.get("maxvolume")),
                "address": a.get("address", ""),
            })
        bids = []
        for b in ob.get("bids") or []:
            bids.append({
                "price": MarketApi._num(b.get("price")),
                "max_volume": MarketApi._num(b.get("maxvolume")),
                "address": b.get("address", ""),
            })
        return {
            "pair": "%s/%s" % (ob.get("base", "?"), ob.get("rel", "?")),
            "asks": asks,
            "bids": bids,
            "total_asks_base_vol": MarketApi._num(ob.get("total_asks_base_vol", "")),
            "total_bids_rel_vol": MarketApi._num(ob.get("total_bids_rel_vol", "")),
        }


def rates_from_book(book: dict) -> dict:
    """#96: derive the market rate from a PARSED orderbook - the kdf book's
    mid IS the market rate (electrs fx convention, #88: mid=(ask+bid)/2 when
    both sides quote, else the single quoted side). Shape matches the raw
    /fx/rates.json snapshot (lowercase keys) so QML keeps one reading path
    (r.mid/r.source; r.usd/r.eur are absent in the local-kdf form - the QML
    label tolerates both). Prices are parsed from the wire STRINGS via
    Decimal (no float artifacts). Raises MarketApiError(-4) on an empty
    book (best_ask nor best_bid quotes anything)."""
    if not isinstance(book, dict):
        raise MarketApiError(-2, "book is not an object")
    try:
        ask = (Decimal(dec_str(book["asks"][0]["price"]))
               if book.get("asks") else None)
        bid = (Decimal(dec_str(book["bids"][0]["price"]))
               if book.get("bids") else None)
    except (ArithmeticError, KeyError, IndexError, TypeError, ValueError):
        raise MarketApiError(-2, "book entries malformed")
    if ask is not None and not ask.is_finite():
        ask = None
    if bid is not None and not bid.is_finite():
        bid = None
    if (ask is None or ask <= 0) and (bid is None or bid <= 0):
        raise MarketApiError(-4, "empty orderbook - no rate")
    if ask is not None and ask <= 0:
        ask = None  # degenerate single side
    elif bid is not None and bid <= 0:
        bid = None
    if ask is not None and bid is not None:
        with localcontext() as ctx:
            ctx.prec = 12
            mid = dec_str((ask + bid) / 2)
    elif ask is not None:
        mid = dec_str(ask)
    else:
        mid = dec_str(bid)
    return {"source": "local-kdf", "mid": mid,
            "pair": book.get("pair") or ""}


class KdfClient:
    """Client for the user's OWN client-kdf instance (M2 no-custody: the
    wallet process that holds the user's coins and runs the swaps).

    This never targets the market daemons. Legacy buy uses top-level
    fields (v1-style); status polling uses the mmrpc 2.0 wrapper.
    """

    FINAL_EVENTS = ("Finished", "Failed", "Refunded", "Successful", "Error")

    def __init__(self, url: str, userpass: str, timeout: float = 190.0):
        self.url = (url or "").rstrip("/")
        self.userpass = userpass or ""
        self._timeout = aiohttp.ClientTimeout(total=timeout, connect=5.0)
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                connector=aiohttp.TCPConnector(ssl=_verified_ssl()),
                headers={"Accept": "application/json"})
        return self._session

    async def close(self):
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _post(self, body: dict) -> dict:
        session = await self._get_session()
        try:
            async with session.post(self.url, json=body) as reply:
                text = await reply.text()
                if reply.status != 200:
                    raise MarketApiError(reply.status, text)
                return json.loads(text)
        except MarketApiError:
            raise
        except Exception as e:
            raise MarketApiError("net", f"{type(e).__name__}: {e}") from e

    async def buy_legacy(self, base: str, rel: str, price: str, volume: str) -> dict:
        return await self._post({"userpass": self.userpass, "method": "buy",
                                 "base": base, "rel": rel,
                                 "price": price, "volume": volume})

    async def setprice(self, base: str, rel: str, price: str, volume: str,
                       min_volume: str | None = None,
                       cancel_previous: bool = False) -> dict:
        """Legacy `setprice` (top-level fields, lp_ordermatch.rs SetPriceReq):
        place a maker order selling `base` for `rel` (price = rel per base).
        The daemon default for cancel_previous is True (it replaces every
        other own order of the same pair); we send False so the app can keep
        several orders and cancel them individually by uuid."""
        body = {"userpass": self.userpass, "method": "setprice",
                "base": base, "rel": rel, "price": price, "volume": volume,
                "cancel_previous": cancel_previous}
        if min_volume:
            body["min_volume"] = min_volume
        return await self._post(body)

    async def my_orders_legacy(self) -> dict:
        """Legacy `my_orders` - NOT mmrpc 2.0 (the v2 wrapper answers
        NoSuchMethod; routed only in dispatcher_legacy.rs). Response:
        {result: {maker_orders: {uuid: {...}}, taker_orders: {..}}}."""
        return await self._post({"userpass": self.userpass,
                                 "method": "my_orders"})

    async def cancel_order(self, uuid: str) -> dict:
        """Legacy `cancel_order` - uuid at top level (CancelOrderReq is
        deserialized from the request root)."""
        return await self._post({"userpass": self.userpass,
                                 "method": "cancel_order", "uuid": uuid})

    @staticmethod
    def parse_my_orders(raw: dict) -> dict:
        result = raw.get("result") if isinstance(raw, dict) else None
        if result is None and isinstance(raw, dict):
            # legacy wrappers surface failures as {"error": "..."}
            raise MarketApiError(-5, f"my_orders: {str(raw)[:200]}")
        if not isinstance(result, dict):
            raise MarketApiError(-5, f"my_orders: unexpected body {json.dumps(raw)[:200]}")
        makers = []
        for uuid, o in (result.get("maker_orders") or {}).items():
            if not (isinstance(o, dict) and o.get("uuid", uuid)):
                continue
            makers.append({
                "uuid": str(o.get("uuid", uuid)),
                "base": str(o.get("base", "")),
                "rel": str(o.get("rel", "")),
                "price": str(o.get("price", "")),
                "volume": str(o.get("max_base_vol", "")),
                "available": str(o.get("available_amount", "")),
                "cancellable": bool(o.get("cancellable", False)),
                "created_at": int(o.get("created_at", 0) or 0),
            })
        # newest first - the app shows fresh orders on top
        makers.sort(key=lambda m: m["created_at"], reverse=True)
        takers = []
        for uuid in (result.get("taker_orders") or {}):
            takers.append({"uuid": str(uuid)})
        return {"maker_orders": makers, "taker_orders": takers}

    async def my_swap_status(self, uuid: str) -> dict:
        return await self._post({
            "userpass": self.userpass, "method": "my_swap_status",
            "params": {"uuid": uuid}, "mmrpc": "2.0"})

    async def my_recent_swaps(self, limit: int = 1) -> dict:
        return await self._post({
            "userpass": self.userpass, "method": "my_recent_swaps",
            "params": {"limit": limit}, "mmrpc": "2.0"})

    async def orderbook(self, base: str, rel: str) -> dict:
        """#96: the kdf book is the market truth - read it directly from the
        user's own kdf (same shape the elek-web /rpc proxy relays: legacy
        top-level askdepth/asks/bids/biddepth, #70/#74/#88 evidence)."""
        return await self._post({
            "userpass": self.userpass, "method": "orderbook",
            "base": base, "rel": rel})

    @staticmethod
    def find_uuid(obj) -> "str | None":
        """Search a legacy buy response for a swap uuid (depth-first). The
        buy response carries the uuid somewhere below `result` without a
        guaranteed top-level `uuid` key, so scan defensively."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                if (k == "uuid" and isinstance(v, str)
                        and len(v) == 36 and v.count("-") == 4):
                    return v
            for v in obj.values():
                found = KdfClient.find_uuid(v)
                if found:
                    return found
        elif isinstance(obj, list):
            for v in obj:
                found = KdfClient.find_uuid(v)
                if found:
                    return found
        return None

    @staticmethod
    def parse_swap_status(raw: dict) -> dict:
        """Shape (evidence, A4/`#61` e2e): result.swap_data.events[*].event.type.
        Falls back to result.events for older layouts."""
        result = raw.get("result") if isinstance(raw, dict) else None
        if not isinstance(result, dict):
            raise MarketApiError(-4, f"my_swap_status: unexpected body {json.dumps(raw)[:200]}")
        swap_data = result.get("swap_data") if isinstance(result.get("swap_data"), dict) else {}
        events_raw = swap_data.get("events") or result.get("events") or []
        types = []
        for e in events_raw:
            ev = e.get("event") if isinstance(e, dict) else None
            if isinstance(ev, dict):
                types.append(str(ev.get("type") or "-"))
        state = "Running"
        for t in types:
            if t == "Finished":
                state = "Finished"
                break
            if t in KdfClient.FINAL_EVENTS:
                state = t
        return {"types": types, "state": state}

    async def enable_coin(self, coin: str, params: dict | None = None) -> dict:
        """Legacy `enable` (top-level fields; the mmrpc 2.0 wrapper does not
        route it). `params` merges extra fields - SEPOLIAETH requires
        `urls` + `swap_contract_address` (sepolia_enable_params)."""
        body = {"userpass": self.userpass, "method": "enable",
                "coin": coin, "mm2": 1}
        if params:
            body.update(params)
        return await self._post(body)

    async def enable_electrum(self, coin: str, servers: list) -> dict:
        """Legacy `electrum` activation (#73) - the ONLY working mode for
        UTXO coins without a native RPC node: legacy `enable` answers
        UtxoRpcMode::Native (utxo.rs from_legacy_req reads req["servers"]
        ONLY for method "electrum"; kdf has no conf-based fallback)."""
        body = {"userpass": self.userpass, "method": "electrum",
                "coin": coin, "servers": servers, "mm2": 1}
        return await self._post(body)

    async def get_enabled_coins(self) -> list:
        """Legacy `get_enabled_coins` - answers a coin-name list, wrapped as
        {result: [...]} or bare depending on build; parse defensively."""
        raw = await self._post({"userpass": self.userpass,
                                "method": "get_enabled_coins"})
        return self.parse_enabled_coins(raw)

    @staticmethod
    def parse_enabled_coins(raw) -> list:
        """Accepts {result: [...]}, bare [...], {result: {coins: [...]}} -
        entries are coin-name strings (dicts tolerated)."""
        nodes = [raw]
        if isinstance(raw, dict):
            nodes += [raw.get("result"), (raw.get("result") or {}).get("coins")
                      if isinstance(raw.get("result"), dict) else None]
        for node in nodes:
            if isinstance(node, list):
                out = []
                for c in node:
                    if isinstance(c, dict):
                        c = c.get("coin") or c.get("ticker")
                    if c:
                        out.append(str(c))
                return out
        return []

    async def my_balance(self, coin: str) -> dict:
        """Legacy `my_balance` (v1 fields) - answers TOP-LEVEL
        {coin, balance, address, unspendable_balance}; the mmrpc 2.0 wrapper
        answers 400 for it (#70 evidence)."""
        return await self._post({"userpass": self.userpass,
                                 "method": "my_balance", "coin": coin})

    @staticmethod
    def parse_balance(raw, coin: str = "") -> dict:
        """Tolerates the legacy top-level shape and a {result: {...}} wrap;
        surfaces daemon errors ({"error": ...} / MM2 HTTPError) as `error`."""
        node = raw if isinstance(raw, dict) else {}
        res = node.get("result")
        if isinstance(res, dict):
            node = res
        return {
            "coin": str(node.get("coin") or coin),
            "balance": str(node.get("balance", "0") or "0"),
            "address": str(node.get("address", "") or ""),
            "unspendable": str(node.get("unspendable_balance", "0") or "0"),
            "error": str(node.get("error") or node.get("HTTPError") or ""),
        }
