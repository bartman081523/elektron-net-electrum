# -*- coding: utf-8 -*-
"""Render Edge and P2P Federation Tests."""
import json
import urllib.request
import urllib.error


def _http_get(url, timeout=20):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def _http_post_raw(url, payload, timeout=20):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def test_render_healthz(ctx):
    """Render edge /healthz must return 200 OK."""
    url = f"{ctx.render_url}/healthz"
    try:
        status, body = _http_get(url)
        if status == 200 and "ok" in body.lower():
            return "PASS", f"200 OK ({body.strip()})", {"status": status}
        return "FAIL", f"Unexpected status {status}: {body[:100]}", {"status": status}
    except Exception as e:
        return "FAIL", f"Render /healthz failed: {e}", {}


def test_render_orderbook(ctx):
    """Render market must return orderbook for tELEK/tBTC on netid 8888."""
    url = f"{ctx.render_url}/rpc"
    payload = {"method": "orderbook", "base": "tELEK", "rel": "tBTC", "userpass": ""}
    try:
        status, body = _http_post_raw(url, payload)
        if status != 200:
            return "FAIL", f"HTTP {status}: {body[:120]}", {"status": status}
        data = json.loads(body)
        netid = data.get("netid")
        base = data.get("base")
        rel = data.get("rel")
        if netid == 8888 and base == "tELEK" and rel == "tBTC":
            asks = len(data.get("asks") or [])
            bids = len(data.get("bids") or [])
            return "PASS", f"netid={netid} asks={asks} bids={bids}", {"asks": asks, "bids": bids}
        return "FAIL", f"Invalid orderbook structure: {data}", {"response": data}
    except Exception as e:
        return "FAIL", f"Render orderbook query failed: {e}", {}


def test_render_api_only_403_mutation_guard(ctx):
    """Render must block mutation RPCs with 403 Forbidden (#71 API-only mandate)."""
    url = f"{ctx.render_url}/rpc"
    mutating_methods = ["buy", "sell", "setprice", "withdraw", "stop"]
    results = {}
    failures = []

    for method in mutating_methods:
        payload = {"method": method, "userpass": ""}
        try:
            status, body = _http_post_raw(url, payload)
            results[method] = status
            if status != 403:
                failures.append(f"{method}->HTTP {status} (expected 403)")
        except Exception as e:
            failures.append(f"{method}->Error {e}")

    if not failures:
        return "PASS", f"All {len(mutating_methods)} mutations blocked with 403 Forbidden", results
    return "FAIL", f"Security violation - unblocked mutations: {', '.join(failures)}", results


def test_render_read_only_allowed_methods(ctx):
    """Render allows non-mutating query methods."""
    url = f"{ctx.render_url}/rpc"
    allowed_methods = ["version", "get_enabled_coins"]
    results = {}
    failures = []

    for method in allowed_methods:
        payload = {"method": method, "userpass": ""}
        try:
            status, body = _http_post_raw(url, payload)
            results[method] = status
            if status != 200:
                failures.append(f"{method}->HTTP {status}")
        except Exception as e:
            failures.append(f"{method}->Error {e}")

    if not failures:
        return "PASS", f"Allowed read methods return 200 OK", results
    return "FAIL", f"Read-only methods failed: {', '.join(failures)}", results


TESTS = [
    test_render_healthz,
    test_render_orderbook,
    test_render_api_only_403_mutation_guard,
    test_render_read_only_allowed_methods,
]
