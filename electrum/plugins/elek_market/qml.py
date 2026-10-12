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
        MarketPlugin.buy(base, rel, volume, price)
        MarketPlugin.check_status()
        MarketPlugin.swapUuid / MarketPlugin.swapStatus
    """

    ratesReady = pyqtSignal()
    orderbookReady = pyqtSignal()
    buyResult = pyqtSignal()
    swapStatusReady = pyqtSignal()
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

    @pyqtSlot(str, str, str, str)
    def buy(self, base: str, rel: str, volume: str, price: str):
        """Taker-buy. Primary path: legacy `buy` on the user's own client-kdf
        (config: plugins.elek_market.swap_endpoint / swap_userpass) — the
        market itself stays 0-balance (M2 no-custody). Fallback: legacy
        market /rpc proxy."""
        import json as _json
        import aiohttp
        import asyncio as _asyncio
        from electrum.util import get_asyncio_loop

        cfg = self.plugin.config
        swap_endpoint = cfg.get('plugins.elek_market.swap_endpoint')
        swap_userpass = cfg.get('plugins.elek_market.swap_userpass')

        # The QML pair is labelled (base=tELEK, rel=tBTC) as a user-market view.
        # A taker BUY in kdf legacy semantics buys `base` paying `rel`. The user
        # wants to trade tELEK -> tBTC, i.e. buy(base=tBTC, rel=tELEK).
        if base == 'tELEK' and rel == 'tBTC':
            base, rel = rel, base

        loop = get_asyncio_loop()
        if swap_endpoint:
            kdf = self._get_kdf(swap_endpoint, swap_userpass)
            fut = _asyncio.run_coroutine_threadsafe(
                kdf.buy_legacy(base, rel, price, volume), loop)
        else:
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
        try:
            self._last_buy = fut.result(timeout=200)
            # Legacy buy carries the swap uuid somewhere below `result`
            # without a guaranteed top-level key; capture it for status polling.
            self._swap_uuid = KdfClient.find_uuid(self._last_buy)
            if self._swap_uuid is None and isinstance(self._last_buy, dict) \
                    and self._last_buy.get("error") is not None:
                self.error.emit(str(self._last_buy["error"])[:200])
                return
            self.buyResult.emit()
        except Exception as e:
            self.error.emit(str(e))

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
