import QtQuick 2.12
import QtQuick.Controls 2.12
import QtQuick.Layouts 1.12

ColumnLayout {
    id: targetEndTab
    property var api
    property var theme
    spacing: 12

    function riskColor(kind) { return kind === "overdue" ? theme.red : theme.amber }
    function riskBg(kind) { return kind === "overdue" ? theme.redSoft : theme.amberSoft }
    function riskText(kind, days) {
        return kind === "overdue" ? qsTr("Overdue %1 days").arg(Math.abs(days)) : qsTr("%1 days left").arg(days)
    }

    RowLayout {
        Layout.fillWidth: true
        Layout.preferredHeight: 76
        spacing: 12

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 76
            color: theme.card
            radius: 14
            border.color: theme.border

            RowLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 12

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Label { text: qsTr("At risk"); color: theme.muted; font.pixelSize: 12 }
                    Label { text: tasksList.count; color: theme.text; font.pixelSize: 28; font.bold: true }
                }
                Rectangle { Layout.preferredWidth: 1; Layout.fillHeight: true; color: theme.border }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Label { text: qsTr("Status"); color: theme.muted; font.pixelSize: 12 }
                    Label { text: api && api.checking ? qsTr("Checking") : qsTr("Ready"); color: theme.text; font.pixelSize: 18; font.bold: true }
                }

                Button {
                    Layout.alignment: Qt.AlignRight
                    text: api && api.checking ? qsTr("Checking") : qsTr("Check")
                    enabled: api && !api.checking
                    Layout.preferredWidth: 128
                    Layout.preferredHeight: 40
                    font.pixelSize: 14
                    font.bold: true
                    contentItem: Text {
                        text: parent.text
                        color: parent.enabled ? "#ffffff" : "#98a2b3"
                        font: parent.font
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        radius: 10
                        color: parent.enabled ? (parent.down ? "#1d4ed8" : theme.blue) : "#eef1f5"
                    }
                    onClicked: if (api) api.refreshTarget()
                }
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        spacing: 14

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 10

            ListView {
                id: tasksList
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 10
                rightMargin: 10
                model: api ? api.taskModel : null
                ScrollBar.vertical: ScrollBar {
                    id: taskScrollBar
                    policy: ScrollBar.AsNeeded
                    width: 8

                    background: Rectangle {
                        color: "transparent"
                    }

                    contentItem: Rectangle {
                        implicitWidth: 6
                        radius: 3
                        color: taskScrollBar.pressed ? "#98a2b3" : "#cbd5e1"
                        opacity: taskScrollBar.active ? 1 : 0.55
                    }
                }

                delegate: Rectangle {
                    width: ListView.view.width - tasksList.rightMargin
                    height: 104
                    clip: true
                    color: mouse.containsMouse ? "#fbfcfe" : theme.card
                    border.color: mouse.containsMouse ? theme.blue : theme.border
                    radius: 14

                    Behavior on border.color { ColorAnimation { duration: 120 } }
                    Behavior on color { ColorAnimation { duration: 120 } }

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 14

                        Rectangle {
                            Layout.preferredWidth: 5
                            Layout.fillHeight: true
                            radius: 3
                            color: targetEndTab.riskColor(kind)
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            spacing: 6

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8

                                Label {
                                    text: key
                                    color: theme.text
                                    font.pixelSize: 17
                                    font.bold: true
                                }

                                Rectangle {
                                    radius: 999
                                    color: targetEndTab.riskBg(kind)
                                    Layout.preferredHeight: 26
                                    Layout.preferredWidth: badgeText.implicitWidth + 18

                                    Text {
                                        id: badgeText
                                        anchors.centerIn: parent
                                        text: targetEndTab.riskText(kind, days)
                                        color: targetEndTab.riskColor(kind)
                                        font.pixelSize: 12
                                        font.bold: true
                                    }
                                }

                                Item { Layout.fillWidth: true }

                                Label {
                                    text: target_end
                                    color: theme.muted
                                    font.pixelSize: 13
                                }
                            }

                            Text {
                                text: summary
                                color: theme.text
                                font.pixelSize: 15
                                wrapMode: Text.NoWrap
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }

                            Text {
                                text: qsTr("Status: %1 \u00b7 Assignee: %2").arg(status).arg(assignee)
                                color: theme.muted
                                font.pixelSize: 13
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                    }

                    MouseArea {
                        id: mouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: if (api) api.openIssue(key)
                    }
                }

                Label {
                    anchors.centerIn: parent
                    visible: tasksList.count === 0 && !(api && api.checking)
                    text: qsTr("No issues at risk")
                    color: theme.muted
                    font.pixelSize: 18
                }
            }

            Rectangle {
                visible: api && api.errorsText.length > 0
                Layout.fillWidth: true
                Layout.preferredHeight: visible ? 96 : 0
                color: theme.redSoft
                border.color: "#fecaca"

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 6

                    Label { text: qsTr("Check errors"); color: theme.red; font.bold: true; font.pixelSize: 14 }
                    TextArea {
                        text: api ? api.errorsText : ""
                        readOnly: true
                        wrapMode: TextEdit.Wrap
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        background: Rectangle { color: "transparent" }
                    }
                }
            }
        }

        Rectangle {
            id: sidePanel
            property bool editorOpen: false
            Layout.preferredWidth: 286
            Layout.fillHeight: true
            color: theme.card
            radius: 14
            border.color: theme.border

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 12

                Label {
                    text: qsTr("Jira sources")
                    color: theme.text
                    font.pixelSize: 18
                    font.bold: true
                    Layout.fillWidth: true
                }

                Text {
                    text: qsTr("Issues, links or JQL, one per line.")
                    color: theme.muted
                    font.pixelSize: 13
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                Item {
                    Layout.fillWidth: true
                    Layout.preferredHeight: height
                    height: sidePanel.editorOpen ? Math.max(0, sidePanel.height - 158) : 0
                    clip: true

                    Behavior on height {
                        NumberAnimation { duration: 180; easing.type: Easing.OutCubic }
                    }

                    ScrollView {
                        anchors.fill: parent
                        visible: parent.height > 1

                        TextArea {
                            id: itemsEditor
                            text: api ? api.itemsText : ""
                            placeholderText: qsTr("Issues, links or JQL")
                            wrapMode: TextEdit.Wrap
                        }
                    }
                }

                Button {
                    id: editItemsButton
                    text: sidePanel.editorOpen ? qsTr("Update and hide") : qsTr("Edit list")
                    font.pixelSize: 14
                    Layout.fillWidth: true
                    Layout.preferredHeight: 40
                    contentItem: Text {
                        text: editItemsButton.text
                        color: editItemsButton.enabled ? theme.text : "#98a2b3"
                        font: editItemsButton.font
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        radius: 10
                        color: editItemsButton.down ? "#e8edf5" : "#f8fafc"
                        border.color: theme.border
                    }
                    onClicked: {
                        if (sidePanel.editorOpen && api) {
                            api.saveItems(itemsEditor.text)
                        }
                        sidePanel.editorOpen = !sidePanel.editorOpen
                    }
                }

                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: theme.border }

                Text {
                    text: qsTr("Click on an issue to open it in the browser.")
                    color: theme.muted
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
            }
        }
    }
}
