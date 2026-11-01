# -*- coding: utf-8 -*-
"""Device and Android App E2E Tests."""
import json
import os
import subprocess
import time
import urllib.request
import urllib.error


def _adb_cmd(ctx, args, timeout=15):
    cmd = ["adb", "-s", ctx.device_addr] + args
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _adb_run_as(ctx, args, timeout=15):
    return _adb_cmd(ctx, ["shell", "run-as", "org.elektronnet.market.elektronelectrum"] + args, timeout=timeout)


def _device_rpc(ctx, method, **params):
    if not ctx.device_conf or "rpc_password" not in ctx.device_conf:
        raise RuntimeError("Device conf not loaded")
    body = {
        "userpass": ctx.device_conf["rpc_password"],
        "method": method,
        "id": 0,
    }
    body.update(params)
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{ctx.device_forward_port}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(err_body)
        except Exception:
            return {"HTTPError": e.code, "body": err_body}


def test_device_adb_reachability(ctx):
    """Device must be reachable via ADB over IP."""
    try:
        r = _adb_cmd(ctx, ["shell", "echo ping"])
        if r.returncode == 0 and "ping" in r.stdout:
            ctx.device_online = True
            return "PASS", f"Device reachable at {ctx.device_addr}", {}
        ctx.device_online = False
        return "FAIL", f"Device not responding: {r.stderr.strip()}", {"stdout": r.stdout, "stderr": r.stderr}
    except Exception as e:
        ctx.device_online = False
        return "FAIL", f"ADB connection failed: {e}", {}


def test_device_package_installed(ctx):
    """App package org.elektronnet.market.elektronelectrum must be installed."""
    if not ctx.device_online:
        return "SKIP", "Device offline", {}
    try:
        r = _adb_cmd(ctx, ["shell", "dumpsys package org.elektronnet.market.elektronelectrum | grep -E 'versionName|lastUpdateTime'"])
        lines = [line.strip() for line in r.stdout.splitlines() if line.strip()]
        if lines:
            return "PASS", " | ".join(lines), {"dumpsys": lines}
        return "FAIL", "Package not found in dumpsys", {}
    except Exception as e:
        return "FAIL", f"Error checking package: {e}", {}


def test_device_running_processes(ctx):
    """App process and libkdfbin.so must be running."""
    if not ctx.device_online:
        return "SKIP", "Device offline", {}
    try:
        r = _adb_cmd(ctx, ["shell", "ps -A | grep -E 'org.elektronnet.market|libkdfbin.so'"])
        lines = [line.strip() for line in r.stdout.splitlines() if line.strip()]
        has_app = any("org.elektronnet.market.elektronelectrum" in l for l in lines)
        has_kdf = any("libkdfbin.so" in l for l in lines)
        if has_app and has_kdf:
            return "PASS", f"App (PID) and libkdfbin.so running", {"processes": lines}
        elif has_app:
            return "FAIL", "App is running, but libkdfbin.so runtime process is missing", {"processes": lines}
        return "FAIL", "App process is not running", {"processes": lines}
    except Exception as e:
        return "FAIL", f"Error checking processes: {e}", {}


def test_device_custody_passphrase_prefix(ctx):
    """Device MM2.json passphrase must start with elkkdf1 (wallet-seed custody #98)."""
    if not ctx.device_online:
        return "SKIP", "Device offline", {}
    try:
        r = _adb_run_as(ctx, ["cat", "files/data/testnet/kdf/MM2.json"])
        if r.returncode != 0:
            return "FAIL", f"Cannot read MM2.json: {r.stderr.strip()}", {}
        conf = json.loads(r.stdout)
        ctx.device_conf = conf
        passphrase = conf.get("passphrase", "")
        if passphrase.startswith("elkkdf1"):
            return "PASS", f"Custody prefix elkkdf1 verified (len={len(passphrase)})", {"prefix": "elkkdf1"}
        return "FAIL", f"Invalid custody prefix: {passphrase[:10]}... (expected elkkdf1)", {}
    except Exception as e:
        return "FAIL", f"Failed reading MM2.json: {e}", {}


def test_device_runtime_rpc_version(ctx):
    """Device embedded runtime must respond to RPC version query via forwarded port."""
    if not ctx.device_online or not ctx.device_conf:
        return "SKIP", "Prerequisites not met (device offline or conf missing)", {}
    try:
        # Ensure forward is active
        _adb_cmd(ctx, ["forward", f"tcp:{ctx.device_forward_port}", "tcp:7821"])
        resp = _device_rpc(ctx, "version")
        ver = resp.get("result")
        if ver and "3.0.0" in ver:
            return "PASS", f"Device kdf version: {ver}", {"version": ver}
        return "FAIL", f"Unexpected version response: {resp}", {"response": resp}
    except Exception as e:
        return "FAIL", f"Runtime RPC version call failed: {e}", {}


