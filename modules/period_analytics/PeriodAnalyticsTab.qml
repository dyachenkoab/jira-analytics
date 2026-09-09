import QtQuick 2.12
import QtQuick.Controls 2.12
import QtQuick.Layouts 1.12

ScrollView {
    id: periodAnalyticsTab
    property var api
    property var theme
    ScrollBar.vertical.policy: ScrollBar.AsNeeded

    ColumnLayout {
        spacing: 12
        width: periodAnalyticsTab.width
        height: Math.max(implicitHeight, periodAnalyticsTab.height)

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
                        text: qsTr("Period analytics")
                        color: theme.text
                        font.pixelSize: 24
                        font.bold: true
                        Layout.fillWidth: true
                    }

                    Text {
                        text: api && api.selectedTeam ? qsTr("Team: %1").arg(api.selectedTeam) : qsTr("All teams")
                        color: api && api.selectedTeam ? theme.blue : theme.muted
                        font.pixelSize: 13
                        font.bold: api && api.selectedTeam
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }

                    Text {
                        text: api && api.periodStatusText.length > 0 ? api.periodStatusText : qsTr("Period, boards and Jira fields are configured in YAML.")
                        color: theme.muted
                        font.pixelSize: 13
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
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
            columns: periodAnalyticsTab.width > 900 ? 6 : 2
            rowSpacing: 10
            columnSpacing: 10

            Repeater {
                model: [
                    { label: qsTr("Done points"), value: api ? api.periodKpi.donePoints || "0" : "0", tone: theme.blue, wide: true },
                    { label: qsTr("Avg velocity"), value: api ? api.periodKpi.avgVelocity || "0" : "0", tone: theme.text, wide: true },
                    { label: qsTr("Completion"), value: api ? api.periodKpi.completion || "0%" : "0%", tone: theme.text, wide: false },
                    { label: qsTr("Not done"), value: api ? api.periodKpi.notDone || 0 : 0, tone: theme.amber, wide: false },
                    { label: qsTr("Spillover"), value: api ? api.periodKpi.spillover || 0 : 0, tone: theme.text, wide: false },
                    { label: qsTr("→ completed"), value: api ? api.periodKpi.spilloverCompleted || 0 : 0, tone: theme.blue, wide: false },
                    { label: qsTr("Completed later"), value: api ? api.periodKpi.completedLater || 0 : 0, tone: theme.blue, wide: false },
                    { label: qsTr("Backlog"), value: api ? api.periodKpi.backlog || 0 : 0, tone: theme.text, wide: false },
                    { label: qsTr("Other board"), value: api ? api.periodKpi.observedOtherBoard || 0 : 0, tone: theme.muted, wide: false },
                    { label: qsTr("Not observed"), value: api ? api.periodKpi.notObserved || 0 : 0, tone: theme.muted, wide: false },
                    { label: qsTr("Unknown"), value: api ? api.periodKpi.unknown || 0 : 0, tone: theme.amber, wide: false },
                    { label: qsTr("Unestimated"), value: api ? api.periodKpi.unestimated || 0 : 0, tone: theme.red, wide: false },
                    { label: qsTr("Removed"), value: api ? api.periodKpi.removed || 0 : 0, tone: theme.red, wide: false },
                    { label: qsTr("Flow efficiency"), value: api ? api.periodKpi.flowEfficiency || "0%" : "0%", tone: theme.blue, wide: false }
                ]
                delegate: Rectangle {
                    Layout.fillWidth: true
                    Layout.columnSpan: modelData.wide ? 2 : 1
                    Layout.preferredHeight: modelData.wide ? 92 : 78
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
                            font.pixelSize: modelData.wide ? 32 : 24
                            font.bold: true
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }
                    }
                }
            }
        }

        Rectangle {
            id: metricGuide
            Layout.fillWidth: true
            color: theme.card
            radius: 14
            border.color: theme.border
            clip: true

            property bool expanded: false

            Layout.preferredHeight: expanded
                ? guideContent.implicitHeight + guideHeaderRow.implicitHeight + 24
                : guideHeaderRow.implicitHeight + 24
            Behavior on Layout.preferredHeight {
                NumberAnimation { duration: 200; easing.type: Easing.OutCubic }
            }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 10
                spacing: 8

                Item {
                    id: guideHeader
                    Layout.fillWidth: true
                    Layout.preferredHeight: guideHeaderRow.implicitHeight

                    RowLayout {
                        id: guideHeaderRow
                        anchors.fill: parent

                        Label {
                            text: qsTr("Metric guide")
                            color: theme.text
                            font.pixelSize: 13
                            font.bold: true
                            Layout.fillWidth: true
                        }

                        Label {
                            text: metricGuide.expanded ? "▾" : "▸"
                            color: theme.muted
                            font.pixelSize: 11
                        }
                    }

                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: metricGuide.expanded = !metricGuide.expanded
                    }
                }

                ColumnLayout {
                    id: guideContent
                    visible: metricGuide.expanded
                    spacing: 4

                    Repeater {
                        model: [
                            qsTr("Done — task status at sprint cutoff."),
                            qsTr("Not done — status not in the done list at cutoff."),
                            qsTr("Spillover — not done tasks found in later sprints."),
                            qsTr("Completed later — a done transition observed after sprint cutoff."),
                            qsTr("Backlog — not done tasks currently in backlog."),
                            qsTr("Other board — observed in a later sprint on another configured board."),
                            qsTr("Not observed — absent from successfully loaded later sprints and backlog query."),
                            qsTr("Unknown — data sources were incomplete."),
                            qsTr("Removed — removed from sprint report."),
                        ]
                        delegate: Text {
                            text: modelData
                            color: theme.muted
                            font.pixelSize: 11
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 360
            spacing: 12

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: theme.card
                radius: 14
                border.color: theme.border

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            text: qsTr("Teams")
                            color: theme.text
                            font.pixelSize: 18
                            font.bold: true
                            Layout.fillWidth: true

                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: if (api) api.selectTeam("")
                            }
                        }
                        Label { text: String(periodBoardList.count); color: theme.muted; font.pixelSize: 13 }
                    }

                    ListView {
                        id: periodBoardList
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: api ? api.periodBoardModel : null
                        spacing: 8

                        delegate: Rectangle {
                            width: ListView.view.width
                            height: 82
                            color: mouse.containsMouse ? "#fbfcfe" : theme.card
                            radius: 12
                            border.color: (api && api.selectedTeam === team) ? theme.blue : (mouse.containsMouse ? theme.blue : theme.border)
                            border.width: (api && api.selectedTeam === team) ? 2 : 1

                            Behavior on border.color { ColorAnimation { duration: 120 } }
                            Behavior on color { ColorAnimation { duration: 120 } }

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 12
                                spacing: 7

                                RowLayout {
                                    Layout.fillWidth: true
                                    Label { text: team; color: theme.text; font.pixelSize: 15; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                                }

                                Text {
                                    text: qsTr("%1 / %2 SP · velocity %3 · completion %4 · not done %5 · spillover %6→%7 · backlog %8 · other board %9 · not observed %10 · unknown %11 · removed %12")
                                        .arg(donePoints).arg(totalPoints).arg(avgVelocity).arg(completion).arg(notDone).arg(spillover).arg(spilloverCompleted).arg(backlog).arg(observedOtherBoard).arg(notObserved).arg(unknown).arg(removed)
                                    color: theme.muted
                                    font.pixelSize: 12
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                            }

                            MouseArea {
                                id: mouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    if (api) {
                                        api.selectTeam(api.selectedTeam === team ? "" : team)
                                    }
                                }
                            }
                        }

                        Label {
                            Layout.fillWidth: true
                            horizontalAlignment: Text.AlignHCenter
                            Layout.alignment: Qt.AlignVCenter
                            visible: periodBoardList.count === 0 && !(api && api.periodChecking)
                            text: qsTr("No team data loaded")
                            color: theme.muted
                            font.pixelSize: 15
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: theme.card
                radius: 14
                border.color: theme.border

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: qsTr("Sprints"); color: theme.text; font.pixelSize: 18; font.bold: true; Layout.fillWidth: true }
                        Label { text: String(periodSprintList.count); color: theme.muted; font.pixelSize: 13 }
                    }

                    ListView {
                        id: periodSprintList
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        visible: api && api.periodSprintModel ? api.periodSprintModel.count > 0 : false
                        model: api ? api.periodSprintModel : null
                        spacing: 8

                        delegate: Rectangle {
                            width: ListView.view.width
                            height: 88
                            color: mouse2.containsMouse ? "#fbfcfe" : theme.card
                            radius: 12
                            border.color: mouse2.containsMouse ? theme.blue : theme.border

                            Behavior on border.color { ColorAnimation { duration: 120 } }
                            Behavior on color { ColorAnimation { duration: 120 } }

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 12
                                spacing: 6

                                RowLayout {
                                    Layout.fillWidth: true
                                    Label { text: sprint; color: theme.text; font.pixelSize: 15; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                                    Label { text: team; color: theme.muted; font.pixelSize: 12; elide: Text.ElideRight; Layout.maximumWidth: 130 }
                                }

                                Text { text: dates; color: theme.muted; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true }

                                Text {
                                    text: qsTr("done %1/%2 · done SP %3 · completion %4 · not done %5 · spillover %6→%7 · backlog %8 · other board %9 · not observed %10 · unknown %11 · removed %12")
                                        .arg(done).arg(total).arg(points).arg(completion).arg(notDone).arg(spillover).arg(spilloverCompleted).arg(backlog).arg(observedOtherBoard).arg(notObserved).arg(unknown).arg(removed)
                                    color: theme.muted
                                    font.pixelSize: 12
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                            }

                            MouseArea {
                                id: mouse2
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    if (api) api.openSprintReport(boardId, sprintId)
                                }
                            }
                        }
                    }

                    Label {
                        Layout.fillWidth: true
                        horizontalAlignment: Text.AlignHCenter
                        Layout.alignment: Qt.AlignVCenter
                        visible: periodSprintList.count === 0 && !(api && api.periodChecking)
                        text: qsTr("No sprint data loaded")
                        color: theme.muted
                        font.pixelSize: 15
                    }
                }
            }
        }

        Rectangle {
            visible: api && api.periodErrorsText.length > 0
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? 88 : 0
            color: theme.redSoft
            border.color: "#fecaca"

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 10
                spacing: 4

                Label { text: qsTr("Analytics errors"); color: theme.red; font.bold: true; font.pixelSize: 13 }

                TextArea {
                    text: api ? api.periodErrorsText : ""
                    readOnly: true
                    wrapMode: TextEdit.Wrap
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    background: Rectangle { color: "transparent" }
                }
            }
        }
    }
}
