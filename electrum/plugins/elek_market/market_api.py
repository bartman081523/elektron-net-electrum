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
