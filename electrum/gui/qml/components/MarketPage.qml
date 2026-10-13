import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import org.electrum 1.0

import "controls"

// #91 multi-coin market page: pair selector (tELEK / tBTC / SEPOLIAETH -
// view-frame pairs, ELEK-ish coin as base), balance chips with tap-to-enable,
// clickable order book. #92 click-to-fulfill: tapping a quote opens a
// prefilled taker dialog (buy_taker against an ask / buy against a bid).
ElDialog {
    id: root

    title: qsTr('Elektron Market')

    // view-frame convention: pairs sit with the ELEK-ish coin as base
    // (("tELEK","tBTC") default - zero migration from the former fixed pair)
    property string pairBase: 'tELEK'
    property string pairRel: 'tBTC'
    // best book prices in view terms (rel per base); restored on every book
    // refresh since the book order is not guaranteed sorted (asks arrived
    // 4.2/3.5/2.0 in the #83 federation mesh)
    property string bestAsk: ''
    property string bestBid: ''
    // #91 mirrors of the plugin state (pyqtProperty has no explicit notify,
    // so handlers re-assign these on the ready signals)
    property var balances: ({})
    property var enabledList: []
    property var coins: ['tELEK', 'tBTC', 'SEPOLIAETH']
    // #92 fulfill-dialog state
    property string fSide: 'ask'
    property string fPrice: ''
    property string fMax: ''

    implicitWidth: 540
    implicitHeight: 880

    function displayNum(x) {
        // parse -> round to 8 dp -> strip trailing zeros/dot (0.05000000 -> 0.05)
        var f = parseFloat(String(x).replace(',', '.'))
        if (isNaN(f)) return ''
        var s = f.toFixed(8)
        s = s.replace(/(\.\d*?)0+$/, '$1')
        s = s.replace(/\.$/, '')
        return s
    }

    // volume the balance can fund for the given side at the given price
    // ('ask': rel-needed / price; 'bid': base volume directly). NaN when no
    // balance is known - openFulfill then falls back to the book volume.
    function budgetVolume(side, price) {
        var b = root.balances
        var p = parseFloat(String(price).replace(',', '.'))
        if (side === 'ask') {
            var relBal = (b && b[root.pairRel]) ? parseFloat(b[root.pairRel].balance) : NaN
            if (isNaN(relBal) || isNaN(p) || p <= 0) return NaN
            return relBal / p
        }
        var baseBal = (b && b[root.pairBase]) ? parseFloat(b[root.pairBase].balance) : NaN
        if (isNaN(baseBal)) return NaN
        return baseBal
    }

    function openFulfill(side, price, maxVolume) {
        root.fSide = side
        root.fPrice = String(price)
        root.fMax = String(maxVolume)
        var budget = budgetVolume(side, price)
        var cap = parseFloat(String(maxVolume).replace(',', '.'))
        var v = ''
        if (!isNaN(budget))
            v = (isNaN(cap) || budget <= cap) ? budget : cap
        else if (!isNaN(cap))
            v = cap
        // imperative, not a binding - user edits would break a binding
        fpVolume.text = displayNum(v)
        fpPrice.text = String(price)
        fulfillPopup.open()
    }

    function refreshAll() {
        if (MarketPlugin == null || MarketPlugin == undefined) { statusLabel.text = qsTr("Market plugin not loaded."); return }
        MarketPlugin.refresh_rates()
        MarketPlugin.refresh_orderbook(pairBase, pairRel)
        if (MarketPlugin.swapConfigured) {
            MarketPlugin.refresh_my_orders()
            MarketPlugin.refresh_enabled()
        } else {
            statusLabel.text = qsTr('Kein kdf-Endpoint konfiguriert - Trades deaktiviert (Settings).')
        }
        root.enabledList = MarketPlugin.enabledCoins || []
        root.balances = MarketPlugin.balances || {}
    }

    Connections {
        target: MarketPlugin
        function onRatesReady() {
            var r = MarketPlugin.rates
            if (r !== null) {
                rateLabel.text = qsTr('1 ELEK ≈ %1 USD / %2 EUR (source: %3)').arg(r.usd).arg(r.eur).arg(r.source)
            }
        }
        function onEnabledReady() {
            root.enabledList = MarketPlugin.enabledCoins || []
        }
        function onBalancesReady() {
            root.balances = MarketPlugin.balances || {}
        }
        function onOrderbookReady() {
            var ob = MarketPlugin.orderbook
            if (ob !== null) {
                root.bestAsk = ''
                root.bestBid = ''
                bookModel.clear()
                var asks = ob.asks || [], bids = ob.bids || []
                var bestA = null, bestB = null
                for (var i = 0; i < asks.length; ++i) {
                    var ap = parseFloat(asks[i].price)
                    if (bestA === null || ap < bestA) bestA = ap
                    bookModel.append({ side: qsTr('Ask'), kind: 'ask',
                                       price: String(asks[i].price),
                                       volume: String(asks[i].max_volume) })
                }
                for (var j = 0; j < bids.length; ++j) {
                    var bp = parseFloat(bids[j].price)
                    if (bestB === null || bp > bestB) bestB = bp
                    bookModel.append({ side: qsTr('Bid'), kind: 'bid',
                                       price: String(bids[j].price),
                                       volume: String(bids[j].max_volume) })
                }
                root.bestAsk = bestA !== null ? String(bestA) : ''
                root.bestBid = bestB !== null ? String(bestB) : ''
                statusLabel.text = qsTr('%1/%2: %3 Asks / %4 Bids - antippen zum Erfüllen').arg(root.pairBase).arg(root.pairRel).arg(asks.length).arg(bids.length)
            }
        }
        function onMyOrdersReady() {
            var mo = MarketPlugin.myOrders || {}
            myOrdersModel.clear()
            var arr = mo.maker_orders || []
            for (var i = 0; i < arr.length; ++i) {
                myOrdersModel.append({
                    uuid: String(arr[i].uuid),
                    pairText: String(arr[i].base) + '/' + String(arr[i].rel),
                    side: String(arr[i].base) === root.pairBase ? qsTr('Ask') : qsTr('Bid'),
                    price: String(arr[i].price),
                    volume: String(arr[i].volume),
                    available: String(arr[i].available),
                    cancellable: !!arr[i].cancellable
                })
            }
            emptyOrdersLabel.visible = arr.length === 0
        }
        function onMakerOrderReady() {
            var m = MarketPlugin.lastMakerOrder
            if (m !== null && m !== undefined)
                makerLabel.text = qsTr('Order %1 angelegt: %2 @%3 (Menge %4)')
                    .arg(String(m.uuid).substring(0, 8)).arg(m.side).arg(m.price).arg(m.volume)
        }
        function onBuyResult() {
            var u = MarketPlugin.swapUuid
            swapLabel.text = qsTr('Swap started: %1').arg(u ? u.substring(0, 8) : '-')
            root.swapRunning = !!u
            if (u)
                MarketPlugin.check_status()
        }
        function onSwapStatusReady() {
            var s = MarketPlugin.swapStatus
            if (s !== null && s !== undefined) {
                var u = s.uuid ? String(s.uuid).substring(0, 8) : '-'
                if (s.state === 'OrderWaitingForMatch')
                    swapLabel.text = (s.retries > 0)
                        ? qsTr('Order %1 im Buch - gegen Kreuzquote erneuert (%2x)').arg(u).arg(s.retries)
                        : qsTr('Order %1 im Buch - wartet auf Match').arg(u)
                else
                    swapLabel.text = qsTr('Swap %1 - %2').arg(u).arg(s.state)
                swapEventsLabel.text = (s.types || []).join(' -> ')
                root.swapRunning = !(s.state === 'Finished' || s.state === 'Failed'
                                  || s.state === 'Refunded' || s.state === 'Successful'
                                  || s.state === 'Error')
            }
        }
        function onError(msg) {
            statusLabel.text = msg
            if (String(msg).indexOf('no swap to check') < 0)
                root.swapRunning = false
        }
    }

    // Bounded status poll while the dialog is open: starts with a buy, stops
    // once my_swap_status reports a final state (or an error occurs).
    property bool swapRunning: false
    Timer {
        id: swapTimer
        interval: 5000
        repeat: true
        running: root.swapRunning
        onTriggered: MarketPlugin && MarketPlugin.check_status()
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // whole content scrolls - the page outgrew the dialog on small screens
        Flickable {
            Layout.fillWidth: true
            Layout.fillHeight: true

            contentHeight: pageColumn.height
            clip: true
            interactive: height < contentHeight

            ColumnLayout {
                id: pageColumn
                width: parent.width
                spacing: 6

                Label { text: qsTr('Paar') }
                ComboBox {
                    id: pairSelector
                    Layout.fillWidth: true
                    textRole: 'p'
                    model: ListModel {
                        ListElement { p: 'tELEK / tBTC'; b: 'tELEK'; r: 'tBTC' }
                        ListElement { p: 'tELEK / SEPOLIAETH'; b: 'tELEK'; r: 'SEPOLIAETH' }
                        ListElement { p: 'tBTC / SEPOLIAETH'; b: 'tBTC'; r: 'SEPOLIAETH' }
                    }
                    onActivated: function (index) {
                        var b = model.get(index).b
                        var r = model.get(index).r
                        if (b === root.pairBase && r === root.pairRel) return
                        root.pairBase = b
                        root.pairRel = r
                        root.bestAsk = ''
                        root.bestBid = ''
                        if (MarketPlugin !== null && MarketPlugin !== undefined)
                            MarketPlugin.refresh_orderbook(b, r)
                    }
                }
                Label {
                    id: rateLabel
                    Layout.fillWidth: true
                    text: qsTr('loading…')
                }

                Heading { text: qsTr('Guthaben') }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    Repeater {
                        model: root.coins
                        delegate: Rectangle {
                            required property string modelData
                            required property int index
                            implicitWidth: chipCol.implicitWidth + 16
                            implicitHeight: chipCol.implicitHeight + 10
                            radius: 6
                            border.width: 1
                            border.color: root.enabledList.indexOf(modelData) >= 0
                                          ? constants.colorDone : constants.mutedForeground
                            ColumnLayout {
                                id: chipCol
                                x: 8; y: 5
                                spacing: 0
                                Label { text: modelData; font.pixelSize: 12; font.bold: true }
                                Label {
                                    font.pixelSize: 11
                                    color: constants.mutedForeground
                                    text: {
                                        var b = root.balances[modelData]
                                        if (!b) return qsTr('inaktiv')
                                        if (b.error) return qsTr('Fehler: %1').arg(b.error)
                                        var s = displayNum(b.balance)
                                        return s === '' ? qsTr('…') : s
                                    }
                                }
                                FlatButton {
                                    text: qsTr('Aktivieren')
                                    font.pixelSize: 11
                                    visible: root.enabledList.indexOf(modelData) < 0
                                    onClicked: {
                                        if (MarketPlugin !== null && MarketPlugin !== undefined)
                                            MarketPlugin.enable_coin(modelData)
                                    }
                                }
                            }
                        }
                    }
                }

                Heading { text: qsTr('Orderbuch') }
                Label {
                    id: statusLabel
                    Layout.fillWidth: true
                    wrapMode: Text.WrapAnywhere
                    text: ''
                }
                ListView {
                    id: bookList
                    Layout.fillWidth: true
                    Layout.preferredHeight: 160
                    model: ListModel { id: bookModel }
                    delegate: ItemDelegate {
                        width: bookList.width
                        height: 34
                        onClicked: root.openFulfill(model.kind, model.price, model.volume)
                        contentItem: RowLayout {
                            spacing: 10
                            Rectangle {
                                implicitWidth: 8; implicitHeight: 8; radius: 4
                                color: model.kind === 'ask' ? 'red' : 'green'
                            }
                            Label { text: model.side; font.pixelSize: 12;
                                    color: model.side === qsTr('Ask') ? 'red' : 'green' }
                            Label { text: model.price; font.pixelSize: 14 }
                            Label { text: model.volume; font.pixelSize: 14; Layout.alignment: Qt.AlignRight }
                        }
                    }
                }

                Label { text: qsTr('Maker - eigene Order (Kurs in %1 je %2):').arg(root.pairRel).arg(root.pairBase) }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    TextField {
                        id: mkPrice
                        Layout.fillWidth: true
                        placeholderText: qsTr('Preis, z.B. 2.5')
                    }
                    TextField {
                        id: mkVolume
                        Layout.fillWidth: true
                        placeholderText: qsTr('Menge %1, z.B. 0.1').arg(root.pairBase)
                    }
                    ComboBox {
                        id: mkSide
                        font.pixelSize: 12
                        textRole: 'label'
                        valueRole: 'value'
                        model: ListModel {
                            ListElement { label: qsTr('Verkauf'); value: 'ask' }
                            ListElement { label: qsTr('Kauf'); value: 'bid' }
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        id: makerLabel
                        Layout.fillWidth: true
                        font.pixelSize: 11
                        wrapMode: Text.WrapAnywhere
                        text: ''
                    }
                    FlatButton {
                        text: qsTr('Order anlegen')
                        onClicked: {
                            if (MarketPlugin == null || MarketPlugin == undefined) { statusLabel.text = qsTr('plugin missing'); return }
                            if (mkPrice.text === '' || mkVolume.text === '') { statusLabel.text = qsTr('Preis und Menge angeben'); return }
                            MarketPlugin.make_order(root.pairBase, root.pairRel,
                                                    mkSide.currentValue, mkPrice.text, mkVolume.text)
                        }
                    }
                }

                Label { text: qsTr('Meine offenen Orders (alle Paare):') }
                ListView {
                    id: myOrdersList
                    Layout.fillWidth: true
                    Layout.preferredHeight: 128
                    model: ListModel { id: myOrdersModel }
                    delegate: ItemDelegate {
                        width: myOrdersList.width
                        height: 38
                        contentItem: RowLayout {
                            spacing: 8
                            Label { text: model.pairText; font.pixelSize: 11; font.bold: true }
                            Label { text: model.side; font.pixelSize: 11;
                                    color: model.side === qsTr('Ask') ? 'red' : 'green' }
                            Label { text: '@' + model.price; font.pixelSize: 12 }
                            Label {
                                text: qsTr('%1, frei %2').arg(model.volume).arg(model.available)
                                font.pixelSize: 11
                            }
                            Item { Layout.fillWidth: true }
                            FlatButton {
                                visible: model.cancellable
                                text: qsTr('Cancel')
                                onClicked: {
                                    if (MarketPlugin != null && MarketPlugin != undefined)
                                        MarketPlugin.cancel_order(model.uuid)
                                }
                            }
                        }
                    }
                }
                Label {
                    id: emptyOrdersLabel
                    visible: false
                    Layout.fillWidth: true
                    font.pixelSize: 11
                    text: qsTr('Keine offenen Orders.')
                }

                Label { text: qsTr('Taker - sofort (Menge in %1):').arg(root.pairBase) }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    TextField {
                        id: tkVolume
                        Layout.fillWidth: true
                        placeholderText: qsTr('Menge, z.B. 0.1')
                    }
                    TextField {
                        id: tkPrice
                        Layout.fillWidth: true
                        placeholderText: qsTr('Preis - leer = bestes')
                    }
                    FlatButton {
                        text: qsTr('Kaufen (Ask)')
                        onClicked: {
                            if (MarketPlugin == null || MarketPlugin == undefined) { statusLabel.text = qsTr('plugin missing'); return }
                            // #72 lesson: no silent defaults - an empty volume used to
                            // publish a resting taker order ('1' @ best cap) that
                            // stays in the book and answers NoSwapWithUuid.
                            if (tkVolume.text === '') {
                                statusLabel.text = qsTr('Menge angeben (z.B. 0.1)')
                                return
                            }
                            if (tkPrice.text === '' && root.bestAsk === '') {
                                statusLabel.text = qsTr('Keine Asks im Buch - Preis angeben')
                                return
                            }
                            MarketPlugin.buy_taker(root.pairBase, root.pairRel, tkVolume.text,
                                                   tkPrice.text !== '' ? tkPrice.text : root.bestAsk)
                        }
                    }
                    FlatButton {
                        text: qsTr('Verkaufen (Bid)')
                        onClicked: {
                            if (MarketPlugin == null || MarketPlugin == undefined) { statusLabel.text = qsTr('plugin missing'); return }
                            if (tkVolume.text === '') {
                                statusLabel.text = qsTr('Menge angeben (z.B. 0.1)')
                                return
                            }
                            if (tkPrice.text === '' && root.bestBid === '') {
                                statusLabel.text = qsTr('Keine Bids im Buch - Preis angeben')
                                return
                            }
                            MarketPlugin.buy(root.pairBase, root.pairRel, tkVolume.text,
                                             tkPrice.text !== '' ? tkPrice.text : root.bestBid)
                        }
                    }
                }

                Label {
                    id: swapLabel
                    Layout.fillWidth: true
                    text: ''
                }
                Label {
                    id: swapEventsLabel
                    Layout.fillWidth: true
                    font.pixelSize: 11
                    wrapMode: Text.WrapAnywhere
                    text: ''
                }

                RowLayout {
                    Layout.fillWidth: true
                    FlatButton {
                        Layout.fillWidth: true
                        text: qsTr('Refresh')
                        onClicked: root.refreshAll()
                    }
                    FlatButton {
                        Layout.fillWidth: true
                        text: qsTr('Status')
                        onClicked: {
                            if (MarketPlugin == null || MarketPlugin == undefined) { statusLabel.text = qsTr('plugin missing'); return }
                            MarketPlugin.check_status()
                        }
                    }
                }

                FlatButton {
                    Layout.fillWidth: true
                    text: qsTr('Close')
                    onClicked: root.close()
                }

            } // pageColumn
        } // Flickable
    }

    // #92 click-to-fulfill: prefilled taker dialog over the tapped quote
    ElDialog {
        id: fulfillPopup
        parent: Overlay.overlay
        modal: true
        anchors.centerIn: parent
        implicitWidth: parent.width * 0.85
        implicitHeight: Math.min(parent.height * 0.9, fpCol.implicitHeight + 120)
        title: root.fSide === 'ask'
               ? qsTr('Angebot erfüllen - %1 kaufen').arg(root.pairBase)
               : qsTr('Angebot erfüllen - %1 verkaufen').arg(root.pairBase)

        ColumnLayout {
            id: fpCol
            width: parent.width
            spacing: 6

            Label {
                Layout.fillWidth: true
                font.pixelSize: 12
                color: constants.mutedForeground
                text: qsTr('Buchpreis: %1 %2 je %3').arg(root.fPrice).arg(root.pairRel).arg(root.pairBase)
            }

            Label { text: qsTr('Menge (%1):').arg(root.pairBase) }
            RowLayout {
                Layout.fillWidth: true
                TextField {
                    id: fpVolume
                    Layout.fillWidth: true
                    inputMethodHints: Qt.ImhFormattedNumbersOnly
                    placeholderText: qsTr('Menge, z.B. 0.1')
                }
                FlatButton {
                    text: qsTr('Max')
                    onClicked: {
                        var b = root.budgetVolume(root.fSide, root.fPrice)
                        var cap = parseFloat(String(root.fMax).replace(',', '.'))
                        var v = !isNaN(b) && (isNaN(cap) || b <= cap) ? b : cap
                        fpVolume.text = root.displayNum(v)
                    }
                }
            }

            Label {
                Layout.fillWidth: true
                font.pixelSize: 11
                color: constants.mutedForeground
                text: root.fSide === 'ask'
                      ? qsTr('Preis (%1 je %2) - Maximum: darunter bleibt Rest im Buch').arg(root.pairRel).arg(root.pairBase)
                      : qsTr('Preis (%1 je %2) - Minimum: darüber bleibt Rest im Buch').arg(root.pairRel).arg(root.pairBase)
            }
            TextField {
                id: fpPrice
                Layout.fillWidth: true
                inputMethodHints: Qt.ImhFormattedNumbersOnly
            }

            Label {
                Layout.fillWidth: true
                font.pixelSize: 13
                text: {
                    var v = parseFloat(fpVolume.text.replace(',', '.'))
                    var p = parseFloat(fpPrice.text.replace(',', '.'))
                    if (isNaN(v) || isNaN(p) || v <= 0 || p <= 0) return ''
                    var t = v * p
                    return root.fSide === 'ask'
                        ? qsTr('Kosten: %1 %2').arg(root.displayNum(t)).arg(root.pairRel)
                        : qsTr('Erlös: %1 %2').arg(root.displayNum(t)).arg(root.pairRel)
                }
            }

            Label {
                Layout.fillWidth: true
                font.pixelSize: 11
                wrapMode: Text.WrapAnywhere
                color: constants.mutedForeground
                text: qsTr('Die Order wird als Taker-Auftrag gegen die beste passende Quote im Buch erfüllt (bis zu diesem Preis). Eine ungematchte Menge bleibt als eigene Order im Buch stehen (kdf macht sie nach 30 s zu einer Maker-Order).')
            }

            RowLayout {
                Layout.fillWidth: true
                FlatButton {
                    Layout.fillWidth: true
                    text: qsTr('Tauschen')
                    onClicked: {
                        var v = fpVolume.text.trim().replace(',', '.')
                        var p = fpPrice.text.trim().replace(',', '.')
                        if (v === '') { statusLabel.text = qsTr('Menge angeben'); return }
                        if (p === '' || parseFloat(p) <= 0) { statusLabel.text = qsTr('Preis angeben'); return }
                        if (MarketPlugin == null || MarketPlugin == undefined) { statusLabel.text = qsTr('plugin missing'); return }
                        if (root.fSide === 'ask')
                            MarketPlugin.buy_taker(root.pairBase, root.pairRel, fpVolume.text.trim(), fpPrice.text.trim())
                        else
                            MarketPlugin.buy(root.pairBase, root.pairRel, fpVolume.text.trim(), fpPrice.text.trim())
                        fulfillPopup.close()
                    }
                }
                FlatButton {
                    Layout.fillWidth: true
                    text: qsTr('Abbrechen')
                    onClicked: fulfillPopup.close()
                }
            }
        }
    }

    Component.onCompleted: {
        if (MarketPlugin !== null && MarketPlugin !== undefined && MarketPlugin.coins)
            root.coins = MarketPlugin.coins
        refreshAll()
    }
}