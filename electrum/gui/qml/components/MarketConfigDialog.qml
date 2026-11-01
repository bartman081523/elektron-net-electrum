import QtQuick
import QtQuick.Layouts
import QtQuick.Controls
import QtQuick.Controls.Material

import org.electrum 1.0

import "controls"

ElDialog {
    id: rootItem

    title: qsTr('Market P2P Network')
    iconSource: Qt.resolvedUrl('../../icons/network.png')

    width: parent.width
    height: parent.height

    padding: 0

    ColumnLayout {
        width: parent.width
        height: parent.height
        spacing: 0

        Flickable {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.leftMargin: constants.paddingLarge
            Layout.rightMargin: constants.paddingLarge
            Layout.bottomMargin: constants.paddingLarge

            contentHeight: contentCol.height
            clip: true
            interactive: height < contentHeight

            ColumnLayout {
                id: contentCol
                width: parent.width
                spacing: constants.paddingMedium

                InfoTextArea {
                    Layout.fillWidth: true
                    compact: true
                    text: qsTr('Seednode der P2P-Market-Federation und optionale Server je Coin (leer = Standard-Server). Gespeicherte Änderungen werden nach Neustart der Runtime wirksam.')
                    backgroundColor: constants.darkerDialogBackground
                }

                Label {
                    text: qsTr('KDF Runtime Status: %1').arg(MarketPlugin ? MarketPlugin.kdfPhase : qsTr('nicht geladen'))
                    color: Material.accentColor
                    font.bold: true
                }

                Label {
                    text: qsTr('Seednode:')
                }

                TextField {
                    id: nsSeed
                    Layout.fillWidth: true
                    placeholderText: qsTr('z.B. wss://elektron-market.onrender.com')
                }

                Label {
                    text: qsTr('%1 Server:').arg((MarketPlugin && MarketPlugin.coins && MarketPlugin.coins[0]) ? MarketPlugin.coins[0] : 'ELEK')
                }

                TextField {
                    id: nsTelek
                    Layout.fillWidth: true
                    placeholderText: qsTr('z.B. 100.64.0.2:50002 (leer = Standard)')
                }

                Label {
                    text: qsTr('%1 Server:').arg((MarketPlugin && MarketPlugin.coins && MarketPlugin.coins[1]) ? MarketPlugin.coins[1] : 'BTC')
                }

                TextField {
                    id: nsTbtc
                    Layout.fillWidth: true
                    placeholderText: qsTr('z.B. 100.64.0.2:50002 (leer = Standard)')
                }

                Label {
                    text: qsTr('%1 RPC:').arg((MarketPlugin && MarketPlugin.coins && MarketPlugin.coins[2]) ? MarketPlugin.coins[2] : 'ETH')
                }

                TextField {
                    id: nsSep
                    Layout.fillWidth: true
                    placeholderText: qsTr('z.B. https://ethereum-sepolia-rpc.publicnode.com (leer = Standard)')
                }

                Label {
                    id: netHint
                    Layout.fillWidth: true
                    font.pixelSize: 11
                    wrapMode: Text.WrapAnywhere
                    color: constants.mutedForeground
                    text: ''
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: constants.paddingSmall

                    FlatButton {
                        Layout.fillWidth: true
                        text: qsTr('Speichern')
                        icon.source: '../../icons/confirmed.png'
                        onClicked: {
                            if (MarketPlugin == null || MarketPlugin == undefined) {
                                netHint.text = qsTr('Market-Plugin nicht verfügbar')
                                return
                            }
                            var c0 = (MarketPlugin.coins && MarketPlugin.coins[0]) ? MarketPlugin.coins[0] : 'ELEK'
                            var c1 = (MarketPlugin.coins && MarketPlugin.coins[1]) ? MarketPlugin.coins[1] : 'BTC'
                            var c2 = (MarketPlugin.coins && MarketPlugin.coins[2]) ? MarketPlugin.coins[2] : 'ETH'
                            var sMap = {}
                            sMap[c0] = nsTelek.text.trim()
                            sMap[c1] = nsTbtc.text.trim()
                            sMap[c2] = nsSep.text.trim()
                            MarketPlugin.save_net_settings(nsSeed.text.trim(), sMap)
                            netHint.text = qsTr('Gespeichert – wirksam nach Runtime-Neustart.')
                        }
                    }

                    FlatButton {
                        Layout.fillWidth: true
                        text: qsTr('Runtime neu starten')
                        icon.source: '../../icons/refresh.png'
                        onClicked: {
                            if (MarketPlugin == null || MarketPlugin == undefined) {
                                netHint.text = qsTr('Market-Plugin nicht verfügbar')
                                return
                            }
                            netHint.text = qsTr('Runtime wird neu gestartet ...')
                            MarketPlugin.restart_runtime()
                        }
                    }
                }
            }
        }

        DialogButtonContainer {
            Layout.fillWidth: true

            FlatButton {
                Layout.fillWidth: true
                text: qsTr('Schließen')
                icon.source: '../../icons/closebutton.png'
                onClicked: rootItem.close()
            }
        }
    }

    Component.onCompleted: {
        if (MarketPlugin != null && MarketPlugin != undefined) {
            nsSeed.text = String(MarketPlugin.netSeednode || '')
            var cs = MarketPlugin.netCoinServers || {}
            var c0 = (MarketPlugin.coins && MarketPlugin.coins[0]) ? MarketPlugin.coins[0] : 'ELEK'
            var c1 = (MarketPlugin.coins && MarketPlugin.coins[1]) ? MarketPlugin.coins[1] : 'BTC'
            var c2 = (MarketPlugin.coins && MarketPlugin.coins[2]) ? MarketPlugin.coins[2] : 'ETH'
            nsTelek.text = String(cs[c0] || cs.ELEK || '')
            nsTbtc.text = String(cs[c1] || cs.BTC || '')
            nsSep.text = String(cs[c2] || cs.ETH || '')
        }
    }
}
