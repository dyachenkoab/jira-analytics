import QtQuick 2.12
import QtQuick.Controls 2.12
import QtQuick.Layouts 1.12

ColumnLayout {
    id: assigneesTab
    property var api
    property var theme
    spacing: 12

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 96
        color: theme.card
        radius: 16
        border.color: theme.border

        RowLayout {
            anchors.fill: parent
            anchors.margins: 18
            spacing: 16

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 5

                Label {
                    text: qsTr("Assignees performance")
                    color: theme.text
                    font.pixelSize: 24
                    font.bold: true
                    Layout.fillWidth: true
                }

                Text {
                    text: qsTr("Assignees are calculated from sprint tasks within the period window.")
                    color: theme.muted
                    font.pixelSize: 13
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
            }

            ComboBox {
                id: assigneeBoardFilter
                model: api ? ([qsTr("All teams")].concat(api.assigneeBoardNames)) : [qsTr("All teams")]
                Layout.preferredWidth: 200
                Layout.preferredHeight: 42
                currentIndex: 0
                onCurrentIndexChanged: {
                    if (api && currentIndex >= 0) {
                        api.setAssigneeBoardFilter(currentIndex === 0 ? "" : model[currentIndex])
                    }
                }
                background: Rectangle {
                    radius: 10
                    color: "#f8fafc"
                    border.color: theme.border
                }
                contentItem: TextField {
                    text: assigneeBoardFilter.displayText
                    color: theme.text
                    font.pixelSize: 14
                    readOnly: true
                    leftPadding: 12
                    verticalAlignment: Text.AlignVCenter
                    background: Rectangle { color: "transparent" }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: assigneeBoardFilter.popup.open()
                    }
                }
                indicator: Text {
                    text: "▾"
                    color: theme.muted
                    font.pixelSize: 12
                    anchors.right: parent.right
                    anchors.rightMargin: 12
                    anchors.verticalCenter: parent.verticalCenter
                }
            }

            ColumnLayout {
                spacing: 6
                Layout.alignment: Qt.AlignRight

                Text {
                    visible: text !== ""
                    text: {
                        if (api && api.periodFromCache) return qsTr("Cached — click Refresh")
                        if (api && api.periodLastRefresh) return qsTr("Last refresh: %1").arg(api.periodLastRefresh)
                        return ""
                    }
                    color: theme.muted
                    font.pixelSize: 12
                }

                Button {
                    text: api && api.periodChecking ? qsTr("Loading") : qsTr("Refresh analytics")
                    enabled: api && !api.periodChecking
                    Layout.preferredWidth: 170
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
                    onClicked: if (api) api.refreshPeriod()
                }
            }
        }
    }

    GridLayout {
        Layout.fillWidth: true
        columns: assigneesTab.width > 900 ? 4 : 2
        rowSpacing: 10
        columnSpacing: 10

        Repeater {
            model: [
                { label: qsTr("People"), value: api ? api.assigneeKpi.people || 0 : 0, tone: theme.text },
                { label: qsTr("Done SP"), value: api ? api.assigneeKpi.doneSp || "0" : "0", tone: theme.blue },
                { label: qsTr("Avg close %"), value: api ? api.assigneeKpi.avgCompletion || "0%" : "0%", tone: theme.text },
                { label: qsTr("Total spillover"), value: api ? api.assigneeKpi.totalSpillover || 0 : 0, tone: theme.amber }
            ]
            delegate: Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 78
                color: theme.card
                radius: 14
                border.color: theme.border

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 4

                    Label {
                        text: modelData.label
                        color: theme.muted
                        font.pixelSize: 11
                        font.bold: true
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }

                    Label {
                        text: modelData.value
                        color: modelData.tone
                        font.pixelSize: 24
                        font.bold: true
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                }
            }
        }
    }

    ListView {
        id: assigneeList
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        model: api ? api.assigneeModel : null
        spacing: 8

        delegate: Rectangle {
            id: delegateRect
            width: ListView.view.width
            height: expandedColumn.implicitHeight + 24
            color: theme.card
            border.color: expanded ? theme.blue : theme.border
            radius: 14
            property bool expanded: false

            Behavior on height { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
            Behavior on border.color { ColorAnimation { duration: 120 } }

            ColumnLayout {
                id: expandedColumn
                anchors.fill: parent
                anchors.margins: 12
                spacing: 6

                Item {
                    id: headerArea
                    Layout.fillWidth: true
                    Layout.preferredHeight: headerNameRow.height + headerMetricsText.height + 6

                    RowLayout {
                        id: headerNameRow
                        anchors.left: parent.left
                        anchors.right: parent.right
                        Label { text: name; color: theme.text; font.pixelSize: 15; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                        Label { text: team; color: theme.muted; font.pixelSize: 12; elide: Text.ElideRight; Layout.maximumWidth: 130 }
                    }

                    Text {
                        id: headerMetricsText
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: headerNameRow.bottom
                        anchors.topMargin: 6
                        text: qsTr("%1/%2 tasks · done SP %3/%4 · completion %5 · spillover %6→%7 · completed later %8 · backlog %9 · other board %10 · not observed %11 · unknown %12")
                            .arg(done).arg(tasks).arg(doneSp).arg(totalSp).arg(completion).arg(spillover).arg(spilloverCompleted).arg(completedLater).arg(backlog).arg(observedOtherBoard).arg(notObserved).arg(unknown)
                        color: theme.muted
                        font.pixelSize: 12
                        elide: Text.ElideRight
                    }

                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: delegateRect.expanded = !delegateRect.expanded
                    }
                }

                ColumnLayout {
                    visible: delegateRect.expanded
                    spacing: 8

                    Repeater {
                        model: ["done", "spillover", "backlog", "observed_other_board", "not_observed", "unknown"]
                        delegate: ColumnLayout {
                            spacing: 2

                            function itemsFor(cat) {
                                var result = [];
                                if (items) for (var i = 0; i < items.length; i++)
                                    if (items[i].routeCategory === cat) result.push(items[i]);
                                return result;
                            }

                            property var catItems: itemsFor(modelData)

                            Text {
                                text: modelData === "done" ? qsTr("Done") + " (" + catItems.length + ")" :
                                    modelData === "spillover" ? qsTr("Spillover") + " (" + catItems.length + ")" :
                                    modelData === "backlog" ? qsTr("Backlog") + " (" + catItems.length + ")" :
                                    modelData === "observed_other_board" ? qsTr("Other board") + " (" + catItems.length + ")" :
                                    modelData === "not_observed" ? qsTr("Not observed") + " (" + catItems.length + ")" :
                                    qsTr("Unknown") + " (" + catItems.length + ")"
                                color: theme.text
                                font.pixelSize: 13
                                font.bold: true
                            }

                            Repeater {
                                model: catItems
                                delegate: Text {
                                    text: modelData.key + " · " + modelData.summary + " · " + (modelData.originalName ? modelData.originalName + " · " : "") + modelData.points + " SP · " + modelData.sprint + (modelData.completedLater ? " · " + qsTr("Completed later") : "")
                                    color: theme.muted
                                    font.pixelSize: 12
                                    elide: Text.ElideRight
                                    width: parent.width

                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            if (api) api.openIssue(modelData.key)
                                        }
                                    }
                                }
                            }

                            Text {
                                visible: catItems.length === 0
                                text: qsTr("No tasks")
                                color: theme.muted
                                font.pixelSize: 12
                            }

                            Rectangle {
                                visible: modelData !== "unknown" || catItems.length > 0
                                Layout.fillWidth: true
                                Layout.preferredHeight: 1
                                color: theme.border
                            }
                        }
                    }
                }
            }
        }

        Label {
            anchors.centerIn: parent
            visible: assigneeList.count === 0
            text: qsTr("No assignee data loaded")
            color: theme.muted
            font.pixelSize: 15
        }
    }
}
