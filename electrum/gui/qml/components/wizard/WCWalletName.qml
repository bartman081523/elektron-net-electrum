import QtQuick
import QtQuick.Layouts
import QtQuick.Controls

import org.electrum 1.0

WizardComponent {
    valid: Daemon.isValidWalletName(wallet_name.text)

    function apply() {
        wizard_data['wallet_name'] = wallet_name.text
        wizard_data['testnet'] = rbTestnet.checked
        if (Config.isTestnet !== rbTestnet.checked) {
            Config.setNetwork(rbTestnet.checked)
        }
    }

    ColumnLayout {
        width: parent.width

        Label {
            text: qsTr('Wallet name')
        }

        TextField {
            id: wallet_name
            Layout.fillWidth: true
            focus: true
            text: Daemon.suggestWalletName()
            inputMethodHints: Qt.ImhNoPredictiveText
        }

        Label {
            text: qsTr('Network')
            Layout.topMargin: constants.paddingMedium
        }

        ButtonGroup {
            id: netGroup
        }

        RowLayout {
            Layout.fillWidth: true
            RadioButton {
                id: rbMainnet
                text: qsTr('Elektron Mainnet')
                ButtonGroup.group: netGroup
                checked: !Config.isTestnet
                onCheckedChanged: {
                    if (checked && Config.isTestnet) {
                        Config.setNetwork(false)
                        wallet_name.text = Daemon.suggestWalletName()
                    }
                }
            }
            RadioButton {
                id: rbTestnet
                text: qsTr('Elektron Testnet')
                ButtonGroup.group: netGroup
                checked: Config.isTestnet
                onCheckedChanged: {
                    if (checked && !Config.isTestnet) {
                        Config.setNetwork(true)
                        wallet_name.text = Daemon.suggestWalletName()
                    }
                }
            }
        }
    }

    Component.onCompleted: {
        wallet_name.forceActiveFocus()
    }
}
