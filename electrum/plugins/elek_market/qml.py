#!/usr/bin/env python
# Elektron Electrum - Market-API plugin (QML part for Android).
# Registers a QSignalObject under "MarketPlugin" context property so the new
# QML "Tausch" page can call market-API functions. Read-only orderbook/rates
# plus a buyer path to a user-owned client-kdf (no market-side custody).

from typing import TYPE_CHECKING

import os

from PyQt6.QtCore import pyqtSignal, pyqtSlot, pyqtProperty

from electrum.i18n import _
from electrum.plugin import hook
from electrum.gui.common_qt.plugins import PluginQObject

from . import Plugin as ElekMarketBase
from . import kdf_runtime
from .market_api import (KdfClient, TESTNET_COINS, MarketApi,
                         MarketApiError, dec_div, dec_mul,
                         lightning_ticker, rates_from_book, LN_SUPPORTED,
                         sepolia_enable_params)


def retry_crossing(book: dict | None, kind: str, price: float) -> bool:
    """True when the order book (market_api.parse_orderbook shape, VIEW
    frame) holds a quote that crosses the resting taker order's own terms.

    #89 semantics: kdf matches a taker request only in the instant of its
    arrival (maker nodes call process_taker_request; a later-arriving
    MAKER order is merely inserted into the local book); an unmatched GTC
    taker order is auto-converted to a maker order after 30 s and from
    then on fills only against NEW taker arrivals - two maker orders never
    meet. So if the user's resting order now coexists with a crossing
    maker quote, filling requires cancel + re-issue (a fresh request).
    kind='ask': the order fills an ASK (buys `base` paying `rel`, cap
    `price`) -> crossed by any ask <= price. kind='bid': fills a BID (sells
    `base` for `rel`, floor `price`) -> crossed by any bid >= price."""
    if not isinstance(book, dict):
        return False
    try:
        side = "asks" if kind == "ask" else "bids"
        return any(
            0.0 < float(q["price"]) <= price + 1e-12
            if kind == "ask" else
            float(q["price"]) >= price - 1e-12 > 0.0
            for q in (book.get(side) or [])
        )
    except (TypeError, ValueError, KeyError):
        return False


TAKER_RETRY_MAX = 3
# kdf auto-converts an unmatched GTC taker order to a maker order after
# 30 s; a natural match settles within a few seconds of placement.
TAKER_RETRY_SETTLE_SECS = 45.0

if TYPE_CHECKING:
    from electrum.gui.qml import ElectrumGui, ElectrumQmlApplication


