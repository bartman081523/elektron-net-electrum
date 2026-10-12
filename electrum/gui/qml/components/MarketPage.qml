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

    implicitWidth: 540
    implicitHeight: 620

    function refreshAll() {
        if (MarketPlugin == null || MarketPlugin == undefined) { statusLabel.text = qsTr("Market plugin not loaded."); return }
        MarketPlugin.refresh_rates()
        MarketPlugin.refresh_orderbook(pairBase, pairRel)
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
                for (var i = 0; i < asks.length; ++i) {
                    bookModel.append({ side: qsTr('Ask'), price: String(asks[i].price),
                                       volume: String(asks[i].max_volume) })
                }
                for (var j = 0; j < bids.length; ++j) {
                    bookModel.append({ side: qsTr('Bid'), price: String(bids[j].price),
                                       volume: String(bids[j].max_volume) })
                }
                statusLabel.text = qsTr('%1/%2: %3 asks / %4 bids').arg(pairBase).arg(pairRel).arg(asks.length).arg(bids.length)
            }
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
        spacing: 10

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
            text: ''
        }
        ListView {
            id: bookList
            Layout.fillWidth: true
            Layout.fillHeight: true
            model: ListModel { id: bookModel }
            delegate: ItemDelegate {
                width: bookList.width
                height: 44
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
                text: qsTr('Jetzt tauschen')
                onClicked: {
                    if (MarketPlugin == null) { statusLabel.text = qsTr('plugin missing'); return }
                    MarketPlugin.buy(root.pairBase, root.pairRel, '1', '1')
                }
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
