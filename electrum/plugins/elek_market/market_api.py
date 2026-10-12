#!/usr/bin/env python
# Elektron Market-api client (network layer only).
#
# Principals (Kampagne #61):
#   * fetches FX snapshot: GET  <endpoint>/fx/rates.json
#   * fetches orderbook:   POST <endpoint>/rpc  {"method":"orderbook", ...}
#   * the elek-web proxy injects userpass server-side, so this module never
#     handles secrets.
#   * strictly read-only; no swaps/deposits/withdrawals in this client.
#
import json

import aiohttp


def _verified_ssl():
    """TLS config for HTTPS endpoints (e.g. https://elektron-market.onrender.com).

    On Android the bundled OpenSSL has no usable default CA directory, so
    aiohttp's plain `ssl=True` fails with ClientConnectorCertificateError.
    Verify against certifi's bundle instead (electrum core ships it —
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
    def parse_orderbook(ob: dict) -> dict:
        if not isinstance(ob, dict):
            raise MarketApiError(-2, "orderbook is not an object")
        if "error" in ob:
            raise MarketApiError(-3, ob["error"])
        asks = []
        for a in ob.get("asks") or []:
            asks.append({
                "price": str(a.get("price")),
                "max_volume": str(a.get("maxvolume")),
                "address": a.get("address", ""),
            })
        bids = []
        for b in ob.get("bids") or []:
            bids.append({
                "price": str(b.get("price")),
                "max_volume": str(b.get("maxvolume")),
                "address": b.get("address", ""),
            })
        return {
            "pair": "%s/%s" % (ob.get("base", "?"), ob.get("rel", "?")),
            "asks": asks,
            "bids": bids,
            "total_asks_base_vol": str(ob.get("total_asks_base_vol", "")),
            "total_bids_rel_vol": str(ob.get("total_bids_rel_vol", "")),
        }


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

    async def my_swap_status(self, uuid: str) -> dict:
        return await self._post({
            "userpass": self.userpass, "method": "my_swap_status",
            "params": {"uuid": uuid}, "mmrpc": "2.0"})

    async def my_recent_swaps(self, limit: int = 1) -> dict:
        return await self._post({
            "userpass": self.userpass, "method": "my_recent_swaps",
            "params": {"limit": limit}, "mmrpc": "2.0"})

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
