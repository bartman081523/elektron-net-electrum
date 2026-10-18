#!/usr/bin/env python
# Elektron Electrum - embedded market runtime lifecycle (task #95).
#
# Owns the kdf process that ships inside the APK (lib/arm64-v8a/libkdfbin.so,
# extracted to nativeLibraryDir at install time - the exec-permitted location
# for targetSdk >= 29). Pure Python on purpose: no Qt types here, so the
# runtime is testable standalone; the QML bridge (qml.py) wraps phase changes
# into a signal.
#
# Config bootstrap: <electrum_path>/kdf/MM2.json is generated ONCE with a
# fresh random passphrase and rpc_password, then reused so the netid-8888
# identity and swap history survive app restarts. The seed must never be
# logged. Coins are the task-#84 device template (SEPOLIAETH + the two
# fork-testnet UTXO slots), electrum servers point at 127.0.0.1:50005
# (adb-reversed by the market flow), seednode is the Render federation
# entry. A stopped-but-live previous instance (app restart) is terminated
# via its pid file before a new one is spawned.
#
# Desktop (non-Android) is inert: start() moves the phase to 'unavailable'
# and never touches a process - the desktop plugin keeps using the external
# swap_endpoint config (see market_api.KdfClient).
import hashlib
import json
import os
import secrets
import subprocess
import threading
import time
import urllib.request

RPC_HOST = "127.0.0.1"
RPC_PORT = 7821
RPC_METHOD_VERSION = "version"
HEALTH_TIMEOUT = 2.0
POLL_INTERVAL = 2.0
START_DEADLINE = 120.0
STOP_GRACE = 5.0
BINARY_NAME = "libkdfbin.so"
NETID = 8888
SEEDNODE = "wss://elektron-market.onrender.com"
LOG_TAIL_BYTES = 4000

# #97 template defaults, restored at runtime boot when a settings override
# is cleared (the conf file is re-resolved on every start - settings are
# never stale in the persisted conf). SEPOLIAETH has no template urls: the
# kdf built-in public RPC nodes are the default, so a cleared override
# means the urls key is simply dropped.
TEMPLATE_SEEDNODES = [SEEDNODE]
TEMPLATE_SERVERS = {
    "tELEK": ["127.0.0.1:50005"],
    "tBTC": ["127.0.0.1:50005"],
    "SEPOLIAETH": None,
}

# #98: domain-separated derivation of the kdf trading identity from the
# app wallet seed (custody link: one wallet, one trading principal). The
# raw seed never enters the conf/logs - only this tagged digest does; a
# fresh install of the same wallet restores the same kdf identity and
# its swap history. kdf treats the passphrase string as raw seed material
# (task-#84 device passphrase was also a non-mnemonic string), so this
# hex form is a valid, wallet-unique seed.
KDF_PASSPHRASE_TAG = "elektron-net/kdf-passphrase/v1"
KDF_PASSPHRASE_PREFIX = "elkkdf1"


def derive_kdf_passphrase(seed_text) -> str:
    """Deterministic kdf passphrase from the wallet seed; '' when empty."""
    text = (seed_text or "").strip()
    if not text:
        return ""
    digest = hashlib.sha256(
        (KDF_PASSPHRASE_TAG + "/" + text).encode("utf-8")).hexdigest()
    return KDF_PASSPHRASE_PREFIX + digest


def _apply_custody_passphrase(conf: dict, passphrase: str) -> dict:
    """#98: a wallet-derived passphrase is authoritative (identity follows
    the App-Wallet); '' keeps whatever is persisted (fresh confs keep the
    random one). Callers persist the conf right after."""
    if passphrase:
        conf["passphrase"] = passphrase
    return conf


PHASE_UNAVAILABLE = "unavailable"
PHASE_STOPPED = "stopped"
PHASE_STARTING = "starting"
PHASE_READY = "ready"
PHASE_ERROR = "error"
PHASE_STOPPING = "stopping"


def is_android() -> bool:
    # same detection idiom as gui/qml/qeapp.py (jvius-guarding branch)
    return 'ANDROID_DATA' in os.environ


def binary_path() -> str | None:
    """Path of the shipped kdf binary, via the app's nativeLibraryDir.

    jnius is only importable inside the p4a APK (gui/qml/qeapp.py imports
    it under the same guard), so the import stays inside this function."""
    if not is_android():
        return None
    from jnius import autoclass  # noqa: E402 (p4a runtime only)
    activity = autoclass('org.kivy.android.PythonActivity').mActivity
    info = activity.getApplicationInfo()
    return os.path.join(info.nativeLibraryDir, BINARY_NAME)


