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
            var b = MarketPlugin.lastBuy
            swapLabel.text = qsTr('Swap request accepted: %1').arg(b && b.uuid ? b.uuid : '-')
        }
        function onError(msg) {
            statusLabel.text = msg
        }
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
        }

        FlatButton {
            Layout.fillWidth: true
            text: qsTr('Close')
            onClicked: root.close()
        }
    }

    Component.onCompleted: refreshAll()
}
