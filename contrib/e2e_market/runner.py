#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Elektron Net E2E Market Test Suite Runner (#103).

Executes automated end-to-end tests for the App, KDF runtime, host testbed
and Render federation, reporting honest passed/failed/skipped states.
"""
import argparse
import json
import os
import sys
import time
import traceback

DEFAULT_DEVICE_ADDR = os.environ.get("ELEKTRON_DEVICE_ADDR", "192.168.178.26:38153")
DEFAULT_CONTAINER_URL = os.environ.get("ELEKTRON_CONTAINER_URL", "http://127.0.0.1:10100")
DEFAULT_RENDER_URL = os.environ.get("ELEKTRON_RENDER_URL", "https://elektron-market.onrender.com")
DEFAULT_ELECTRS_PORT = int(os.environ.get("ELEKTRON_ELECTRS_PORT", "50005"))


class TestResult:
    def __init__(self, name, group, status, duration_s, message="", details=None):
        self.name = name
        self.group = group
        self.status = status  # 'PASS', 'FAIL', 'SKIP'
        self.duration_s = duration_s
        self.message = message
        self.details = details or {}

    def to_dict(self):
        return {
            "name": self.name,
            "group": self.group,
            "status": self.status,
            "duration_s": round(self.duration_s, 3),
            "message": self.message,
            "details": self.details,
        }


class TestContext:
    def __init__(self, device_addr, container_url, render_url, electrs_port, verbose=False):
        self.device_addr = device_addr
        self.container_url = container_url.rstrip("/")
        self.render_url = render_url.rstrip("/")
        self.electrs_port = electrs_port
        self.verbose = verbose
        self.device_online = False
        self.device_forward_port = 27821
        self.device_conf = None


def format_status(status):
    if sys.stdout.isatty():
        if status == "PASS":
            return "\033[32m[PASS]\033[0m"
        elif status == "FAIL":
            return "\033[31m[FAIL]\033[0m"
        elif status == "SKIP":
            return "\033[33m[SKIP]\033[0m"
    return f"[{status}]"


def run_test_case(ctx, test_func, group_name):
    t0 = time.monotonic()
    test_name = test_func.__name__
    try:
        res = test_func(ctx)
        dur = time.monotonic() - t0
        if isinstance(res, tuple):
            status, msg, details = res
        elif res is False:
            status, msg, details = "FAIL", "Assertion failed", {}
        else:
            status, msg, details = "PASS", "", {}
        return TestResult(test_name, group_name, status, dur, msg, details)
    except Exception as e:
        dur = time.monotonic() - t0
        tb = traceback.format_exc()
        return TestResult(test_name, group_name, "FAIL", dur, str(e), {"traceback": tb})


def main():
    parser = argparse.ArgumentParser(description="Elektron Net E2E Market Test Suite Runner")
    parser.add_argument("--device-addr", default=DEFAULT_DEVICE_ADDR,
                        help=f"Target ADB device address (default: {DEFAULT_DEVICE_ADDR})")
    parser.add_argument("--container-url", default=DEFAULT_CONTAINER_URL,
                        help=f"Local testbed container URL (default: {DEFAULT_CONTAINER_URL})")
    parser.add_argument("--render-url", default=DEFAULT_RENDER_URL,
                        help=f"Render market URL (default: {DEFAULT_RENDER_URL})")
    parser.add_argument("--electrs-port", type=int, default=DEFAULT_ELECTRS_PORT,
                        help=f"Electrs testnet port (default: {DEFAULT_ELECTRS_PORT})")
    parser.add_argument("--host-only", action="store_true",
                        help="Execute only host and render tests (skip device tests)")
    parser.add_argument("--device-only", action="store_true",
                        help="Execute only Android device tests")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print verbose details of test executions")
    parser.add_argument("--json", action="store_true",
                        help="Output results in JSON format")
    args = parser.parse_args()

    # Ensure this module's directory is in sys.path
    suite_dir = os.path.dirname(os.path.abspath(__file__))
    if suite_dir not in sys.path:
        sys.path.insert(0, suite_dir)

    import test_market_plugin
    import test_host_infra
    import test_render_federation
    import test_device_app
    import test_market_swaps

    ctx = TestContext(
        device_addr=args.device_addr,
        container_url=args.container_url,
        render_url=args.render_url,
        electrs_port=args.electrs_port,
        verbose=args.verbose,
    )

    test_groups = []
    if not args.device_only:
        test_groups.append(("Market Plugin & Dual Network", test_market_plugin.TESTS))
        test_groups.append(("Host Testbed Infra", test_host_infra.TESTS))
        test_groups.append(("Render & P2P Federation", test_render_federation.TESTS))
        test_groups.append(("Host Market Swaps & Ledger", test_market_swaps.TESTS))

    if not args.host_only:
        test_groups.append(("Device & App (Android)", test_device_app.TESTS))

    if not args.json:
        print("=" * 76)
        print(" ELEKTRON NET E2E MARKET TEST SUITE (#103)")
        print(f" Target ADB Device : {ctx.device_addr}")
        print(f" Local Container   : {ctx.container_url}")
        print(f" Render Edge       : {ctx.render_url}")
        print(f" Electrs Port      : {ctx.electrs_port}")
        print("=" * 76)

    results = []
    for group_name, tests in test_groups:
        if not args.json:
            print(f"\n--- {group_name} ---")
        for test_fn in tests:
            res = run_test_case(ctx, test_fn, group_name)
            results.append(res)
            if not args.json:
                tag = format_status(res.status)
                dur_ms = int(res.duration_s * 1000)
                msg_part = f" - {res.message}" if res.message else ""
                print(f"  {tag} {res.name} ({dur_ms}ms){msg_part}")
                if args.verbose and res.details:
                    for k, v in res.details.items():
                        print(f"       | {k}: {v}")

    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")
    skipped = sum(1 for r in results if r.status == "SKIP")
    total = len(results)

    if args.json:
        report = {
            "summary": {
                "total": total,
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "timestamp": time.time(),
            },
            "results": [r.to_dict() for r in results],
        }
        print(json.dumps(report, indent=2))
    else:
        print("\n" + "=" * 76)
        print(f" RESULTS: {total} total, {passed} passed, {failed} failed, {skipped} skipped")
        if failed > 0:
            print("\n FAILURES:")
            for r in results:
                if r.status == "FAIL":
                    print(f"  * {r.group} -> {r.name}: {r.message}")
                    if r.details.get("traceback") and args.verbose:
                        print(r.details["traceback"])
        print("=" * 76)

    # Return exit code 1 if any failures occurred, else 0
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
