#!/usr/bin/env python
# Elektron Electrum - Market-API plugin (QML part for Android).
# Registers a QSignalObject under "MarketPlugin" context property so the new
# QML "Tausch" page can call market-API functions. Read-only orderbook/rates
# plus a buyer path to a user-owned client-kdf (no market-side custody).

from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal, pyqtSlot, pyqtProperty

from electrum.i18n import _
from electrum.plugin import hook
from electrum.gui.common_qt.plugins import PluginQObject

from . import Plugin as ElekMarketBase
from .market_api import MarketApi, MarketApiError, KdfClient


def retry_crossing(book: dict | None, kind: str, price: float) -> bool:
    """True when the order book (market_api.parse_orderbook shape, VIEW
    frame) holds a quote that crosses the resting taker order's own terms.

    #89 semantics: kdf matches a taker request only in the instant of its
    arrival (maker nodes call process_taker_request; a later-arriving
    MAKER order is merely inserted into the local book); an unmatched GTC
    taker order is auto-converted to a maker order after 30 s and from
    then on fills only against NEW taker arrivals - two maker orders never
    meet. So if the user's resting order now coexists with a crossing
    maker quote, filling requires cancel + re-issue (a fresh request).
    kind='ask': the order fills an ASK (buys `base` paying `rel`, cap
    `price`) -> crossed by any ask <= price. kind='bid': fills a BID (sells
    `base` for `rel`, floor `price`) -> crossed by any bid >= price."""
    if not isinstance(book, dict):
        return False
    try:
        side = "asks" if kind == "ask" else "bids"
        return any(
            0.0 < float(q["price"]) <= price + 1e-12
            if kind == "ask" else
            float(q["price"]) >= price - 1e-12 > 0.0
            for q in (book.get(side) or [])
        )
    except (TypeError, ValueError, KeyError):
        return False


TAKER_RETRY_MAX = 3
# kdf auto-converts an unmatched GTC taker order to a maker order after
# 30 s; a natural match settles within a few seconds of placement.
TAKER_RETRY_SETTLE_SECS = 45.0

if TYPE_CHECKING:
    from electrum.gui.qml import ElectrumGui, ElectrumQmlApplication


