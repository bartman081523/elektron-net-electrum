#!/usr/bin/env python
# Elektron Electrum – Market-API plugin (QML part for Android).
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
        (config: plugins.elek_market.swap_endpoint / swap_userpass) — the
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

    @pyqtSlot(str, str, str, str)
    def buy(self, base: str, rel: str, volume: str, price: str):
        """Taker that FILLS A BID of the view pair (instantly SELL `base`
        for `rel`). View contract: volume in `base`, price in `rel` per
        `base` (the book's bid terms). kdf-native it is buy(rel, base):
        the volume converts to the rel-proceeds (volume * price) and the
        kdf price to its reciprocal (base per rel). At 1:1 both are 1 —
        the historic e2e form ('1', '1') stays equivalent."""
        try:
            p = float(price)
            v = float(volume)
            if p <= 0 or v <= 0:
                raise ValueError('price and volume must be > 0')
        except ValueError as e:
            self.error.emit(str(e))
            return
        base, rel = rel, base
        self._finish_buy(self._post_buy_legacy(
            base, rel, str(v * p), str(1.0 / p)))

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
        self._finish_buy(self._post_buy_legacy(base, rel, volume, price))

    @pyqtSlot(str, str, str, str, str)
    def make_order(self, base: str, rel: str, side: str,
                   price: str, volume: str):
        """MAKER order in the view pair. side='ask': offer `base` for
        `rel` (a plain setprice(base, rel)). side='bid': bid for `base`
        paying `rel` — kdf-natively setprice(rel, base) with reciprocal
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
        parsed event list + state via swapStatus/swapStatusReady (B3 #5)."""
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
            status = KdfClient.parse_swap_status(fut.result(timeout=60))
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