# #93 Lightning: the -lightning coin entries never live in the registries
# (coins/upstream_coins + coins/elektron_coins carry none) - they are
# always constructed, mirroring the kdf fixture (mm2_main/tests/mm2_tests/
# lightning_tests.rs tBTC-TEST-lightning). The template coins are segwit
# UTXO platforms; SEPOLIAETH is EVM - there is no L2 for it, its legs stay
# on-chain HTLC. Legacy `enable` rejects Lightning tickers (lp_coins.rs) -
# activation goes through task::enable_lightning (market_api).
LN_TEMPLATE_COINS = ("tELEK", "tBTC")
# avg_blocktime of the platform coins in the template (both measured: the
# fork testnet settles in ~1 min - the fixture's targets are minutes too).
LN_PLATFORM_AVG_BLOCKTIME = {"tELEK": 1, "tBTC": 1}


def lightning_coin_entry(coin: str) -> dict:
    """Canonical -lightning coin entry: decimals 11 (msat precision), the
    fixture's channel config, confirmation targets rebalanced to the fork
    cadence (6/2/1 fixture-minutes). Deliberately NO allow_outbound_0conf -
    that flag is test-only in the fixture; a real channel needs
    its confirmations."""
    return {
        "coin": coin + "-lightning",
        "mm2": 1,
        "decimals": 11,
        "our_channels_configs": {
            "inbound_channels_confirmations": 1,
            "max_inbound_in_flight_htlc_percent": 90,
            "their_channel_reserve_sats": 1000,
        },
        "counterparty_channel_config_limits": {
            "outbound_channels_confirmations": 1,
        },
        "protocol": {
            "type": "LIGHTNING",
            "protocol_data": {
                "platform": coin,
                "network": "testnet",
                "confirmation_targets": {
                    "background": 6, "normal": 2, "high_priority": 1},
            },
        },
    }


def _ensure_lightning_coins(conf: dict) -> None:
    """Add the platform coins' Lightning counterparts to the daemon conf so
    they are activation candidates (task::enable_lightning requires the
    entry to exist in the conf the kernel boots with). Runs on every boot -
    also over a reused MM2.json (_ensure_conf). Strictly additive and
    idempotent: existing entries are never touched; platform coins only
    receive the LN prerequisites (segwit address_format + avg_blocktime)
    when the key is MISSING - persisted user values always win."""
    coins = conf.get("coins")
    if not isinstance(coins, list):
        return
    by_name = {}
    for entry in coins:
        if isinstance(entry, dict) and entry.get("coin"):
            by_name.setdefault(str(entry["coin"]), entry)
    for coin in LN_TEMPLATE_COINS:
        platform = by_name.get(coin)
        if platform is None:
            continue
        platform.setdefault("segwit", True)
        platform.setdefault("address_format", {"format": "segwit"})
        platform.setdefault("avg_blocktime", LN_PLATFORM_AVG_BLOCKTIME[coin])
    # A counterpart only makes sense on top of its own platform coin - a
    # -lightning entry for a chain the user doesn't carry would be inert
    # dead weight in their coin list.
    additions = [lightning_coin_entry(coin) for coin in LN_TEMPLATE_COINS
                 if coin in by_name
                 and coin + "-lightning" not in by_name]
    if additions:
        conf["coins"] = list(coins) + additions