class QMarketSignalObject(PluginQObject):
    """QML-visible bridge to the market API. Exposed via
    `context.setContextProperty('MarketPlugin', self)` in init_qml.

    QML usage:
        MarketPlugin.endpoint
        MarketPlugin.refresh_rates()
        MarketPlugin.refresh_orderbook(base, rel)
        MarketPlugin.buy(base, rel, volume, price)        # taker vs BIDs
        MarketPlugin.buy_taker(base, rel, volume, price)  # taker vs ASKs
        MarketPlugin.make_order(base, rel, 'ask'|'bid', price, volume)
        MarketPlugin.cancel_order(uuid)
        MarketPlugin.refresh_my_orders()
        MarketPlugin.check_status()
        MarketPlugin.swapUuid / MarketPlugin.swapStatus
        MarketPlugin.myOrders / MarketPlugin.lastMakerOrder
    """

    ratesReady = pyqtSignal()
    orderbookReady = pyqtSignal()
    buyResult = pyqtSignal()
    swapStatusReady = pyqtSignal()
    myOrdersReady = pyqtSignal()
    makerOrderReady = pyqtSignal()
    error = pyqtSignal(str)

    _name = _('Elektron Market')

    def __init__(self, plugin, parent):
        super().__init__(plugin, parent)
        self._rates = None
        self._book = None
        self._last_buy = None
        self._last_error = None
        self._kdf: KdfClient | None = None
        self._swap_uuid = None
        self._swap_status = None
        self._my_orders = {}
        self._last_maker = None
        # #89 taker-retry state: the placed order's view terms + retry bookkeeping
        self._taker = None
        self._taker_since = 0.0
        self._retry_count = 0

    @pyqtProperty(str)
    def endpoint(self):
        return self.plugin.endpoint

    @endpoint.setter
    def endpoint(self, value):
        self.plugin.set_endpoint(value)

    @pyqtSlot()
    def refresh_rates(self):
        import asyncio as _asyncio
        from electrum.util import get_asyncio_loop
        api = self.plugin.get_api()
        fut = _asyncio.run_coroutine_threadsafe(api.get_rates_raw(), get_asyncio_loop())
        try:
            self._rates = fut.result(timeout=30)
            self.ratesReady.emit()
        except Exception as e:
            self.error.emit(str(e))

    @pyqtProperty('QVariant')
    def rates(self):
        return self._rates

    @pyqtSlot(str, str)
    def refresh_orderbook(self, base: str, rel: str):
        import asyncio as _asyncio
        from electrum.util import get_asyncio_loop
        api = self.plugin.get_api()
        fut = _asyncio.run_coroutine_threadsafe(api.get_orderbook(base, rel), get_asyncio_loop())
        try:
            ob = fut.result(timeout=30)
            self._book = api.parse_orderbook(ob)
            self.orderbookReady.emit()
        except Exception as e:
            self.error.emit(str(e))

    @pyqtProperty('QVariant')
    def orderbook(self):
        return self._book

    def _get_kdf(self, swap_endpoint, swap_userpass) -> KdfClient:
        """Shared client for buy + status polling (kept open across calls)."""
        k = self._kdf
        if k is None or k.url != (swap_endpoint or "").rstrip("/"):
            k = KdfClient(swap_endpoint or "", swap_userpass or "")
            self._kdf = k
        else:
            k.userpass = swap_userpass or ""
        return k

    def _get_kdf_cfg(self) -> KdfClient:
        cfg = self.plugin.config
        return self._get_kdf(cfg.get('plugins.elek_market.swap_endpoint'),
                             cfg.get('plugins.elek_market.swap_userpass'))

    def _post_buy_legacy(self, base: str, rel: str, volume: str, price: str):
        """Dispatch a kdf-native legacy `buy` to the user's own client-kdf
        (config: plugins.elek_market.swap_endpoint / swap_userpass) - the
        market itself stays 0-balance (M2 no-custody). Fallback: legacy
        market /rpc proxy. Returns the raw response dict."""
        import json as _json
        import aiohttp
        import asyncio as _asyncio
        from electrum.util import get_asyncio_loop

        loop = get_asyncio_loop()
        cfg = self.plugin.config
        swap_endpoint = cfg.get('plugins.elek_market.swap_endpoint')
        swap_userpass = cfg.get('plugins.elek_market.swap_userpass')
        if swap_endpoint:
            kdf = self._get_kdf(swap_endpoint, swap_userpass)
            fut = _asyncio.run_coroutine_threadsafe(
                kdf.buy_legacy(base, rel, price, volume), loop)
            return fut.result(timeout=200)
        payload = {"method": "buy", "base": base, "rel": rel,
                   "price": price, "volume": volume}
        url = self.plugin.endpoint.rstrip("/") + "/rpc"
        timeout = aiohttp.ClientTimeout(total=180)

        from electrum.plugins.elek_market.market_api import _verified_ssl

        async def _do():
            async with aiohttp.ClientSession(
                    timeout=timeout,
                    connector=aiohttp.TCPConnector(ssl=_verified_ssl())) as s:
                async with s.post(url, json=payload) as reply:
                    body = await reply.text()
                    if reply.status != 200:
                        raise MarketApiError(reply.status, body)
                    return _json.loads(body)

        fut = _asyncio.run_coroutine_threadsafe(_do(), loop)
        return fut.result(timeout=200)

    def _finish_buy(self, res):
        self._last_buy = res
        # Legacy buy carries the swap uuid somewhere below `result`
        # without a guaranteed top-level key; capture it for status polling.
        self._swap_uuid = KdfClient.find_uuid(res)
        if self._swap_uuid is None and isinstance(res, dict) \
                and res.get("error") is not None:
            self.error.emit(str(res["error"])[:200])
            return
        self.buyResult.emit()

    def _begin_taker(self, kind: str, base: str, rel: str,
                     volume: str, price: str):
        """#89 Taker-Retry bookkeeping: remember the placed order's view
        terms so check_status can cancel + re-issue it against a maker
        quote that crosses later (see retry_crossing)."""
        import time as _time
        self._taker = {"kind": kind, "args": (base, rel, volume, price)}
        self._taker_since = _time.time()
        self._retry_count = 0

    def _place_view_taker(self, kind: str, base: str, rel: str,
                          volume: str, price: str):
        """Placement shared by the QML slots (validated inputs) and the
        #89 retry path - applies the view->kdf-native mapping for 'bid'
        orders but touches NO retry bookkeeping (the retry path must not
        reset its counter)."""
        if kind == 'ask':
            res = self._post_buy_legacy(base, rel, volume, price)
        else:  # 'bid': kdf-native buy(rel, base), reciprocal mapping
            p, v = float(price), float(volume)
            res = self._post_buy_legacy(rel, base, str(v * p), str(1.0 / p))
        self._finish_buy(res)

    def _maybe_retry(self):
        """#89: the resting taker order coexists with a maker quote that
        crosses its terms only AFTER its arrival instant - kdf never
        matches two maker orders. Cancel + re-issue (fresh taker request,
        bounded by TAKER_RETRY_MAX) so the counterparty matches on
        arrival. Settle window: the kdf conversion itself happens at 30 s
        and a natural match can take a few seconds to propagate."""
        import asyncio as _asyncio
        import time as _time
        from electrum.util import get_asyncio_loop

        taker = self._taker
        if taker is None or self._retry_count >= TAKER_RETRY_MAX:
            return
        if _time.time() - self._taker_since < TAKER_RETRY_SETTLE_SECS:
            return
        api = self.plugin.get_api()
        base, rel, volume, price = taker["args"]
        fut = _asyncio.run_coroutine_threadsafe(
            api.get_orderbook(base, rel), get_asyncio_loop())
        try:
            book = api.parse_orderbook(fut.result(timeout=30))
            if not retry_crossing(book, taker["kind"], float(price)):
                return
        except Exception:
            return  # book unreadable - retry again on the next poll
        try:
            kdf = self._get_kdf_cfg()
            _asyncio.run_coroutine_threadsafe(
                kdf.cancel_order(self._swap_uuid), get_asyncio_loop()
            ).result(timeout=30)
        except Exception:
            return  # cannot cancel (e.g. being matched) - keep the order
        self._retry_count += 1
        self._taker_since = _time.time()
        self._place_view_taker(taker["kind"], *taker["args"])

    @pyqtSlot(str, str, str, str)
    def buy(self, base: str, rel: str, volume: str, price: str):
        """Taker that FILLS A BID of the view pair (instantly SELL `base`
        for `rel`). View contract: volume in `base`, price in `rel` per
        `base` (the book's bid terms). kdf-native it is buy(rel, base):
        the volume converts to the rel-proceeds (volume * price) and the
        kdf price to its reciprocal (base per rel). At 1:1 both are 1 -
        the historic e2e form ('1', '1') stays equivalent."""
        try:
            p = float(price)
            v = float(volume)
            if p <= 0 or v <= 0:
                raise ValueError('price and volume must be > 0')
        except ValueError as e:
            self.error.emit(str(e))
            return
        self._begin_taker('bid', base, rel, volume, price)
        self._place_view_taker('bid', base, rel, volume, price)

    @pyqtSlot(str, str, str, str)
    def buy_taker(self, base: str, rel: str, volume: str, price: str):
        """Taker that FILLS AN ASK of the view pair: buy `base` paying
        `rel`. NO pair mapping (view pair == native pair): volume in
        `base`, price = max `rel` per `base` accepted (the ask terms)."""
        try:
            if float(price) <= 0 or float(volume) <= 0:
                raise ValueError('price and volume must be > 0')
        except ValueError as e:
            self.error.emit(str(e))
            return
        self._begin_taker('ask', base, rel, volume, price)
        self._place_view_taker('ask', base, rel, volume, price)

    @pyqtSlot(str, str, str, str, str)
    def make_order(self, base: str, rel: str, side: str,
                   price: str, volume: str):
        """MAKER order in the view pair. side='ask': offer `base` for
        `rel` (a plain setprice(base, rel)). side='bid': bid for `base`
        paying `rel` - kdf-natively setprice(rel, base) with reciprocal
        price and the rel-proceeds as volume. cancel_previous=False so
        several orders per pair can coexist, each cancellable by uuid."""
        import asyncio as _asyncio
        from electrum.util import get_asyncio_loop

        try:
            p = float(price)
            v = float(volume)
            if p <= 0 or v <= 0:
                raise ValueError('price and volume must be > 0')
            if side == 'ask':
                k_base, k_rel = base, rel
                k_price, k_vol = str(p), str(v)
                k_min = str(v / 10.0)
            elif side == 'bid':
                proceeds = v * p
                k_base, k_rel = rel, base
                k_price, k_vol = str(1.0 / p), str(proceeds)
                k_min = str(proceeds / 10.0)
            else:
                raise ValueError(
                    f"side must be 'ask' or 'bid', got {side!r}")
        except (ValueError, ZeroDivisionError) as e:
            self.error.emit(str(e))
            return
        try:
            kdf = self._get_kdf_cfg()
            fut = _asyncio.run_coroutine_threadsafe(
                kdf.setprice(k_base, k_rel, k_price, k_vol,
                             min_volume=k_min), get_asyncio_loop())
            res = fut.result(timeout=60)
            maker = res.get("result") if isinstance(res, dict) else None
            uuid = maker.get("uuid") if isinstance(maker, dict) else None
            if not uuid:
                raise MarketApiError(-5, f"setprice: {str(res)[:200]}")
            self._last_maker = {"uuid": uuid, "base": base, "rel": rel,
                                "side": side, "price": price,
                                "volume": volume}
            self.makerOrderReady.emit()
            self.refresh_my_orders()
        except Exception as e:
            self.error.emit(str(e))

    @pyqtSlot(str)
    def cancel_order(self, uuid: str):
        import asyncio as _asyncio
        from electrum.util import get_asyncio_loop
        kdf = self._get_kdf_cfg()
        fut = _asyncio.run_coroutine_threadsafe(
            kdf.cancel_order(str(uuid)), get_asyncio_loop())
        try:
            fut.result(timeout=30)
            self.refresh_my_orders()
        except Exception as e:
            self.error.emit(str(e))

    @pyqtSlot()
    def refresh_my_orders(self):
        import asyncio as _asyncio
        from electrum.util import get_asyncio_loop
        kdf = self._get_kdf_cfg()
        fut = _asyncio.run_coroutine_threadsafe(
            kdf.my_orders_legacy(), get_asyncio_loop())
        try:
            self._my_orders = KdfClient.parse_my_orders(fut.result(timeout=30))
            self.myOrdersReady.emit()
        except Exception as e:
            self.error.emit(str(e))

    @pyqtProperty('QVariant')
    def myOrders(self):
        return self._my_orders

    @pyqtProperty('QVariant')
    def lastMakerOrder(self):
        return self._last_maker

    @pyqtProperty(bool)
    def swapConfigured(self):
        return bool(self.plugin.config.get('plugins.elek_market.swap_endpoint'))

    @pyqtProperty('QVariant')
    def lastBuy(self):
        return self._last_buy

    @pyqtProperty(str)
    def swapUuid(self):
        return self._swap_uuid or ""

    @pyqtSlot()
    def check_status(self):
        """Poll my_swap_status for the captured swap uuid and expose the
        parsed event list + state via swapStatus/swapStatusReady (B3 #5).

        #72 finding: a taker `buy` whose price cap finds no maker match is
        published as a RESTING ORDER - my_swap_status for its uuid answers
        mmrpc 2.0 error_type NoSwapWithUuid until a maker matches it (the
        order uuid only becomes a swap uuid at match). Classify that as
        "OrderWaitingForMatch" so the QML poll timer keeps running instead
        of surfacing an error."""
        if not self._swap_uuid:
            self.error.emit('no swap to check yet')
            return
        import asyncio as _asyncio
        from electrum.util import get_asyncio_loop

        cfg = self.plugin.config
        kdf = self._get_kdf(cfg.get('plugins.elek_market.swap_endpoint'),
                            cfg.get('plugins.elek_market.swap_userpass'))
        fut = _asyncio.run_coroutine_threadsafe(
            kdf.my_swap_status(self._swap_uuid), get_asyncio_loop())
        try:
            raw = fut.result(timeout=60)
        except MarketApiError as e:
            if 'NoSwapWithUuid' in str(getattr(e, "body", "")):
                self._swap_status = {"uuid": self._swap_uuid, "types": [],
                                     "state": "OrderWaitingForMatch",
                                     "retries": self._retry_count}
                self.swapStatusReady.emit()
                self._maybe_retry()
                return
            self.error.emit(str(e))
            return
        try:
            status = KdfClient.parse_swap_status(raw)
            status["uuid"] = self._swap_uuid
            self._swap_status = status
            self.swapStatusReady.emit()
        except Exception as e:
            self.error.emit(str(e))

    @pyqtProperty('QVariant')
    def swapStatus(self):
        return self._swap_status


class Plugin(ElekMarketBase):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._app = None
        self._so = None

    @hook
    def init_qml(self, app: 'ElectrumQmlApplication'):
        self._app = app
        # QML-binding singleton; parent into the QObject tree to keep alive
        self._so = QMarketSignalObject(self, app)
        self._so.setParent(app)
        self.so = self._so  # QEAppController.plugin() expects has-property 'so'
        app.engine.rootContext().setContextProperty('MarketPlugin', self._so)
        self.logger.info('MarketPlugin context property registered')
