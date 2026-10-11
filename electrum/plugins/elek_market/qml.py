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
from .market_api import MarketApi, MarketApiError

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
    """

    ratesReady = pyqtSignal()
    orderbookReady = pyqtSignal()
    buyResult = pyqtSignal()
    error = pyqtSignal(str)

    _name = _('Elektron Market')

    def __init__(self, plugin, parent):
        super().__init__(plugin, parent)
        self._rates = None
        self._book = None
        self._last_buy = None
        self._last_error = None

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

    @pyqtSlot(str, str, str, str)
    def buy(self, base: str, rel: str, volume: str, price: str):
        """Taker-buy. Primary path: POST to the user's own client-kdf
        (config: plugins.elek_market.swap_endpoint / swap_userpass) — the
        market itself stays 0-balance (M2 no-custody). Fallback: legacy
        market /rpc proxy."""
        import json as _json
        import aiohttp

        cfg = self.plugin.config
        swap_endpoint = cfg.get('plugins.elek_market.swap_endpoint')
        swap_userpass = cfg.get('plugins.elek_market.swap_userpass')

        if swap_endpoint:
            payload = {"userpass": swap_userpass or "",
                       "method": "buy", "base": base, "rel": rel,
                       "price": price, "volume": volume}
            url = swap_endpoint.rstrip("/")
        else:
            payload = {"method": "buy", "base": base, "rel": rel,
                       "price": price, "volume": volume}
            url = self.plugin.endpoint.rstrip("/") + "/rpc"
        timeout = aiohttp.ClientTimeout(total=180)

        async def _do():
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.post(url, json=payload) as reply:
                    body = await reply.text()
                    if reply.status != 200:
                        raise MarketApiError(reply.status, body)
                    return _json.loads(body)

        import asyncio as _asyncio
        from electrum.util import get_asyncio_loop
        fut = _asyncio.run_coroutine_threadsafe(_do(), get_asyncio_loop())
        try:
            self._last_buy = fut.result(timeout=200)
            self.buyResult.emit()
        except Exception as e:
            self.error.emit(str(e))

    @pyqtProperty('QVariant')
    def lastBuy(self):
        return self._last_buy


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
        self.logger.info('MarketPlugin context property registered')
