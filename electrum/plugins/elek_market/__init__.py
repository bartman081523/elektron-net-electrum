#!/usr/bin/env python
# Elektron Electrum - Market-API plugin (base loader).
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
# #96: the Render REST layer (/fx/rates.json + /rpc orderbook) is a
# DIAGNOSTIC fallback only - the market truth is the kdf orderbook read from
# the user's own kdf (KdfClient.orderbook). Default "" (falsy = off).
SimpleConfig.ELEK_MARKET_DIAGNOSTIC_REST = ConfigVar(
    key="plugins.%s.diagnostic_rest" % plugin_name,
    default="", type_=str, plugin=plugin_name)


class Plugin(BasePlugin):

    def __init__(self, parent, config, name):
        super().__init__(parent, config, name)
        self._api: MarketApi | None = None

    @property
    def endpoint(self) -> str:
        # SimpleConfig ConfigVars are plain property descriptors:
        # instance access yields the str value directly (no .get()).
        return self.config.ELEK_MARKET_ENDPOINT or ""

    def set_endpoint(self, endpoint: str):
        self.config.ELEK_MARKET_ENDPOINT = endpoint.strip()
        if self._api is not None:
            self._api.set_endpoint(self.endpoint)

    def get_api(self) -> MarketApi:
        if self._api is None:
            self._api = MarketApi(self.endpoint or "http://127.0.0.1:10100")
        return self._api

    @property
    def diagnostic_rest(self) -> bool:
        """#96: opt-in flag for the Render REST fallback (raw string config
        parsed as truthy - never bool() the raw value, 'false' is truthy).
        Only this property may gate MarketApi usage in the QML bridge."""
        raw = self.config.ELEK_MARKET_DIAGNOSTIC_REST or ""
        return raw.strip().lower() in ("1", "true", "yes", "on")

    @property
    def pair(self) -> tuple:
        base = self.config.ELEK_MARKET_BASE
        rel = self.config.ELEK_MARKET_REL
        return base, rel

    def set_pair(self, base: str, rel: str):
        self.config.ELEK_MARKET_BASE = base
        self.config.ELEK_MARKET_REL = rel

    def is_available(self) -> bool:
        # network-independent display; errors are reported in the UI
        return True

    def requires_settings(self) -> bool:
        return True

    def settings_dialog(self, window):
        raise NotImplementedError("override in the GUI plugin part")
