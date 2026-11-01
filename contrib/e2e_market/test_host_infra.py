# -*- coding: utf-8 -*-
"""Host Testbed Infrastructure tests."""
import json
import socket
import urllib.request
import urllib.error


def _http_get(url, timeout=10):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def _http_post_json(url, payload, timeout=15):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8", errors="replace"))


def test_container_healthz(ctx):
    """Container /healthz must return 200 OK."""
    url = f"{ctx.container_url}/healthz"
    try:
        status, body = _http_get(url)
        if status == 200 and "ok" in body.lower():
            return "PASS", f"200 OK ({body.strip()})", {"status": status}
        return "FAIL", f"Unexpected status {status}: {body[:100]}", {"status": status}
    except Exception as e:
        return "FAIL", f"Cannot reach container healthz: {e}", {}


def test_electrs_testnet_banner_and_height(ctx):
    """Local electrs port (50005) must respond to Electrum RPC and report chain height."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5.0)
    try:
        s.connect(("127.0.0.1", ctx.electrs_port))
        # 1. server.version
        req1 = json.dumps({"id": 1, "method": "server.version", "params": ["e2e_suite", ["1.4", "1.5"]]}) + "\n"
        s.sendall(req1.encode("utf-8"))
        f = s.makefile("r", encoding="utf-8")
        resp1 = json.loads(f.readline())
        server_ver = resp1.get("result")

        # 2. blockchain.headers.subscribe
        req2 = json.dumps({"id": 2, "method": "blockchain.headers.subscribe", "params": []}) + "\n"
        s.sendall(req2.encode("utf-8"))
        resp2 = json.loads(f.readline())
        header = resp2.get("result") or {}
        height = header.get("height")

        if height is not None and height > 0:
            return "PASS", f"Electrs tip height={height}, version={server_ver}", {"height": height, "version": server_ver}
        return "FAIL", f"Invalid header subscription: {resp2}", {"response": resp2}
    except Exception as e:
        return "FAIL", f"Electrs connection failed on 127.0.0.1:{ctx.electrs_port}: {e}", {}
    finally:
        s.close()


def test_electrs_lan_port_50002(ctx):
    """LAN electrs relay port (50002) must respond to Electrum RPC without firewall blocking."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5.0)
    try:
        s.connect(("127.0.0.1", 50002))
        req = json.dumps({"id": 1, "method": "server.version", "params": ["e2e_lan", ["1.4", "1.5"]]}) + "\n"
        s.sendall(req.encode("utf-8"))
        f = s.makefile("r", encoding="utf-8")
        resp = json.loads(f.readline())
        server_ver = resp.get("result")
        if server_ver:
            return "PASS", f"LAN port 50002 listening and active ({server_ver})", {"version": server_ver}
        return "FAIL", f"Unexpected response from port 50002: {resp}", {"response": resp}
    except Exception as e:
        return "FAIL", f"Port 50002 relay not reachable: {e}", {}
    finally:
        s.close()


def test_container_kdf_rpc_version(ctx):
    """Container KDF RPC via /rpc proxy must return version 3.0.0-beta_..."""
    url = f"{ctx.container_url}/rpc"
    try:
        status, data = _http_post_json(url, {"method": "version", "userpass": ""})
        ver = data.get("result")
        if status == 200 and ver and "3.0.0" in ver:
            return "PASS", f"kdf version {ver}", {"version": ver}
        return "FAIL", f"Unexpected kdf version response: {data}", {"response": data}
    except Exception as e:
        return "FAIL", f"Container KDF version RPC failed: {e}", {}


def test_container_kdf_enabled_coins(ctx):
    """Container KDF must have tBTC and tELEK enabled."""
    url = f"{ctx.container_url}/rpc"
    try:
        status, data = _http_post_json(url, {"method": "get_enabled_coins", "userpass": ""})
        coins_list = data.get("result", [])
        tickers = {c.get("ticker") for c in coins_list if isinstance(c, dict)}
        missing = {"tBTC", "tELEK"} - tickers
        if not missing:
            return "PASS", f"Enabled coins: {sorted(list(tickers))}", {"tickers": sorted(list(tickers))}
        return "FAIL", f"Missing required coins in {tickers}: {missing}", {"tickers": list(tickers)}
    except Exception as e:
        return "FAIL", f"Container get_enabled_coins failed: {e}", {}


def test_container_kdf_orderbook_shape(ctx):
    """Container KDF orderbook for tELEK/tBTC must respond on netid 8888."""
    url = f"{ctx.container_url}/rpc"
    try:
        status, data = _http_post_json(url, {"method": "orderbook", "base": "tELEK", "rel": "tBTC", "userpass": ""})
        netid = data.get("netid")
        base = data.get("base")
        rel = data.get("rel")
        asks = data.get("asks")
        bids = data.get("bids")
        if netid == 8888 and base == "tELEK" and rel == "tBTC" and isinstance(asks, list) and isinstance(bids, list):
            return "PASS", f"netid={netid} asks={len(asks)} bids={len(bids)}", {"asks": len(asks), "bids": len(bids)}
        return "FAIL", f"Invalid orderbook structure: {data}", {"response": data}
    except Exception as e:
        return "FAIL", f"Container orderbook RPC failed: {e}", {}


TESTS = [
    test_container_healthz,
    test_electrs_testnet_banner_and_height,
    test_electrs_lan_port_50002,
    test_container_kdf_rpc_version,
    test_container_kdf_enabled_coins,
    test_container_kdf_orderbook_shape,
]