# Coin entries are the task-#84 device-node template, verbatim (that
# configuration did a real HTLC swap on-device; see memory elektron-84).
def _base_conf() -> dict:
    return {
        "gui": "elektron-net",
        "netid": NETID,
        "rpcip": RPC_HOST,
        "rpcport": RPC_PORT,
        "seednodes": [SEEDNODE],
        "coins": [
            {
                "coin": "SEPOLIAETH",
                "name": "ethereum",
                "fname": "Ethereum Sepolia (kdf-native, public RPC nodes)",
                "derivation_path": "m/44'/60'",
                "protocol": {"type": "ETH", "protocol_data": {"chain_id": 11155111}},
                "max_eth_tx_type": 2,
                "mm2": 1,
                "is_testnet": True,
            },
            {
                "coin": "tBTC",
                "name": "bitcoin",
                "fname": "tBTC (Elektron fork-testnet electrs-tn)",
                "pubtype": 111,
                "p2shtype": 196,
                "wiftype": 239,
                "segwit": True,
                "bech32_hrp": "tb",
                "address_format": {"format": "segwit"},
                "decimals": 8,
                "txfee": 1000,
                "estimate_fee_mode": "ECONOMICAL",
                "estimate_fee_blocks": 4,
                "avg_blocktime": 1,
                "required_confirmations": 2,
                "mature_confirmations": 1,
                "derivation_path": "m/84'/1'",
                "orderbook_ticker": "tBTC",
                "rpcport": 18332,
                "mm2": 1,
                "is_testnet": True,
                "protocol": {"type": "UTXO"},
                "electrum": [{"url": "127.0.0.1:50005", "protocol": "TCP"}],
            },
            {
                "coin": "tELEK",
                "name": "elektron-testnet",
                "fname": "Elektron (testnet)",
                "pubtype": 111,
                "p2shtype": 196,
                "wiftype": 239,
                "segwit": True,
                "bech32_hrp": "tb",
                "address_format": {"format": "segwit"},
                "decimals": 8,
                "txfee": 1000,
                "estimate_fee_mode": "ECONOMICAL",
                "estimate_fee_blocks": 4,
                "avg_blocktime": 1,
                "required_confirmations": 2,
                "mature_confirmations": 1,
                "derivation_path": "m/84'/1370'",
                "orderbook_ticker": "tELEK",
                "rpcport": 18332,
                "mm2": 1,
                "protocol": {"type": "UTXO"},
                "electrum": [{"url": "127.0.0.1:50005", "protocol": "TCP"}],
            },
        ],
        "myipaddr": None,
        "rproxy_ips": [],
    }


def parse_server_overrides(raw) -> dict:
    """#97: compact per-coin server overrides as a dict - format
    'COIN=url1,url2;COIN2=url3'. Empty coin or empty url chunks are dropped,
    whitespace tolerated, tickers case-sensitive (they are the conf keys)."""
    out = {}
    for chunk in (raw or "").split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        coin, _, urls = chunk.partition("=")
        coin = coin.strip()
        urls = [u.strip() for u in urls.split(",") if u.strip()]
        if coin and urls:
            out[coin] = urls
    return out


def encode_server_overrides(mapping) -> str:
    """Inverse of parse_server_overrides - deterministic compact form
    'COIN=url1,url2;COIN2=url3' (mapping order preserved, empty entries
    dropped: an absent coin means 'use the template default')."""
    parts = []
    for coin, urls in (mapping or {}).items():
        urls = [u for u in (urls or []) if isinstance(u, str) and u.strip()]
        if urls:
            parts.append("%s=%s" % (str(coin).strip(), ",".join(urls)))
    return ";".join(parts)


def _sync_settings(conf: dict, seednode: str, overrides: dict) -> dict:
    """#97: user settings win at boot. seednode '' -> template list; for
    template coins an override replaces the electrum (UTXO) / urls (ETH)
    array and a cleared override restores the template default. Unknown
    coins stay untouched unless they carry an override."""
    seeds = [s.strip() for s in (seednode or "").split(",") if s.strip()]
    conf["seednodes"] = list(seeds or TEMPLATE_SEEDNODES)
    for entry in conf.get("coins", []):
        ticker = entry.get("coin") or ""
        urls = [u for u in overrides.get(ticker) or []
                if isinstance(u, str) and u.strip()]
        is_eth = entry.get("protocol", {}).get("type") == "ETH"
        if urls:
            if is_eth:
                entry["urls"] = urls
            else:
                entry["electrum"] = [{"url": u, "protocol": "TCP"}
                                     for u in urls]
        elif ticker in TEMPLATE_SERVERS:
            if is_eth:
                entry.pop("urls", None)
            else:
                entry["electrum"] = [{"url": u, "protocol": "TCP"}
                                     for u in (TEMPLATE_SERVERS[ticker] or [])]
    return conf


