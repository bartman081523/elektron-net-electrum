#!/usr/bin/env python
# Elektron Electrum – Market-API plugin (base loader).
# See market_api.py for the custody rules; base plugins are read-only.

from typing import TYPE_CHECKING

from electrum.plugin import BasePlugin
from electrum.simple_config import SimpleConfig, ConfigVar

from .market_api import MarketApi, MarketApiError  # noqa: F401

if TYPE_CHECKING:
    from ..gui.qt import ElectrumWindow

plugin_name = "elek_market"


def _endpoint_config():
    return ConfigVar(key="plugins.%s.endpoint" % plugin_name,
                     default="", type_=str, plugin=plugin_name)


SimpleConfig.ELEK_MARKET_ENDPOINT = _endpoint_config()
SimpleConfig.ELEK_MARKET_BASE = ConfigVar(
    key="plugins.%s.base" % plugin_name,
    default="tELEK", type_=str, plugin=plugin_name)
SimpleConfig.ELEK_MARKET_REL = ConfigVar(
    key="plugins.%s.rel" % plugin_name,
    default="tBTC", type_=str, plugin=plugin_name)


class Plugin(BasePlugin):

    def __init__(self, parent, config, name):
        super().__init__(parent, config, name)
        self._api: MarketApi | None = None

    @property
    def endpoint(self) -> str:
        return self.config.ELEK_MARKET_ENDPOINT.get()

    def set_endpoint(self, endpoint: str):
        self.config.ELEK_MARKET_ENDPOINT.set(endpoint.strip(), save=True)
        if self._api is not None:
            self._api.set_endpoint(self.endpoint)

    def get_api(self) -> MarketApi:
        if self._api is None:
            self._api = MarketApi(self.endpoint or "http://127.0.0.1:10100")
        return self._api

    @property
    def pair(self) -> tuple:
        base = self.config.ELEK_MARKET_BASE.get()
        rel = self.config.ELEK_MARKET_REL.get()
        return base, rel

    def set_pair(self, base: str, rel: str):
        self.config.ELEK_MARKET_BASE.set(base, save=True)
        self.config.ELEK_MARKET_REL.set(rel, save=True)

    def is_available(self) -> bool:
        # network-independent display; errors are reported in the UI
        return True

    def requires_settings(self) -> bool:
        return True

    def settings_dialog(self, window):
        raise NotImplementedError("override in the GUI plugin part")
