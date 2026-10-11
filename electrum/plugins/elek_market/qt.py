#!/usr/bin/env python
# Elektron Electrum – Market-API plugin (Qt part).
# Read-only market viewer: ELEK rate line + orderbook of the chosen pair.
#
# No custody, no swaps: this module never sends seeds, private keys, or
# money-moving RPCs. Trading over the market is out of scope (M2: optional).
#
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import pyqtSlot
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout)

import asyncio

from electrum.gui.qt.util import Buttons, CancelButton, OkButton, WindowModalDialog
from electrum.i18n import _
from electrum.plugin import hook
from electrum.util import get_asyncio_loop

from . import Plugin as ElekMarketBase
from .market_api import MarketApiError

if TYPE_CHECKING:
    from electrum.gui.qt import ElectrumWindow


BUILTIN_PAIRS = [
    ("tELEK", "tBTC"),
    ("tELEK", "tELEK2"),
    ("tELEK", "SEPOLIAETH"),
]


class MarketDialog(QDialog):
    """Read-only: ELEK rate line + best quotes of the chosen pair."""

    def __init__(self, window: 'ElectrumWindow', plugin: ElekMarketBase):
        super().__init__(window)
        self.window = window
        self.plugin = plugin
        self.api = plugin.get_api()
        self.setWindowTitle(_("Elektron Market"))

        self.pair_box = QComboBox(self)
        default_index = 0
        for i, pair in enumerate(BUILTIN_PAIRS):
            self.pair_box.addItem("%s / %s" % pair, pair)
            if pair == plugin.pair:
                default_index = i
        self.pair_box.setCurrentIndex(default_index)
        self.pair_box.currentIndexChanged.connect(self.refresh)

        self.rate_label = QLabel(_("Rates: loading…"))
        self.rate_label.setWordWrap(True)
        self.status_label = QLabel("")

        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels([_("Side"), _("Price"), _("Volume"), _("Address")])
        self.tree.setRootIsDecorated(False)

        refresh_btn = QPushButton(_("Refresh"))
        refresh_btn.clicked.connect(self.refresh)
        close_btn = QPushButton(_("Close"))
        close_btn.clicked.connect(self.accept)

        top = QHBoxLayout()
        top.addWidget(QLabel(_("Pair:")))
        top.addWidget(self.pair_box)
        top.addWidget(refresh_btn)
        top.addWidget(close_btn)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.rate_label)
        lay.addWidget(self.status_label)
        lay.addWidget(self.tree)
        self.resize(560, 420)
        self.refresh()

    @pyqtSlot()
    def refresh(self):
        pair = self.pair_box.currentData() or ("tELEK", "tBTC")
        asyncio.run_coroutine_threadsafe(
            self._refresh_async(*pair), get_asyncio_loop())

    async def _refresh_async(self, base: str, rel: str):
        try:
            snap = await self.api.get_rates_raw()
        except MarketApiError as e:
            self.rate_label.setText(_("Rates: {}").format(e))
        else:
            self.rate_label.setText(
                _("1 ELEK ≈ {usd} USD / {eur} EUR  (source: {source})").format(
                    usd=snap.get("usd") or "-", eur=snap.get("eur") or "-",
                    source=snap.get("source") or "-"))
        self.tree.clear()
        try:
            ob = await self.api.get_orderbook(base, rel)
            rows = self.api.parse_orderbook(ob)
        except MarketApiError as e:
            self.status_label.setText(_("Orderbook: {}").format(e))
            return
        except Exception as e:
            self.status_label.setText(str(e))
            return
        for a in rows["asks"]:
            self.tree.addTopLevelItem(QTreeWidgetItem(
                [_("Ask"), a["price"], a["max_volume"], a["address"]]))
        for b in rows["bids"]:
            self.tree.addTopLevelItem(QTreeWidgetItem(
                [_("Bid"), b["price"], b["max_volume"], b["address"]]))
        self.tree.resizeColumnToContents(0)
        self.tree.resizeColumnToContents(1)
        self.status_label.setText(
            _("{pair}: {n} quotes ({na} asks / {nb} bids)").format(
                pair=rows["pair"], n=len(rows["asks"]) + len(rows["bids"]),
                na=len(rows["asks"]), nb=len(rows["bids"])))


class Plugin(ElekMarketBase):
    """Qt extension of the base plugin: toolbar menu + dialog."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._dialog: Optional[MarketDialog] = None

    @hook
    def create_window(self, window: 'ElectrumWindow'):
        tools_menu = getattr(window, "tools_menu", None)
        if tools_menu is None:
            return
        action = QAction(_("Elektron Market..."), window)
        action.triggered.connect(lambda: self.open_dialog(window))
        tools_menu.addAction(action)

    def open_dialog(self, window: 'ElectrumWindow'):
        if self._dialog is not None and self._dialog.isVisible():
            self._dialog.raise_()
            return
        self._dialog = MarketDialog(window, self)
        self._dialog.show()

    def settings_dialog(self, window: 'ElectrumWindow'):
        dlg = WindowModalDialog(window, _("Elektron Market settings"))
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(_("Market-API endpoint (elek-web URL)")))
        endpoint_edit = QLineEdit(self.endpoint)
        lay.addWidget(endpoint_edit)
        lay.addWidget(QLabel(_("Default pair (base / rel)")))
        pair_h = QHBoxLayout()
        base_edit = QLineEdit(self.config.ELEK_MARKET_BASE.get())
        rel_edit = QLineEdit(self.config.ELEK_MARKET_REL.get())
        pair_h.addWidget(base_edit)
        pair_h.addWidget(QLabel("/"))
        pair_h.addWidget(rel_edit)
        lay.addLayout(pair_h)
        lay.addWidget(QLabel(_("Examples: https://<market>.onrender.com\n"
                               "http://<host>:10000 for a local container run")))
        lay.addLayout(Buttons(CancelButton(dlg), OkButton(dlg)))
        if dlg.exec():
            self.set_endpoint(endpoint_edit.text())
            self.set_pair(base_edit.text().strip(), rel_edit.text().strip())
