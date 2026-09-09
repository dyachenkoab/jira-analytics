import QtQuick 2.12
import QtQuick.Controls 2.12
import QtQuick.Layouts 1.12

ScrollView {
    id: backlogTab
    property var api
    property var theme
    ScrollBar.vertical.policy: ScrollBar.AsNeeded

    ColumnLayout {
        spacing: 12
        width: backlogTab.width
        height: Math.max(implicitHeight, backlogTab.height)

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 124
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
                        text: qsTr("Backlog health")
                        color: theme.text
                        font.pixelSize: 24
                        font.bold: true
                        Layout.fillWidth: true
                    }

                    Text {
                        text: api && api.periodStatusText.length > 0 ? api.periodStatusText : qsTr("Open tasks aging: 30+/90+/180+/365+ days. Stale = no updates 90+ days.")
                        color: theme.muted
                        font.pixelSize: 13
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }

                    RowLayout {
                        spacing: 8
                        Layout.fillWidth: true

                        Label { text: qsTr("Filter"); color: theme.muted; font.pixelSize: 12; Layout.alignment: Qt.AlignVCenter }

                        Button {
                            id: staleBtn
                            text: qsTr("Stale")
                            checkable: true
                            checked: api && api.backlogSort === "stale"
                            Layout.preferredHeight: 28
                            Layout.preferredWidth: implicitContentWidth + 20
                            font.pixelSize: 12
                            font.bold: true
                            contentItem: Text {
                                text: parent.text
                                color: parent.checked ? "#ffffff" : theme.muted
                                font: parent.font
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                            background: Rectangle {
                                radius: 8
                                color: parent.checked ? "#dc2626" : (parent.hovered ? "#f1f5f9" : "transparent")
                                border.color: parent.checked ? "transparent" : theme.border
                                border.width: 1
                            }
                            onClicked: if (api) api.setBacklogSort("stale")
                        }

                        Button {
                            id: aging365Btn
                            text: qsTr("365+")
                            checkable: true
                            checked: api && api.backlogSort === "aging365"
                            Layout.preferredHeight: 28
                            Layout.preferredWidth: implicitContentWidth + 20
                            font.pixelSize: 12
                            font.bold: true
                            contentItem: Text {
                                text: parent.text
                                color: parent.checked ? "#ffffff" : theme.muted
                                font: parent.font
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                            background: Rectangle {
                                radius: 8
                                color: parent.checked ? "#b91c1c" : (parent.hovered ? "#f1f5f9" : "transparent")
                                border.color: parent.checked ? "transparent" : theme.border
                                border.width: 1
                            }
                            onClicked: if (api) api.setBacklogSort("aging365")
                        }

                        Button {
                            id: forgottenBtn
                            text: qsTr("Forgotten")
                            checkable: true
                            checked: api && api.backlogSort === "forgotten"
                            Layout.preferredHeight: 28
                            Layout.preferredWidth: implicitContentWidth + 20
                            font.pixelSize: 12
                            font.bold: true
                            contentItem: Text {
                                text: parent.text
                                color: parent.checked ? "#ffffff" : theme.muted
                                font: parent.font
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                            background: Rectangle {
                                radius: 8
                                color: parent.checked ? "#d97706" : (parent.hovered ? "#f1f5f9" : "transparent")
                                border.color: parent.checked ? "transparent" : theme.border
                                border.width: 1
                            }
                            onClicked: if (api) api.setBacklogSort("forgotten")
                        }
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

        ListView {
            id: backlogHealthList
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 300
            clip: true
            visible: backlogHealthList.count > 0
            model: api ? api.backlogHealthModel : null
            spacing: 8

            delegate: Rectangle {
                id: bhDelegate
                width: ListView.view.width
                color: theme.card
                border.color: expanded ? theme.blue : theme.border
                border.width: expanded ? 2 : 1
                radius: 12
                property bool expanded: false
                property var taskItems: items

                Component.onCompleted: height = agingRow.implicitHeight + 28

                onExpandedChanged: {
                    if (!expanded) {
                        height = agingRow.implicitHeight + 28
                    } else {
                        Qt.callLater(function() {
                            height = expandedCol.implicitHeight + 20
                        })
                    }
                }

                Behavior on height { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
                Behavior on border.color { ColorAnimation { duration: 120 } }

                ColumnLayout {
                    id: expandedCol
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 10
                    spacing: 8

                    onImplicitHeightChanged: {
                        if (bhDelegate.expanded) bhDelegate.height = implicitHeight + 20
                    }

                    Item {
                        id: headerRow
                        Layout.fillWidth: true
                        Layout.preferredHeight: agingRow.implicitHeight + 4

                        RowLayout {
                            id: agingRow
                            anchors.left: parent.left
                            anchors.right: parent.right
                            spacing: 12

                            Label { text: team; color: theme.text; font.pixelSize: 14; font.bold: true; Layout.preferredWidth: 120; elide: Text.ElideRight }

                            Rectangle { color: "#e0f2fe"; radius: 4; Layout.preferredWidth: bp30.width + 12; height: 22
                                Label { id: bp30; anchors.centerIn: parent; text: "30+ " + aging30; font.pixelSize: 11; color: "#0369a1" } }
                            Rectangle { color: "#fef3c7"; radius: 4; Layout.preferredWidth: bp90.width + 12; height: 22
                                Label { id: bp90; anchors.centerIn: parent; text: "90+ " + aging90; font.pixelSize: 11; color: "#a16207" } }
                            Rectangle { color: "#ffedd5"; radius: 4; Layout.preferredWidth: bp180.width + 12; height: 22
                                Label { id: bp180; anchors.centerIn: parent; text: "180+ " + aging180; font.pixelSize: 11; color: "#b45309" } }
                            Rectangle { color: "#fee2e2"; radius: 4; Layout.preferredWidth: bp365.width + 12; height: 22
                                Label { id: bp365; anchors.centerIn: parent; text: "365+ " + aging365; font.pixelSize: 11; color: "#b91c1c" } }

                            Label { text: "Avg " + agingAvg + "d"; color: theme.muted; font.pixelSize: 12; Layout.fillWidth: true }
                            Label { text: qsTr("Stale") + ": " + staleCount; color: staleCount > 0 ? "#dc2626" : theme.muted; font.pixelSize: 13; font.bold: true }
                        }

                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: bhDelegate.expanded = !bhDelegate.expanded
                        }
                    }

                    Loader {
                        id: drillDownLoader
                        active: bhDelegate.expanded
                        sourceComponent: drillDownComponent
                        Layout.fillWidth: true
                        onItemChanged: if (item) item.taskItems = Qt.binding(function() { return taskItems; })
                    }
                }
            }
        }

        Component {
            id: drillDownComponent
            ColumnLayout {
                spacing: 8
                property var taskItems: []
                property bool flatMode: api && (api.backlogAgeFilter === "stale" || api.backlogAgeFilter === "365+" || api.backlogAgeFilter === "forgotten")

                function filteredItems() {
                    var result = [];
                    var ageFilter = api ? api.backlogAgeFilter : "";
                    if (taskItems) for (var i = 0; i < taskItems.length; i++) {
                        var item = taskItems[i];
                        if (ageFilter === "stale" && (item.sinceUpdate || 0) < 90) continue;
                        if (ageFilter === "365+" && item.age < 365) continue;
                        if (ageFilter === "forgotten") {
                            if (!(api && api.isForgotten(item.status, item.sinceUpdate || 0))) continue;
                        }
                        result.push(item);
                    }
                    return result;
                }

                Repeater {
                    model: flatMode ? filteredItems() : []
                    delegate: Text {
                        text: modelData.key + " · " + modelData.summary + " · " + modelData.status + " · " + modelData.age + "d"
                        color: theme.muted
                        font.pixelSize: 12
                        elide: Text.ElideRight
                        width: parent ? parent.width : 0

                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                if (api) api.openIssue(modelData.key)
                            }
                        }
                    }
                }

                Repeater {
                    model: flatMode ? [] : ["365+", "180+", "90+", "30+"]
                    delegate: ColumnLayout {
                        spacing: 2

                        function itemsForBucket(bucket) {
                            var result = [];
                            var parentItems = parent.taskItems;
                            var threshold = bucket === "365+" ? 365 : (bucket === "180+" ? 180 : (bucket === "90+" ? 90 : 30));
                            if (parentItems) for (var i = 0; i < parentItems.length; i++) {
                                var a = parentItems[i].age;
                                if (a < threshold) continue;
                                result.push(parentItems[i]);
                            }
                            return result;
                        }

                        property var bucketItems: itemsForBucket(modelData)

                        Text {
                            visible: bucketItems.length > 0
                            text: modelData + " (" + bucketItems.length + ")"
                            color: modelData === "365+" ? "#b91c1c" : (modelData === "180+" ? "#b45309" : (modelData === "90+" ? "#a16207" : "#0369a1"))
                            font.pixelSize: 12; font.bold: true
                        }

                        Repeater {
                            model: bucketItems
                            delegate: Text {
                                text: modelData.key + " · " + modelData.summary + " · " + modelData.status + " · " + modelData.age + "d"
                                color: theme.muted
                                font.pixelSize: 12
                                elide: Text.ElideRight
                                width: parent ? parent.width : 0

                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        if (api) api.openIssue(modelData.key)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        Label {
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            Layout.alignment: Qt.AlignVCenter
            visible: backlogHealthList.count === 0 && !(api && api.periodChecking)
            text: qsTr("No backlog health data loaded")
            color: theme.muted
            font.pixelSize: 15
        }
    }
}
