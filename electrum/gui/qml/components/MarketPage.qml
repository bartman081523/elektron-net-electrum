import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import org.electrum 1.0

import "controls"

ElDialog {
    id: root

    title: qsTr('Elektron Market — Tausch tELEK↔tBTC')

    property string pairBase: 'tELEK'
    property string pairRel: 'tBTC'
    property string _bookStr: ''
    property string _rateStr: ''
    // best book prices in view terms (tBTC per tELEK); computed numerically
    // since the book order is not guaranteed sorted (asks arrived 4.2/3.5/2.0)
    property string bestAsk: ''
    property string bestBid: ''

    implicitWidth: 540
    implicitHeight: 880

    function refreshAll() {
        if (MarketPlugin == null || MarketPlugin == undefined) { statusLabel.text = qsTr("Market plugin not loaded."); return }
        MarketPlugin.refresh_rates()
        MarketPlugin.refresh_orderbook(pairBase, pairRel)
        if (MarketPlugin.swapConfigured)
            MarketPlugin.refresh_my_orders()
    }

    Connections {
        target: MarketPlugin
        function onRatesReady() {
            var r = MarketPlugin.rates
            if (r !== null) {
                rateLabel.text = qsTr('1 ELEK ≈ %1 USD / %2 EUR (source: %3)').arg(r.usd).arg(r.eur).arg(r.source)
            }
        }
        function onOrderbookReady() {
            var ob = MarketPlugin.orderbook
            if (ob !== null) {
                bookModel.clear()
                var asks = ob.asks || [], bids = ob.bids || []
                var bestA = null, bestB = null
                for (var i = 0; i < asks.length; ++i) {
                    var ap = parseFloat(asks[i].price)
                    if (bestA === null || ap < bestA) bestA = ap
                    bookModel.append({ side: qsTr('Ask'), price: String(asks[i].price),
                                       volume: String(asks[i].max_volume) })
                }
                for (var j = 0; j < bids.length; ++j) {
                    var bp = parseFloat(bids[j].price)
                    if (bestB === null || bp > bestB) bestB = bp
                    bookModel.append({ side: qsTr('Bid'), price: String(bids[j].price),
                                       volume: String(bids[j].max_volume) })
                }
                root.bestAsk = bestA !== null ? String(bestA) : root.bestAsk
                root.bestBid = bestB !== null ? String(bestB) : root.bestBid
                statusLabel.text = qsTr('%1/%2: %3 asks / %4 bids').arg(pairBase).arg(pairRel).arg(asks.length).arg(bids.length)
            }
        }
        function onMyOrdersReady() {
            var mo = MarketPlugin.myOrders || {}
            myOrdersModel.clear()
            var arr = mo.maker_orders || []
            for (var i = 0; i < arr.length; ++i) {
                myOrdersModel.append({
                    uuid: String(arr[i].uuid),
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
                    swapLabel.text = qsTr('Order %1 im Buch — wartet auf Match').arg(u)
                else
                    swapLabel.text = qsTr('Swap %1 — %2').arg(u).arg(s.state)
                swapEventsLabel.text = (s.types || []).join(' → ')
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
        spacing: 6

        Label {
            Layout.fillWidth: true
            text: qsTr('Pair: tELEK / tBTC')
            font.pixelSize: 16
        }
        Label {
            id: rateLabel
            Layout.fillWidth: true
            text: qsTr('loading…')
        }

        Label { text: qsTr('Order book') }
        Label {
            id: statusLabel
            Layout.fillWidth: true
            wrapMode: Text.WrapAnywhere
            text: ''
        }
        ListView {
            id: bookList
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 120
            model: ListModel { id: bookModel }
            delegate: ItemDelegate {
                width: bookList.width
                height: 34
                anchors.margins: 4
                contentItem: RowLayout {
                    spacing: 10
                    Label { text: model.side; font.pixelSize: 12;
                            color: model.side === qsTr('Ask') ? 'red' : 'green' }
                    Label { text: model.price; font.pixelSize: 14 }
                    Label { text: model.volume; font.pixelSize: 14; Layout.alignment: Qt.AlignRight }
                }
            }
        }

        Label { text: qsTr('Maker — eigene Order (Kurs in %1 je %2):').arg(root.pairRel).arg(root.pairBase) }
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

        Label { text: qsTr('Meine offenen Orders (eigener Kurs):') }
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

        Label { text: qsTr('Taker — sofort (Menge in %1):').arg(root.pairBase) }
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
                placeholderText: qsTr('Preis — leer = bestes')
            }
            FlatButton {
                text: qsTr('Kaufen (Ask)')
                onClicked: {
                    if (MarketPlugin == null || MarketPlugin == undefined) { statusLabel.text = qsTr('plugin missing'); return }
                    // #72 lesson: no silent defaults — an empty volume used to
                    // publish a resting taker order ('1' @ best cap) that
                    // stays in the book and answers NoSwapWithUuid.
                    if (tkVolume.text === '') {
                        statusLabel.text = qsTr('Menge angeben (z.B. 0.1)')
                        return
                    }
                    if (tkPrice.text === '' && root.bestAsk === '') {
                        statusLabel.text = qsTr('Keine Asks im Buch — Preis angeben')
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
                        statusLabel.text = qsTr('Keine Bids im Buch — Preis angeben')
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
    }

    Component.onCompleted: refreshAll()
}