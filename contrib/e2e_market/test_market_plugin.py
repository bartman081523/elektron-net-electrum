# -*- coding: utf-8 -*-
"""Unit and regression tests for elek_market plugin components."""
import unittest
import sys
import os

# Ensure electrum package is in sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from electrum import constants
from electrum.plugins.elek_market import market_api
from electrum.plugins.elek_market import kdf_runtime


class TestMarketApi(unittest.TestCase):
    """Test market_api dual-network resolution, servers, and rate helpers."""

    def test_tradeable_coins_and_pairs(self):
        # Default testnet
        coins = market_api.tradeable_coins()
        pairs = market_api.builtin_pairs()
        if market_api.is_testnet():
            self.assertEqual(coins, ("tELEK", "tBTC", "SEPOLIAETH"))
            self.assertEqual(pairs, (("tELEK", "tBTC"), ("tELEK", "SEPOLIAETH"), ("tBTC", "SEPOLIAETH")))
        else:
            self.assertEqual(coins, ("ELEK", "BTC", "ETH"))
            self.assertEqual(pairs, (("ELEK", "BTC"), ("ELEK", "ETH"), ("BTC", "ETH")))

    def test_electrum_servers_resolution(self):
        # Mainnet ELEK and BTC servers must be populated
        self.assertTrue(len(market_api.ELEK_ELECTRUM_SERVERS) >= 2)
        self.assertIn("electrs.elektron-net.org:50002", market_api.ELEK_ELECTRUM_SERVERS)
        self.assertTrue(len(market_api.BTC_ELECTRUM_SERVERS) >= 2)
        self.assertIn("btc.electrum1.cipig.net:10000", market_api.BTC_ELECTRUM_SERVERS)

        # Helper returns expected servers as TCP dicts
        elek_srv = market_api.electrum_servers("ELEK")
        self.assertEqual(len(elek_srv), len(market_api.ELEK_ELECTRUM_SERVERS))
        self.assertEqual(elek_srv[0]["url"], market_api.ELEK_ELECTRUM_SERVERS[0])
        self.assertEqual(elek_srv[0]["protocol"], "TCP")

        btc_srv = market_api.electrum_servers("BTC")
        self.assertEqual(len(btc_srv), len(market_api.BTC_ELECTRUM_SERVERS))
        self.assertEqual(btc_srv[0]["url"], market_api.BTC_ELECTRUM_SERVERS[0])

    def test_default_price_for(self):
        rates = {"usd": 0.25, "usd_per_btc": 77783.0}
        # tELEK / tBTC: 0.25 / 77783.0 ≈ 0.0000032139
        p = market_api.default_price_for(rates, "tELEK", "tBTC")
        self.assertIsNotNone(p)
        self.assertTrue(p.startswith("0.00000321"))

        # Exact pair mid from live book takes priority
        rates_with_mid = {"pair": "tELEK/tBTC", "mid": "0.000005"}
        self.assertEqual(market_api.default_price_for(rates_with_mid, "tELEK", "tBTC"), "0.000005")

        # Unknown coin returns empty string (no fabricated rate)
        self.assertEqual(market_api.default_price_for(rates, "UNKNOWN", "tBTC"), "")

    def test_parse_my_orders_cancellable(self):
        # KDF order representation without explicit cancellable field
        kdf_order = {
            "uuid": "test-uuid-1234",
            "base": "tELEK",
            "rel": "tBTC",
            "price": "0.00000321",
            "max_base_vol": "40.0",
            "started_swaps": [],
        }
        raw_my_orders = {"result": {"maker_orders": {kdf_order["uuid"]: kdf_order}}}
        parsed = market_api.KdfClient.parse_my_orders(raw_my_orders)
        self.assertEqual(len(parsed["maker_orders"]), 1)
        item = parsed["maker_orders"][0]
        self.assertEqual(item["uuid"], "test-uuid-1234")
        self.assertEqual(item["base"], "tELEK")
        self.assertEqual(item["price"], "0.00000321")
        # Must be cancellable!
        self.assertTrue(item["cancellable"])


class TestKdfRuntime(unittest.TestCase):
    """Test kdf_runtime configuration and server override serialization."""

    def test_server_overrides_parsing_and_encoding(self):
        raw = "ELEK=100.64.0.2:50002;BTC=btc.electrum1.cipig.net:10000,btc.electrum2.cipig.net:10000"
        parsed = kdf_runtime.parse_server_overrides(raw)
        self.assertEqual(parsed["ELEK"], ["100.64.0.2:50002"])
        self.assertEqual(parsed["BTC"], ["btc.electrum1.cipig.net:10000", "btc.electrum2.cipig.net:10000"])

        # Re-encode from dict with lists
        encoded = kdf_runtime.encode_server_overrides(parsed)
        self.assertEqual(encoded, raw)

        # Encode from dict with comma-separated strings (as passed from QML text fields)
        from_strings = {"ELEK": "100.64.0.2:50002", "BTC": "btc.electrum1.cipig.net:10000,btc.electrum2.cipig.net:10000"}
        encoded2 = kdf_runtime.encode_server_overrides(from_strings)
        self.assertEqual(encoded2, raw)

    def test_base_conf_structure(self):
        conf = kdf_runtime._base_conf()
        self.assertEqual(conf["gui"], "elektron-net")
        self.assertEqual(conf["netid"], 8888)
        self.assertIn("wss://elektron-market.onrender.com", conf["seednodes"])
        coins = [c["coin"] for c in conf.get("coins", [])]
        if market_api.is_testnet():
            self.assertIn("tELEK", coins)
            self.assertIn("tBTC", coins)
            self.assertIn("SEPOLIAETH", coins)
        else:
            self.assertIn("ELEK", coins)
            self.assertIn("BTC", coins)
            self.assertIn("ETH", coins)

    def test_sync_settings_with_seednodes(self):
        conf = kdf_runtime._base_conf()
        custom_seed = "wss://custom-seed.example.com"
        synced = kdf_runtime._sync_settings(conf, custom_seed, {})
        self.assertEqual(synced["seednodes"], [custom_seed])

        # Empty seednode restores default
        synced_default = kdf_runtime._sync_settings(conf, "", {})
        self.assertEqual(synced_default["seednodes"], ["wss://elektron-market.onrender.com"])