class QMarketSignalObject(PluginQObject):
    """QML-visible bridge to the market API. Exposed via
    `context.setContextProperty('MarketPlugin', self)` in init_qml.

    QML usage:
        MarketPlugin.endpoint
        MarketPlugin.refresh_rates()
        MarketPlugin.refresh_orderbook(base, rel)
        MarketPlugin.buy(base, rel, volume, price)        # taker vs BIDs
        MarketPlugin.buy_taker(base, rel, volume, price)  # taker vs ASKs
        MarketPlugin.make_order(base, rel, 'ask'|'bid', price, volume)
        MarketPlugin.cancel_order(uuid)
        MarketPlugin.refresh_my_orders()
        MarketPlugin.check_status()
        MarketPlugin.swapUuid / MarketPlugin.swapStatus
        MarketPlugin.myOrders / MarketPlugin.lastMakerOrder
        # #91 multi-coin:
        MarketPlugin.refresh_enabled()
        MarketPlugin.enable_coin('SEPOLIAETH')   # merges urls + contract
        MarketPlugin.refresh_balances()
        MarketPlugin.coins / MarketPlugin.enabledCoins / MarketPlugin.balances
    """

    ratesReady = pyqtSignal()
    orderbookReady = pyqtSignal()
    buyResult = pyqtSignal()
    swapStatusReady = pyqtSignal()
    myOrdersReady = pyqtSignal()
    makerOrderReady = pyqtSignal()
    enabledReady = pyqtSignal()    # #91: enabled-set refreshed
    balancesReady = pyqtSignal()   # #91: balance snapshot refreshed
    kdfStatusReady = pyqtSignal()  # #95: embedded kdf runtime phase changed
    lnStateReady = pyqtSignal()    # #93: lightning activation state changed
    error = pyqtSignal(str)

    _name = _('Elektron Market')

    def __init__(self, plugin, parent):
        super().__init__(plugin, parent)
        # #98: the ElectrumQmlApplication - the only window onto the open
        # wallet (Daemon.currentWallet), used by the custody passphrase.
        self._app = parent
        self._rates = None
        self._book = None
        self._last_buy = None
        self._last_error = None
        self._kdf: KdfClient | None = None
        self._swap_uuid = None
        self._swap_status = None
        self._my_orders = {}
        self._last_maker = None
        # #91 multi-coin state: which coins the client-kdf enabled +
        # their last known balances (coin -> parse_balance dict)
        self._enabled = []
        self._balances = {}
        # #93 lightning: per platform coin activation state ('activating' |
        # 'ok' | 'error: ...'; missing/'' = not yet attempted this session)
        # + the once-per-session attempt ledger for the self-heal path.
        self._ln_state = {}
        self._ln_tried = set()
        # #89 taker-retry state: the placed order's view terms + retry bookkeeping
        self._taker = None
        self._taker_since = 0.0
        self._retry_count = 0
        # #95 embedded market runtime: on Android the kdf binary ships in the
        # APK's nativeLibraryDir and is owned/started here; on desktop the
        # runtime stays a phase-'unavailable' shell (daemon comes from the
        # configured swap_endpoint). Phase changes surface via kdfStatusReady.
        self._kdf_rt = kdf_runtime.KdfRuntime(
            # electrum_path is a METHOD on SimpleConfig (returns the per-chain
            # datadir) - call it, or os.path.join gets a bound method object
            # (device crash: "TypeError: expected str, bytes or os.PathLike
            # object, not method" at kdf_runtime.KdfRuntime.__init__).
            base_dir=self.plugin.config.electrum_path(),
            # #97: plugin network settings reach the runtime at construction
            # only; they are resolved into the boot conf on every start.
            seednode=self.plugin.net_seednode,
            server_overrides=self.plugin.coin_server_overrides(),
            # #98: wallet-seed-derived kdf identity, evaluated in the
            # runtime thread on every start (the wallet loads later).
            passphrase_provider=self._custody_passphrase)
        self._kdf_rt.set_notify(self.kdfStatusReady.emit)
        if kdf_runtime.is_android():
            self._kdf_rt.start()

    @pyqtProperty(str, notify=kdfStatusReady)
    def kdfPhase(self) -> str:
        """lifecycle phase of the embedded kdf runtime (stopped/starting/
        ready/error/stopping/unavailable)."""
        return self._kdf_rt.phase

    @pyqtProperty(str, notify=kdfStatusReady)
    def kdfDetail(self) -> str:
        """error detail when kdfPhase == 'error' (log tail, secrets scrubbed)."""
        return self._kdf_rt.last_error or ''

    @pyqtSlot()
    def kdf_start(self):
        self._kdf_rt.start()

    @pyqtSlot()
    def kdf_stop(self):
        self._kdf_rt.stop()

    def _wallet_seed(self) -> str:
        """#98: seed of the open wallet ('' when none is open or it is
        password-protected - we must not (and cannot) ask here). The seed
        is used in-process only for hashing; it never enters conf/logs."""
        app = self._app
        daemon = getattr(app, "daemon", None)
        qe_wallet = getattr(daemon, "currentWallet", None)
        wallet = getattr(qe_wallet, "wallet", None)
        if wallet is None:
            return ""
        for password in (None, ""):
            try:
                return (wallet.get_seed(password) or "").strip()
            except Exception:
                continue
        return ""

    def _custody_passphrase(self) -> str:
        """#98 custody link - deterministic kdf identity from the App-Wallet
        seed (derive_kdf_passphrase tags the digest; the raw seed stays in
        the plugin process). '' = keep the persisted random passphrase."""
        return kdf_runtime.derive_kdf_passphrase(self._wallet_seed())

    @pyqtProperty(str)
    def endpoint(self):
        return self.plugin.endpoint

    @endpoint.setter
    def endpoint(self, value):
        self.plugin.set_endpoint(value)

    @pyqtSlot()
    def refresh_rates(self):
        """#96 truth source: the user's own kdf book mid (rates_from_book,
        snapshot source 'local-kdf'). The Render REST /fx/rates.json only
        answers when the diagnostic flag is set; without either source the
        error signal explains why no rate exists."""
        import asyncio as _asyncio
        from electrum.util import get_asyncio_loop
        kdf_err = None
        kdf = self._resolved_kdf()
        if kdf is not None:
            fut = _asyncio.run_coroutine_threadsafe(
                kdf.orderbook(*self.plugin.pair), get_asyncio_loop())
            try:
                self._rates = rates_from_book(
                    MarketApi.parse_orderbook(fut.result(timeout=30)))
                self.ratesReady.emit()
                return
            except Exception as e:
                kdf_err = str(e)
        if not self.plugin.diagnostic_rest:
            self.error.emit(kdf_err or
                            'no kdf available (embedded runtime phase: %s) '
                            '- REST diagnostic disabled'
                            % self._kdf_rt.phase)
            return
        api = self.plugin.get_api()
        fut = _asyncio.run_coroutine_threadsafe(api.get_rates_raw(), get_asyncio_loop())
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
        import asyncio as _asyncio
        from electrum.util import get_asyncio_loop
        kdf_err = None
        kdf = self._resolved_kdf()
        if kdf is not None:
            fut = _asyncio.run_coroutine_threadsafe(
                kdf.orderbook(base, rel), get_asyncio_loop())
            try:
                self._book = MarketApi.parse_orderbook(fut.result(timeout=30))
                self.orderbookReady.emit()
                return
            except Exception as e:
                kdf_err = str(e)
        if not self.plugin.diagnostic_rest:
            self.error.emit(kdf_err or
                            'no kdf available (embedded runtime phase: %s) '
                            '- REST diagnostic disabled'
                            % self._kdf_rt.phase)
            return
        api = self.plugin.get_api()
        fut = _asyncio.run_coroutine_threadsafe(api.get_orderbook(base, rel), get_asyncio_loop())
        try:
            ob = fut.result(timeout=30)
            self._book = api.parse_orderbook(ob)
            self.orderbookReady.emit()
        except Exception as e:
            self.error.emit(str(e))

    @pyqtProperty('QVariant')
    def orderbook(self):
        return self._book

    def _get_kdf(self, swap_endpoint, swap_userpass) -> KdfClient:
        """Shared client for buy + status polling (kept open across calls)."""
        k = self._kdf
        if k is None or k.url != (swap_endpoint or "").rstrip("/"):
            k = KdfClient(swap_endpoint or "", swap_userpass or "")
            self._kdf = k
        else:
            k.userpass = swap_userpass or ""
        return k

    def _resolved_kdf(self) -> KdfClient | None:
        """#96 truth source: the user's OWN kdf. Priority: the configured
        external swap_endpoint, else the embedded runtime (Android, phase
        'ready' - it owns the conf the rpc_password comes from). None when
        neither is up: then there is NO market truth (the Render REST layer
        is a diagnostic-only fallback, never the primary path)."""
        cfg = self.plugin.config
        swap_endpoint = cfg.get('plugins.elek_market.swap_endpoint')
        if swap_endpoint:
            return self._get_kdf(swap_endpoint,
                                 cfg.get('plugins.elek_market.swap_userpass'))
        rt = self._kdf_rt
        if rt.phase == kdf_runtime.PHASE_READY:
            return self._get_kdf(rt.rpc_endpoint, rt.rpc_userpass())
        return None

    def _get_kdf_cfg(self) -> KdfClient:
        """Resolved kdf for the order-management call sites; falls back to
        the plain config pair (possibly empty) so legacy error surfaces
        keep working when no kdf is reachable."""
        kdf = self._resolved_kdf()
        if kdf is not None:
            return kdf
        cfg = self.plugin.config
        return self._get_kdf(cfg.get('plugins.elek_market.swap_endpoint'),
                             cfg.get('plugins.elek_market.swap_userpass'))

    def _post_buy_legacy(self, base: str, rel: str, volume: str, price: str):
        """Dispatch a kdf-native legacy `buy` to the user's OWN kdf (#96):
        the embedded runtime (Android) or the configured swap_endpoint.
        The market REST proxy is NOT a buy path (no-custody: only the
        wallet's own kdf may move coins - the daemons stay 0-balance, M2).
        Returns the raw response dict; without a resolved kdf it answers a
        {'error': ...} dict instead of raising (the QML slots do not wrap
        placement in try/except - _finish_buy surfaces the error)."""
        import asyncio as _asyncio
        from electrum.util import get_asyncio_loop
        kdf = self._resolved_kdf()
        if kdf is None:
            return {"error": "no kdf available - configure swap_endpoint "
                             "or wait for the embedded kdf runtime"}
        fut = _asyncio.run_coroutine_threadsafe(
            kdf.buy_legacy(base, rel, price, volume), get_asyncio_loop())
        try:
            return fut.result(timeout=200)
        except Exception as e:
            return {"error": str(e)[:200]}

    def _finish_buy(self, res):
        self._last_buy = res
        # Legacy buy carries the swap uuid somewhere below `result`
        # without a guaranteed top-level key; capture it for status polling.
        self._swap_uuid = KdfClient.find_uuid(res)
        if self._swap_uuid is None and isinstance(res, dict) \
                and res.get("error") is not None:
            self.error.emit(str(res["error"])[:200])
            return
        self.buyResult.emit()

    def _begin_taker(self, kind: str, base: str, rel: str,
                     volume: str, price: str):
        """#89 Taker-Retry bookkeeping: remember the placed order's view
        terms so check_status can cancel + re-issue it against a maker
        quote that crosses later (see retry_crossing)."""
        import time as _time
        self._taker = {"kind": kind, "args": (base, rel, volume, price)}
        self._taker_since = _time.time()
        self._retry_count = 0

    def _place_view_taker(self, kind: str, base: str, rel: str,
                          volume: str, price: str):
        """Placement shared by the QML slots (validated inputs) and the
        #89 retry path - applies the view->kdf-native mapping for 'bid'
        orders but touches NO retry bookkeeping (the retry path must not
        reset its counter)."""
        if kind == 'ask':
            res = self._post_buy_legacy(base, rel, volume, price)
        else:  # 'bid': kdf-native buy(rel, base), reciprocal mapping
            p, v = float(price), float(volume)  # validate; strings carry on
            res = self._post_buy_legacy(rel, base,
                                        dec_mul(volume, price),
                                        dec_div("1", price))
        self._finish_buy(res)

    def _maybe_retry(self):
        """#89: the resting taker order coexists with a maker quote that
        crosses its terms only AFTER its arrival instant - kdf never
        matches two maker orders. Cancel + re-issue (fresh taker request,
        bounded by TAKER_RETRY_MAX) so the counterparty matches on
        arrival. Settle window: the kdf conversion itself happens at 30 s
        and a natural match can take a few seconds to propagate."""
        import asyncio as _asyncio
        import time as _time
        from electrum.util import get_asyncio_loop

        taker = self._taker
        if taker is None or self._retry_count >= TAKER_RETRY_MAX:
            return
        if _time.time() - self._taker_since < TAKER_RETRY_SETTLE_SECS:
            return
        kdf = self._resolved_kdf()
        if kdf is None:
            return  # #96: no market truth without the user's own kdf
        base, rel, volume, price = taker["args"]
        fut = _asyncio.run_coroutine_threadsafe(
            kdf.orderbook(base, rel), get_asyncio_loop())
        try:
            book = MarketApi.parse_orderbook(fut.result(timeout=30))
            if not retry_crossing(book, taker["kind"], float(price)):
                return
        except Exception:
            return  # book unreadable - retry again on the next poll
        try:
            kdf = self._get_kdf_cfg()
            _asyncio.run_coroutine_threadsafe(
                kdf.cancel_order(self._swap_uuid), get_asyncio_loop()
            ).result(timeout=30)
        except Exception:
            return  # cannot cancel (e.g. being matched) - keep the order
        self._retry_count += 1
        self._taker_since = _time.time()
        self._place_view_taker(taker["kind"], *taker["args"])

    @pyqtSlot(str, str, str, str)
    def buy(self, base: str, rel: str, volume: str, price: str):
        """Taker that FILLS A BID of the view pair (instantly SELL `base`
        for `rel`). View contract: volume in `base`, price in `rel` per
        `base` (the book's bid terms). kdf-native it is buy(rel, base):
        the volume converts to the rel-proceeds (volume * price) and the
        kdf price to its reciprocal (base per rel). At 1:1 both are 1 -
        the historic e2e form ('1', '1') stays equivalent."""
        try:
            p = float(price)
            v = float(volume)
            if p <= 0 or v <= 0:
                raise ValueError('price and volume must be > 0')
        except ValueError as e:
            self.error.emit(str(e))
            return
        self._begin_taker('bid', base, rel, volume, price)
        self._place_view_taker('bid', base, rel, volume, price)

    @pyqtSlot(str, str, str, str)
    def buy_taker(self, base: str, rel: str, volume: str, price: str):
        """Taker that FILLS AN ASK of the view pair: buy `base` paying
        `rel`. NO pair mapping (view pair == native pair): volume in
        `base`, price = max `rel` per `base` accepted (the ask terms)."""
        try:
            if float(price) <= 0 or float(volume) <= 0:
                raise ValueError('price and volume must be > 0')
        except ValueError as e:
            self.error.emit(str(e))
            return
        self._begin_taker('ask', base, rel, volume, price)
        self._place_view_taker('ask', base, rel, volume, price)

    @pyqtSlot(str, str, str, str, str)
    def make_order(self, base: str, rel: str, side: str,
                   price: str, volume: str):
        """MAKER order in the view pair. side='ask': offer `base` for
        `rel` (a plain setprice(base, rel)). side='bid': bid for `base`
        paying `rel` - kdf-natively setprice(rel, base) with reciprocal
        price and the rel-proceeds as volume. cancel_previous=False so
        several orders per pair can coexist, each cancellable by uuid."""
        import asyncio as _asyncio
        from electrum.util import get_asyncio_loop

        try:
            p = float(price)
            v = float(volume)
            if p <= 0 or v <= 0:
                raise ValueError('price and volume must be > 0')
            if side == 'ask':
                # validated inputs pass through as the ORIGINAL strings -
                # str(float) would re-shape 0.1 -> 0.100000000000000006
                k_base, k_rel = base, rel
                k_price, k_vol = price, volume
                k_min = dec_div(volume, "10")
            elif side == 'bid':
                proceeds = dec_mul(volume, price)
                k_base, k_rel = rel, base
                k_price, k_vol = dec_div("1", price), proceeds
                k_min = dec_div(proceeds, "10")
            else:
                raise ValueError(
                    f"side must be 'ask' or 'bid', got {side!r}")
        except (ValueError, ZeroDivisionError) as e:
            self.error.emit(str(e))
            return
        try:
            kdf = self._get_kdf_cfg()
            fut = _asyncio.run_coroutine_threadsafe(
                kdf.setprice(k_base, k_rel, k_price, k_vol,
                             min_volume=k_min), get_asyncio_loop())
            res = fut.result(timeout=60)
            maker = res.get("result") if isinstance(res, dict) else None
            uuid = maker.get("uuid") if isinstance(maker, dict) else None
            if not uuid:
                raise MarketApiError(-5, f"setprice: {str(res)[:200]}")
            self._last_maker = {"uuid": uuid, "base": base, "rel": rel,
                                "side": side, "price": price,
                                "volume": volume}
            self.makerOrderReady.emit()
            self.refresh_my_orders()
        except Exception as e:
            self.error.emit(str(e))

    @pyqtSlot(str)
    def cancel_order(self, uuid: str):
        import asyncio as _asyncio
        from electrum.util import get_asyncio_loop
        kdf = self._get_kdf_cfg()
        fut = _asyncio.run_coroutine_threadsafe(
            kdf.cancel_order(str(uuid)), get_asyncio_loop())
        try:
            fut.result(timeout=30)
            self.refresh_my_orders()
        except Exception as e:
            self.error.emit(str(e))

    @pyqtSlot()
    def refresh_my_orders(self):
        import asyncio as _asyncio
        from electrum.util import get_asyncio_loop
        kdf = self._get_kdf_cfg()
        fut = _asyncio.run_coroutine_threadsafe(
            kdf.my_orders_legacy(), get_asyncio_loop())
        try:
            self._my_orders = KdfClient.parse_my_orders(fut.result(timeout=30))
            self.myOrdersReady.emit()
        except Exception as e:
            self.error.emit(str(e))

    @pyqtProperty('QVariant')
    def myOrders(self):
        return self._my_orders

    @pyqtProperty('QVariant')
    def lastMakerOrder(self):
        return self._last_maker

    @pyqtProperty(bool)
    def swapConfigured(self):
        """#96: trades are live whenever A kdf is reachable - the embedded
        runtime (Android, phase ready) or the configured swap_endpoint,
        independent of the REST diagnostic flag."""
        return self._resolved_kdf() is not None

    @pyqtProperty(bool)
    def diagnosticRest(self):
        """#96: whether the Render REST layer may act as read-only
        fallback (opt-in; default OFF - kdf is the only truth)."""
        return self.plugin.diagnostic_rest

    @pyqtProperty('QVariant')
    def lastBuy(self):
        return self._last_buy

    @pyqtProperty(str)
    def swapUuid(self):
        return self._swap_uuid or ""

    @pyqtSlot()
    def check_status(self):
        """Poll my_swap_status for the captured swap uuid and expose the
        parsed event list + state via swapStatus/swapStatusReady (B3 #5).

        #72 finding: a taker `buy` whose price cap finds no maker match is
        published as a RESTING ORDER - my_swap_status for its uuid answers
        mmrpc 2.0 error_type NoSwapWithUuid until a maker matches it (the
        order uuid only becomes a swap uuid at match). Classify that as
        "OrderWaitingForMatch" so the QML poll timer keeps running instead
        of surfacing an error."""
        if not self._swap_uuid:
            self.error.emit('no swap to check yet')
            return
        import asyncio as _asyncio
        from electrum.util import get_asyncio_loop

        kdf = self._resolved_kdf()
        if kdf is None:
            self.error.emit('no kdf available - cannot poll swap %s'
                            % self._swap_uuid)
            return
        fut = _asyncio.run_coroutine_threadsafe(
            kdf.my_swap_status(self._swap_uuid), get_asyncio_loop())
        try:
            raw = fut.result(timeout=60)
        except MarketApiError as e:
            if 'NoSwapWithUuid' in str(getattr(e, "body", "")):
                self._swap_status = {"uuid": self._swap_uuid, "types": [],
                                     "state": "OrderWaitingForMatch",
                                     "retries": self._retry_count}
                self.swapStatusReady.emit()
                self._maybe_retry()
                return
            self.error.emit(str(e))
            return
        try:
            status = KdfClient.parse_swap_status(raw)
            status["uuid"] = self._swap_uuid
            self._swap_status = status
            self.swapStatusReady.emit()
        except Exception as e:
            self.error.emit(str(e))

    @pyqtProperty('QVariant')
    def swapStatus(self):
        return self._swap_status

    # ---- #91 multi-coin: enabled-set + balances on the client-kdf ----

    @pyqtProperty('QVariantList')
    def coins(self):
        return list(TESTNET_COINS)

    @pyqtProperty('QVariant')
    def enabledCoins(self):
        return self._enabled

    @pyqtProperty('QVariant')
    def balances(self):
        return self._balances

    # ---- #93 lightning counterparts (tELEK/tBTC; SEPOLIAETH is EVM) ----

    @pyqtProperty('QVariantList')
    def lnCoins(self):
        """The platform coins that get a Lightning counterpart."""
        return list(LN_SUPPORTED)

    @pyqtProperty('QVariant', notify=lnStateReady)
    def lnState(self):
        """Per platform coin: 'activating' | 'ok' | 'error: ...' ('' or
        missing = not attempted this session); changes arrive via
        lnStateReady."""
        return self._ln_state

    @pyqtSlot()
    def refresh_enabled(self):
        """Refresh the coin set enabled on the client-kdf. Without a
        swap endpoint configured this is a silent no-op (the QML strip
        shows plain chips, no RPC spam against a missing daemon)."""
        if not self.swapConfigured:
            self._enabled = []
            self.enabledReady.emit()
            return
        import asyncio as _asyncio
        from electrum.util import get_asyncio_loop
        fut = _asyncio.run_coroutine_threadsafe(
            self._get_kdf_cfg().get_enabled_coins(), get_asyncio_loop())
        try:
            self._enabled = sorted(set(fut.result(timeout=30)))
            self.enabledReady.emit()
            self.refresh_balances()
        except Exception as e:
            self.error.emit(str(e))
        # #93 self-heal: platform coins whose Lightning counterpart is not
        # enabled yet are activated in the background (once per session per
        # coin). Only the embedded runtime qualifies - it owns the boot conf
        # and injects the -lightning entries (_ensure_lightning_coins); an
        # external swap_endpoint kdf must carry its own conf entries.
        if self._kdf_rt.phase == kdf_runtime.PHASE_READY:
            for ln_coin in LN_SUPPORTED:
                if (ln_coin not in self._enabled
                        or lightning_ticker(ln_coin) in self._enabled
                        or ln_coin in self._ln_tried):
                    continue
                self._ln_tried.add(ln_coin)
                self.enable_lightning(ln_coin)

    @pyqtSlot(str)
    def enable_coin(self, coin: str):
        """Enable one coin on the client-kdf (legacy `enable`). SEPOLIAETH
        merges urls + swap_contract_address - coins-live.json does NOT
        carry them; EIP-55 casing of the address is mandatory."""
        if not self.swapConfigured:
            self.error.emit('swap_endpoint not configured')
            return
        import asyncio as _asyncio
        from electrum.util import get_asyncio_loop
        params = sepolia_enable_params() if coin == 'SEPOLIAETH' else None
        fut = _asyncio.run_coroutine_threadsafe(
            self._get_kdf_cfg().enable_coin(coin, params),
            get_asyncio_loop())
        try:
            res = fut.result(timeout=60)
            # legacy enable answers 200 with {"error": ...} on failure
            err = res.get("error") if isinstance(res, dict) else None
            if err:
                self.error.emit(str(err)[:200])
                return
            if coin not in self._enabled:
                self._enabled = sorted(self._enabled + [coin])
                self.enabledReady.emit()
            self.refresh_balances()
            # #93: the Lightning counterpart follows its platform coin
            self._maybe_activate_lightning(coin)
        except Exception as e:
            self.error.emit(str(e))

    # ---- #93 lightning activation (task::enable_lightning on kdf) ----

    def _maybe_activate_lightning(self, coin: str):
        """Post-enable follow-up: fire the LN activation for a supported
        platform coin once per session (None/'' = not attempted yet;
        'error: ...' is retried when the user re-enables the coin)."""
        if coin not in LN_SUPPORTED:
            return
        state = self._ln_state.get(coin)
        if state is None or state == '' or state.startswith('error:'):
            self._ln_tried.add(coin)
            self.enable_lightning(coin)

    @pyqtSlot(str)
    def enable_lightning(self, coin: str):
        """Activate the coin's Lightning counterpart (task::enable_lightning
        on the user's kdf). Fire-and-forget on the shared asyncio loop - the
        caller may poll that task for up to 600 s, far too long to block the
        Qt event loop; the outcome lands in lnState ('ok' / 'error: ...')
        and is announced via lnStateReady."""
        if not self.swapConfigured:
            self.error.emit('swap_endpoint not configured')
            return
        if self._ln_state.get(coin) == 'activating':
            return  # a task is already running for this coin
        import asyncio as _asyncio
        from electrum.util import get_asyncio_loop
        self._ln_state = dict(self._ln_state, **{coin: 'activating'})
        self.lnStateReady.emit()
        _asyncio.run_coroutine_threadsafe(
            self._activate_lightning(coin), get_asyncio_loop())

    async def _activate_lightning(self, coin: str):
        try:
            res = await self._get_kdf_cfg().enable_lightning(
                coin, coin + '-node')
            state = 'ok' if res.get('status') == 'Ok' else \
                'error: %s' % str(res.get('details') or
                                  res.get('status'))[:100]
        except Exception as e:
            state = 'error: %s' % str(e)[:100]
        self._ln_state = dict(self._ln_state, **{coin: state})
        self.lnStateReady.emit()

    @pyqtSlot()
    def refresh_balances(self):
        """my_balance for every enabled coin (legacy fields; parse_balance
        tolerates wrapper shapes and surfaces daemon errors per coin)."""
        if not self._enabled:
            self._balances = {}
            self.balancesReady.emit()
            return
        import asyncio as _asyncio
        from electrum.util import get_asyncio_loop
        loop = get_asyncio_loop()
        kdf = self._get_kdf_cfg()
        out = {}
        for coin in self._enabled:
            fut = _asyncio.run_coroutine_threadsafe(
                kdf.my_balance(coin), loop)
            try:
                raw = fut.result(timeout=30)
                out[coin] = KdfClient.parse_balance(raw, coin=coin)
            except Exception as e:
                out[coin] = {"coin": coin, "balance": "0", "address": "",
                             "unspendable": "0", "error": str(e)[:200]}
        self._balances = out
        self.balancesReady.emit()

    # ---- #97: market network settings (embedded runtime) ----------------

    @pyqtProperty('QVariant')
    def netSeednode(self):
        """Current seednode setting ('' = template default); the QML page
        seeds its field from this once at page load."""
        return self.plugin.net_seednode

    @pyqtProperty('QVariant')
    def netCoinServers(self):
        """Per-coin server override map {coin: 'url1,url2'} for the page
        fields (compact-string view of the parsed settings)."""
        overrides = self.plugin.coin_server_overrides()
        return {coin: ','.join(urls) for coin, urls in overrides.items()}

    @pyqtSlot(str, 'QVariant')
    def save_net_settings(self, seednode: str, servers):
        """Persist the settings (no runtime restart - the user restarts
        explicitly; a running kdf must not be killed mid-swap)."""
        servers = servers if isinstance(servers, dict) else {}
        self.plugin.set_net_settings(seednode, servers or {})

    @pyqtSlot()
    def restart_runtime(self):
        """Restart the embedded runtime so saved settings take effect.
        Runs in a worker thread: stop() joins the process (STOP_GRACE
        worst case), which must not block the Qt event loop."""
        import threading as _t
        rt = self._kdf_rt

        def _apply():
            try:
                rt.stop()
                rt.start()
            except Exception:
                pass
        _t.Thread(target=_apply, daemon=True).start()


class Plugin(ElekMarketBase):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._app = None
        self._so = None

    @hook
    def init_qml(self, app: 'ElectrumQmlApplication'):
        self._app = app
        # QML-binding singleton; parent into the QObject tree to keep alive
        self._so = QMarketSignalObject(self, app)
        self._so.setParent(app)
        self.so = self._so  # QEAppController.plugin() expects has-property 'so'
        app.engine.rootContext().setContextProperty('MarketPlugin', self._so)
        self.logger.info('MarketPlugin context property registered')
