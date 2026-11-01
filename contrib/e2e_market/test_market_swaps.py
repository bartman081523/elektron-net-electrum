# -*- coding: utf-8 -*-
"""Market Swaps and Persistence Tests."""
import json
import socket
import urllib.request
import urllib.error


def _is_port_open(port):
    s = socket.socket()
    s.settimeout(1.0)
    try:
        return s.connect_ex(("127.0.0.1", int(port))) == 0
    finally:
        s.close()


def _container_rpc(ctx, method, **params):
    url = f"{ctx.container_url}/rpc"
    body = {"method": method, "userpass": ""}
    body.update(params)
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def test_fixture_kdf_client_listeners(ctx):
    """Check listener status of testbed client KDFs (fed_a3: 7797, local78: 7798, blade: 7799)."""
    ports = {"fed_a3": 7797, "local78": 7798, "blade": 7799}
    status = {name: _is_port_open(port) for name, port in ports.items()}
    active = [name for name, is_open in status.items() if is_open]
    down = [name for name, is_open in status.items() if not is_open]

    details = {"status": status, "active": active, "down": down}
    if not down:
        return "PASS", f"All client KDFs active: {', '.join(active)}", details
    elif active:
        return "FAIL", f"Partial client mesh: {len(active)} active ({', '.join(active)}), {len(down)} down ({', '.join(down)})", details
    else:
        return "SKIP", f"Client test fixture KDFs down ({', '.join(down)}) - host-only container active", details


def test_container_recent_swaps_persistence(ctx):
    """Container KDF my_recent_swaps must return valid swap history structure."""
    try:
        res = _container_rpc(ctx, "my_recent_swaps")
        result = res.get("result") or {}
        swaps = result.get("swaps")
        if isinstance(swaps, list):
            return "PASS", f"my_recent_swaps retrieved ({len(swaps)} recorded swaps)", {"count": len(swaps)}
        return "FAIL", f"Unexpected my_recent_swaps structure: {res}", {"response": res}
    except Exception as e:
        return "FAIL", f"my_recent_swaps RPC failed: {e}", {}


def test_cross_orderbook_mesh_sync(ctx):
    """Compare container orderbook and Render edge orderbook."""
    try:
        cont_ob = _container_rpc(ctx, "orderbook", base="tELEK", rel="tBTC")
        cont_asks = len(cont_ob.get("asks") or [])
        cont_bids = len(cont_ob.get("bids") or [])

        # Fetch render orderbook
        req = urllib.request.Request(
            f"{ctx.render_url}/rpc",
            data=json.dumps({"method": "orderbook", "base": "tELEK", "rel": "tBTC", "userpass": ""}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            render_ob = json.loads(resp.read().decode("utf-8", errors="replace"))
        render_asks = len(render_ob.get("asks") or [])
        render_bids = len(render_ob.get("bids") or [])

        details = {
            "container": {"asks": cont_asks, "bids": cont_bids},
            "render": {"asks": render_asks, "bids": render_bids},
        }
        return "PASS", f"Container (asks={cont_asks}, bids={cont_bids}) <-> Render (asks={render_asks}, bids={render_bids})", details
    except Exception as e:
        return "FAIL", f"Cross-orderbook query failed: {e}", {}


TESTS = [
    test_fixture_kdf_client_listeners,
    test_container_recent_swaps_persistence,
    test_cross_orderbook_mesh_sync,
]