def test_device_enable_testnet_coins(ctx):
    """Enable tELEK and tBTC on device via LAN electrs (192.168.178.21:50002 without ADB reverse)."""
    if not ctx.device_online or not ctx.device_conf:
        return "SKIP", "Prerequisites not met", {}
    try:
        results = {}
        failures = []

        lan_server = os.environ.get("ELECTRS_LAN_ADDR", "192.168.178.21:50002")
        for coin in ["tELEK", "tBTC"]:
            out = _device_rpc(ctx, "electrum", coin=coin,
                              servers=[{"url": lan_server, "protocol": "TCP"}])
            res = out.get("result")
            err_str = str(out.get("error", ""))
            ok = (res in ("success", True) or
                  "already initialized" in err_str.lower() or
                  "coinisalreadyenabled" in err_str.lower())
            results[coin] = {"result": res, "error": err_str, "ok": ok}
            if not ok:
                failures.append(f"{coin}: {err_str}")

        if not failures:
            return "PASS", f"tELEK and tBTC enabled via LAN {lan_server} (no ADB reverse)", results
        return "FAIL", f"Coin activation failed: {', '.join(failures)}", results
    except Exception as e:
        return "FAIL", f"Enable coins RPC failed: {e}", {}


def test_device_balances(ctx):
    """Check device balances for tELEK and tBTC."""
    if not ctx.device_online or not ctx.device_conf:
        return "SKIP", "Prerequisites not met", {}
    try:
        balances = {}
        for coin in ["tELEK", "tBTC"]:
            b = _device_rpc(ctx, "my_balance", coin=coin)
            balances[coin] = {
                "balance": b.get("balance", "0"),
                "address": b.get("address", ""),
            }

        summary = ", ".join(f"{c}={balances[c]['balance']}" for c in balances)
        addr = balances.get("tELEK", {}).get("address", "")
        return "PASS", f"Balances: {summary} (addr {addr[:12]}...)", balances
    except Exception as e:
        return "FAIL", f"my_balance RPC failed: {e}", {}


def test_device_orderbook_query(ctx):
    """Query orderbook tELEK/tBTC via device runtime."""
    if not ctx.device_online or not ctx.device_conf:
        return "SKIP", "Prerequisites not met", {}
    try:
        ob = _device_rpc(ctx, "orderbook", base="tELEK", rel="tBTC")
        asks = len(ob.get("asks") or [])
        bids = len(ob.get("bids") or [])
        netid = ob.get("netid")
        return "PASS", f"netid={netid} asks={asks} bids={bids}", {"asks": asks, "bids": bids}
    except Exception as e:
        return "FAIL", f"Device orderbook RPC failed: {e}", {}


def test_device_maker_order_validation(ctx):
    """Check maker order placement on device - reports honest failure if unfunded."""
    if not ctx.device_online or not ctx.device_conf:
        return "SKIP", "Prerequisites not met", {}
    try:
        # Attempt to set a maker order: base=tELEK, rel=tBTC, volume=0.01, price=2.0
        out = _device_rpc(ctx, "setprice", base="tELEK", rel="tBTC", price="2.0", volume="0.01")
        if "result" in out:
            uuid = out["result"].get("uuid")
            # If successful, cancel it immediately
            _device_rpc(ctx, "cancel_order", uuid=uuid)
            return "PASS", f"Order placed and cancelled (uuid {uuid})", out
        err = str(out.get("error", out.get("body", str(out))))
        # Honest reporting: If balance is 0, KDF rejects the order
        return "FAIL", f"Maker order rejected (honest defekt): {err[:120]}", out
    except Exception as e:
        return "FAIL", f"setprice RPC exception: {e}", {}


def test_device_logcat_error_audit(ctx):
    """Audit recent logcat for fatal exceptions and QML reference errors."""
    if not ctx.device_online:
        return "SKIP", "Device offline", {}
    try:
        r = _adb_cmd(ctx, ["shell", "logcat -d -t 400"])
        stdout = r.stdout
        ref_count = stdout.count("ReferenceError: lnBadgeText")
        fatal_count = stdout.count("FATAL EXCEPTION")

        details = {"lnBadgeText_errors": ref_count, "fatal_exceptions": fatal_count}
        if ref_count == 0 and fatal_count == 0:
            return "PASS", "Logcat clean (0 FATAL, 0 lnBadgeText errors)", details
        return "FAIL", f"Logcat errors detected: {fatal_count} fatal, {ref_count} lnBadgeText", details
    except Exception as e:
        return "FAIL", f"Logcat check failed: {e}", {}


TESTS = [
    test_device_adb_reachability,
    test_device_package_installed,
    test_device_running_processes,
    test_device_custody_passphrase_prefix,
    test_device_runtime_rpc_version,
    test_device_enable_testnet_coins,
    test_device_balances,
    test_device_orderbook_query,
    test_device_maker_order_validation,
    test_device_logcat_error_audit,
]