def test_tradeable_coins_and_pairs(ctx):
    """Testnet and mainnet coins and pairs resolve according to network flag."""
    coins = market_api.tradeable_coins()
    pairs = market_api.builtin_pairs()
    if market_api.is_testnet():
        assert coins == ("tELEK", "tBTC", "SEPOLIAETH")
        assert pairs == (("tELEK", "tBTC"), ("tELEK", "SEPOLIAETH"), ("tBTC", "SEPOLIAETH"))
    else:
        assert coins == ("ELEK", "BTC", "ETH")
        assert pairs == (("ELEK", "BTC"), ("ELEK", "ETH"), ("BTC", "ETH"))
    return "PASS", f"Coins={coins}, Pairs={len(pairs)}", {"coins": coins, "pairs": pairs}


def test_electrum_servers_resolution(ctx):
    """ELEK, BTC, tELEK, and tBTC resolve valid Electrum servers."""
    elek_srv = market_api.electrum_servers("ELEK")
    btc_srv = market_api.electrum_servers("BTC")
    assert len(elek_srv) >= 2, "ELEK servers must have at least 2 entries"
    assert len(btc_srv) >= 2, "BTC servers must have at least 2 entries"
    assert elek_srv[0]["url"] == "electrs.elektron-net.org:50002"
    assert btc_srv[0]["url"] == "btc.electrum1.cipig.net:10000"
    return "PASS", f"ELEK: {elek_srv[0]['url']}, BTC: {btc_srv[0]['url']}", {"elek": elek_srv, "btc": btc_srv}


def test_default_price_for(ctx):
    """Price recommendation derives mid from book or fallback registry formula."""
    rates = {"usd": 0.25, "usd_per_btc": 77783.0}
    p = market_api.default_price_for(rates, "tELEK", "tBTC")
    assert p and p.startswith("0.00000321"), f"Unexpected price {p}"

    rates_mid = {"pair": "tELEK/tBTC", "mid": "0.000005"}
    p_mid = market_api.default_price_for(rates_mid, "tELEK", "tBTC")
    assert p_mid == "0.000005", f"Expected exact mid, got {p_mid}"

    p_unrec = market_api.default_price_for(rates, "UNKNOWN", "tBTC")
    assert p_unrec == "", f"Expected empty for unknown coin, got {p_unrec}"
    return "PASS", f"Formula mid={p}, Book mid={p_mid}", {"calc": p, "book": p_mid}


def test_server_overrides_parsing_and_encoding(ctx):
    """Server override parser and encoder are symmetric and handle strings cleanly."""
    raw = "ELEK=100.64.0.2:50002;BTC=btc.electrum1.cipig.net:10000,btc.electrum2.cipig.net:10000"
    parsed = kdf_runtime.parse_server_overrides(raw)
    encoded = kdf_runtime.encode_server_overrides(parsed)
    assert encoded == raw, f"Mismatch: {encoded} != {raw}"

    from_strings = {"ELEK": "100.64.0.2:50002", "BTC": "btc.electrum1.cipig.net:10000,btc.electrum2.cipig.net:10000"}
    encoded2 = kdf_runtime.encode_server_overrides(from_strings)
    assert encoded2 == raw, f"String encoding mismatch: {encoded2} != {raw}"
    return "PASS", "Override encode/decode symmetric (including string text fields)", {"parsed": parsed}


def test_base_conf_structure(ctx):
    """Base KDF conf generates proper netid, seednode, and dynamic coin entries."""
    conf = kdf_runtime._base_conf()
    assert conf["gui"] == "elektron-net"
    assert conf["netid"] == 8888
    assert "wss://elektron-market.onrender.com" in conf["seednodes"]
    coins = [c["coin"] for c in conf.get("coins", [])]
    return "PASS", f"netid=8888, seednode=Render, coins={coins}", {"coins": coins}


def test_parse_my_orders_cancellable(ctx):
    """Maker orders parsed from KDF always receive cancellable=True."""
    kdf_order = {
        "uuid": "test-uuid-1234",
        "base": "tELEK",
        "rel": "tBTC",
        "price": "0.00000321",
        "max_base_vol": "40.0",
        "started_swaps": [],
    }
    raw = {"result": {"maker_orders": {kdf_order["uuid"]: kdf_order}}}
    parsed = market_api.KdfClient.parse_my_orders(raw)
    assert len(parsed["maker_orders"]) == 1
    assert parsed["maker_orders"][0]["cancellable"] is True
    return "PASS", "Maker orders correctly flagged cancellable=True", parsed


TESTS = [
    test_tradeable_coins_and_pairs,
    test_electrum_servers_resolution,
    test_default_price_for,
    test_server_overrides_parsing_and_encoding,
    test_base_conf_structure,
    test_parse_my_orders_cancellable,
]


if __name__ == "__main__":
    unittest.main()