class KdfRuntime:
    """Lifecycle owner of the embedded kdf market runtime (Android only)."""

    def __init__(self, base_dir: str, seednode: str = "",
                 server_overrides: dict | None = None,
                 passphrase_provider=None):
        """#97: seednode (comma-separated list allowed, '' = template
        default) and per-coin server overrides (parsed dict) arrive from
        the plugin settings; they are resolved into the boot conf on every
        start (see _run / _sync_settings), never mid-flight.
        #98: passphrase_provider is an optional zero-arg callable evaluated
        in the runtime thread on every start - it returns the wallet-derived
        kdf passphrase ('' = keep the persisted one). The provider is
        deferred because the wallet is often loaded after this ctor."""
        self.seednode = (seednode or "").strip()
        self.server_overrides = dict(server_overrides or {})
        self.passphrase_provider = passphrase_provider
        self.dir = os.path.join(base_dir, "kdf")
        self.conf_path = os.path.join(self.dir, "MM2.json")
        self.log_path = os.path.join(self.dir, "kdf.log")
        self.tmp_dir = os.path.join(self.dir, "tmp")
        self.db_dir = os.path.join(self.dir, "db")
        self.pid_path = os.path.join(self.dir, "kdf.pid")
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._log_fh = None
        self._phase = PHASE_STOPPED
        self._error: str | None = None
        self._notify = None  # optional callback, set by the Qt bridge

    # -- state ------------------------------------------------------------

    @property
    def phase(self) -> str:
        with self._lock:
            return self._phase

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._error

    @property
    def rpc_endpoint(self) -> str:
        return "http://%s:%s" % (RPC_HOST, RPC_PORT)

    def rpc_userpass(self) -> "str | None":
        """rpc_password of the generated conf (#96: the Qt bridge binds an
        embedded KdfClient to this runtime). None when no conf exists (the
        runtime never started). Never logged - same secret class as the
        passphrase (_scrub)."""
        try:
            with open(self.conf_path) as f:
                return json.load(f).get("rpc_password") or None
        except Exception:
            return None

    def set_notify(self, cb) -> None:
        """cb is invoked (from runtime threads) on every phase change."""
        with self._lock:
            self._notify = cb

    def _set_phase(self, phase: str, error: str | None = None) -> None:
        with self._lock:
            self._phase = phase
            if error is not None or phase in (PHASE_STOPPED, PHASE_READY):
                self._error = error
            notify = self._notify
        if notify:
            try:
                notify()
            except Exception:
                pass

    # -- secrets/logs -----------------------------------------------------

    def _scrub(self, text: str) -> str:
        """Never let the passphrase or rpc_password reach an error surface."""
        try:
            with open(self.conf_path) as f:
                conf = json.load(f)
            for key in ("passphrase", "rpc_password"):
                secret = conf.get(key)
                if secret:
                    text = text.replace(secret, "***")
        except Exception:
            pass
        return text

    def _log_tail(self) -> str:
        try:
            with open(self.log_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - LOG_TAIL_BYTES))
                return f.read().decode("utf-8", "replace").strip()
        except Exception:
            return ""

    # -- config bootstrap --------------------------------------------------

    def _ensure_conf(self) -> dict:
        """Existing MM2.json is reused (identity + history live there);
        a fresh one gets newly generated secrets for passphrase and
        rpc_password, persisted once."""
        try:
            with open(self.conf_path) as f:
                return json.load(f)
        except FileNotFoundError:
            pass
        conf = _base_conf()
        conf["passphrase"] = secrets.token_hex(32)
        conf["rpc_password"] = secrets.token_hex(32)
        conf["dbdir"] = self.db_dir
        os.makedirs(self.dir, exist_ok=True)
        with open(self.conf_path, "w") as f:
            json.dump(conf, f, indent=2)
        os.chmod(self.conf_path, 0o600)
        return conf

    # -- process lifecycle --------------------------------------------------

    def start(self) -> None:
        """Non-blocking: spawns the supervisor thread and returns."""
        with self._lock:
            if not is_android():
                self._phase = PHASE_UNAVAILABLE
                self._error = "embedded runtime only on Android"
                return
            if self._phase in (PHASE_STARTING, PHASE_READY):
                return
            if self._phase == PHASE_STOPPING:
                return
            self._phase = PHASE_STARTING
            self._error = None
        threading.Thread(target=self._run, daemon=True).start()
        with self._lock:
            notify = self._notify
        if notify:
            try:
                notify()
            except Exception:
                pass

    def _kill_stale(self) -> None:
        """A previous app run may have left kdf bound to the RPC port;
        terminate it by pid file before spawning a fresh one."""
        try:
            with open(self.pid_path) as f:
                pid = int(f.read().strip())
        except (OSError, ValueError):
            return
        cmdline_path = "/proc/%d/cmdline" % pid
        try:
            with open(cmdline_path, "rb") as f:
                cmdline = f.read().decode("utf-8", "replace")
        except OSError:
            return
        if BINARY_NAME not in cmdline:
            return
        try:
            os.kill(pid, 15)
        except OSError:
            return
        for _ in range(50):
            if not os.path.exists(cmdline_path):
                break
            time.sleep(0.1)
        else:
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        try:
            os.remove(self.pid_path)
        except OSError:
            pass

    def _run(self) -> None:
        try:
            self._kill_stale()
            conf = self._ensure_conf()
            # #98 custody link: the wallet-seed-derived passphrase wins
            # over the persisted one whenever the provider has one
            # (identity follows the App-Wallet); the writeback below
            # persists the switch.
            custody = ""
            provider = self.passphrase_provider
            if provider is not None:
                try:
                    custody = provider()
                except Exception:
                    custody = ""
            conf = _apply_custody_passphrase(conf, custody)
            # #97: resolve the current settings into the conf and write it
            # back (identity/secrets untouched) so the spawned process and
            # the on-disk conf agree.
            conf = _sync_settings(conf, self.seednode, self.server_overrides)
            # #93: make the Lightning counterparts activatable - entries are
            # injected additively, never touching the user's own coin list.
            _ensure_lightning_coins(conf)
            with open(self.conf_path, "w") as f:
                json.dump(conf, f, indent=2)
            os.chmod(self.conf_path, 0o600)
            bin_path = binary_path()
            if not bin_path or not os.path.isfile(bin_path):
                raise RuntimeError("libkdfbin.so not found (APK packaged without it?)")
            os.makedirs(self.db_dir, exist_ok=True)
            os.makedirs(self.tmp_dir, exist_ok=True)
            env = os.environ.copy()
            env["TMPDIR"] = self.tmp_dir
            fh = open(self.log_path, "wb")
            self._log_fh = fh
            self._proc = subprocess.Popen(
                [bin_path, self.conf_path], stdout=fh, stderr=subprocess.STDOUT,
                env=env)
            with open(self.pid_path, "w") as f:
                f.write(str(self._proc.pid))
            self._wait_ready(conf)
        except Exception as e:
            try:
                self._stop_process(grace=STOP_GRACE)
            finally:
                if self._log_fh:
                    self._log_fh.close()
                    self._log_fh = None
                msg = self._scrub("%s\n%s" % (e, self._log_tail()))[:2000]
                self._set_phase(PHASE_ERROR, msg)

    def _wait_ready(self, conf: dict) -> None:
        """Poll the RPC port until our own kdf instance answers; dies with
        the process exit or the startup deadline."""
        body = json.dumps({
            "method": RPC_METHOD_VERSION,
            "userpass": conf["rpc_password"],
        }).encode()
        url = self.rpc_endpoint + "/"
        deadline = time.time() + START_DEADLINE
        while True:
            proc = self._proc
            if proc is None:
                raise RuntimeError("kdf stopped during startup")
            rc = proc.poll()
            if rc is not None:
                self._set_phase(PHASE_ERROR, "kdf exited rc=%s" % rc)
                raise RuntimeError("kdf exited rc=%s\n%s" % (rc, self._log_tail()))
            try:
                req = urllib.request.Request(
                    url, data=body, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=HEALTH_TIMEOUT) as r:
                    r.read()
                self._set_phase(PHASE_READY)
                return
            except Exception:
                pass
            if time.time() >= deadline:
                raise RuntimeError(
                    "kdf RPC not ready within %.0fs\n%s"
                    % (START_DEADLINE, self._log_tail()))
            time.sleep(POLL_INTERVAL)

    def stop(self) -> None:
        self._set_phase(PHASE_STOPPING)
        self._stop_process(grace=STOP_GRACE)
        self._set_phase(PHASE_STOPPED)

    def _stop_process(self, grace: float) -> None:
        proc, fh, stopped_pid = self._proc, self._log_fh, None
        with self._lock:
            self._proc = None
        if proc is None:
            return
        stopped_pid = proc.pid
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(grace)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if fh:
            fh.close()
        try:
            if os.path.isfile(self.pid_path):
                with open(self.pid_path) as f:
                    if stopped_pid == int(f.read().strip() or 0):
                        os.remove(self.pid_path)
        except (OSError, ValueError):
            pass