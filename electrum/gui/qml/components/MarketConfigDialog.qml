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
                    text: qsTr('Seed node of the P2P market federation and optional servers per coin. Saved changes take effect after runtime restart.')
                    backgroundColor: constants.darkerDialogBackground
                }

                Label {
                    text: qsTr('KDF Runtime Status: %1').arg(MarketPlugin ? MarketPlugin.kdfPhase : qsTr('not loaded'))
                    color: Material.accentColor
                    font.bold: true
                }

                Label {
                    text: qsTr('Seed Node:')
                }

                TextField {
                    id: nsSeed
                    Layout.fillWidth: true
                    placeholderText: qsTr('e.g. wss://elektron-market.onrender.com')
                }

                Label {
                    text: qsTr('%1 Electrum Servers:').arg((MarketPlugin && MarketPlugin.coins && MarketPlugin.coins[0]) ? MarketPlugin.coins[0] : 'ELEK')
                }

                TextField {
                    id: nsTelek
                    Layout.fillWidth: true
                    placeholderText: qsTr('e.g. electrs.elektron-net.org:50002')
                }

                Label {
                    text: qsTr('%1 Electrum Servers:').arg((MarketPlugin && MarketPlugin.coins && MarketPlugin.coins[1]) ? MarketPlugin.coins[1] : 'BTC')
                }

                TextField {
                    id: nsTbtc
                    Layout.fillWidth: true
                    placeholderText: qsTr('e.g. btc.electrum1.cipig.net:10000')
                }

                Label {
                    text: qsTr('%1 RPC Nodes:').arg((MarketPlugin && MarketPlugin.coins && MarketPlugin.coins[2]) ? MarketPlugin.coins[2] : 'ETH')
                }

                TextField {
                    id: nsSep
                    Layout.fillWidth: true
                    placeholderText: qsTr('e.g. https://ethereum-rpc.publicnode.com')
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
                        text: qsTr('Save')
                        icon.source: '../../icons/confirmed.png'
                        onClicked: {
                            if (MarketPlugin == null || MarketPlugin == undefined) {
                                netHint.text = qsTr('Market plugin not available')
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
                            netHint.text = qsTr('Settings saved - effective after runtime restart.')
                        }
                    }

                    FlatButton {
                        Layout.fillWidth: true
                        text: qsTr('Restore Defaults')
                        icon.source: '../../icons/undo.png'
                        onClicked: {
                            if (MarketPlugin == null || MarketPlugin == undefined) {
                                netHint.text = qsTr('Market plugin not available')
                                return
                            }
                            MarketPlugin.reset_net_settings()
                            nsSeed.text = String(MarketPlugin.defaultSeednode || 'wss://elektron-market.onrender.com')
                            var defs = MarketPlugin.defaultCoinServers || {}
                            var c0 = (MarketPlugin.coins && MarketPlugin.coins[0]) ? MarketPlugin.coins[0] : 'ELEK'
                            var c1 = (MarketPlugin.coins && MarketPlugin.coins[1]) ? MarketPlugin.coins[1] : 'BTC'
                            var c2 = (MarketPlugin.coins && MarketPlugin.coins[2]) ? MarketPlugin.coins[2] : 'ETH'
                            nsTelek.text = String(defs[c0] || '')
                            nsTbtc.text = String(defs[c1] || '')
                            nsSep.text = String(defs[c2] || '')
                            netHint.text = qsTr('Standardwerte wiederhergestellt. Klicken Sie auf Speichern.')
                        }
                    }

                    FlatButton {
                        Layout.fillWidth: true
                        text: qsTr('Restart Runtime')
                        icon.source: '../../icons/refresh.png'
                        onClicked: {
                            if (MarketPlugin == null || MarketPlugin == undefined) {
                                netHint.text = qsTr('Market plugin not available')
                                return
                            }
                            netHint.text = qsTr('Runtime restarting...')
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
                text: qsTr('Close')
                icon.source: '../../icons/closebutton.png'
                onClicked: rootItem.close()
            }
        }
    }

    Component.onCompleted: {
        if (MarketPlugin != null && MarketPlugin != undefined) {
            var defSeed = String(MarketPlugin.defaultSeednode || 'wss://elektron-market.onrender.com')
            var curSeed = String(MarketPlugin.netSeednode || '')
            nsSeed.text = curSeed !== '' ? curSeed : defSeed

            var cs = MarketPlugin.netCoinServers || {}
            var defs = MarketPlugin.defaultCoinServers || {}
            var c0 = (MarketPlugin.coins && MarketPlugin.coins[0]) ? MarketPlugin.coins[0] : 'ELEK'
            var c1 = (MarketPlugin.coins && MarketPlugin.coins[1]) ? MarketPlugin.coins[1] : 'BTC'
            var c2 = (MarketPlugin.coins && MarketPlugin.coins[2]) ? MarketPlugin.coins[2] : 'ETH'

            nsTelek.text = String(cs[c0] || defs[c0] || '')
            nsTbtc.text = String(cs[c1] || defs[c1] || '')
            nsSep.text = String(cs[c2] || defs[c2] || '')
        }
    }
}
