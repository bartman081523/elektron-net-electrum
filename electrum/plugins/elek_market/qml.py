#!/usr/bin/env python
# Elektron Electrum – Market-API plugin (QML part for Android).
# Registers a QSignalObject under "MarketPlugin" context property so the new
# QML "Tausch" page can call market-API functions. Read-only orderbook/rates
# plus a buyer path to a user-owned client-kdf (no market-side custody).

from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal, pyqtSlot, pyqtProperty

from electrum.i18n import _
from electrum.plugin import hook
from electrum.gui.qml.qEObject import PluginQObject  # noqa: F401 (import guard)

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
        return self.plugin().endpoint

    @endpoint.setter
    def endpoint(self, value):
        self.plugin().set_endpoint(value)

    @pyqtSlot()
    def refresh_rates(self):
        import asyncio
        api = self.plugin().get_api()
        loop = asyncio.get_event_loop()
        from electrum.util import run_coroutine_threadsafe
        fut = run_coroutine_threadsafe(api.get_rates_raw(), loop)
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
        import asyncio
        api = self.plugin().get_api()
        from electrum.util import run_coroutine_threadsafe
        loop = asyncio.get_event_loop()
        fut = run_coroutine_threadsafe(api.get_orderbook(base, rel), loop)
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
        import asyncio, json as _json
        import aiohttp
        api = self.plugin().get_api()
        payload = {"method": "buy", "base": base, "rel": rel,
                   "price": price, "volume": volume}
        url = self.plugin().endpoint.rstrip("/") + "/rpc"
        timeout = aiohttp.ClientTimeout(total=180)

        async def _do():
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.post(url, json=payload) as reply:
                    body = await reply.text()
                    if reply.status != 200:
                        raise MarketApiError(reply.status, body)
                    return _json.loads(body)

        from electrum.util import run_coroutine_threadsafe
        fut = run_coroutine_threadsafe(_do(), asyncio.get_event_loop())
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
        self._so = QMarketSignalObject(self, app.engine)
        self._so.setParent(app.engine)
        app.engine.rootContext().setContextProperty('MarketPlugin', self._so)
