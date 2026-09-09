import QtQuick 2.12
import QtQuick.Controls 2.12
import QtQuick.Layouts 1.12

ColumnLayout {
    id: chartsTab
    property var api
    property var theme
    Layout.fillWidth: true
    Layout.fillHeight: true
    spacing: 12

    property var selectedTeams: []
    readonly property var palette: ["#2563eb", "#d97706", "#059669", "#dc2626", "#7c3aed", "#0891b2", "#c2410c", "#475467"]

    function sprintModel() {
        return api && api.chartSlotsModel ? api.chartSlotsModel : null
    }

    function allSprints() {
        var model = sprintModel()
        var result = []
        if (!model) return result
        for (var i = 0; i < model.count; i++) result.push(model.get(i))
        return result
    }

    function allTeams() {
        var seen = {}
        var result = []
        var sprints = allSprints()
        for (var i = 0; i < sprints.length; i++) {
            var team = sprints[i].team || ""
            if (team && !seen[team]) {
                seen[team] = true
                result.push(team)
            }
        }
        return result
    }

    function visibleTeams() {
        return selectedTeams.length > 0 ? selectedTeams : allTeams()
    }

    function sprintsForTeam(team) {
        var result = []
        var sprints = allSprints()
        for (var i = 0; i < sprints.length; i++) if (sprints[i].team === team) result.push(sprints[i])
        return result
    }

    function axisLabels(teams) {
        var seen = {}
        var labels = []
        for (var t = 0; t < teams.length; t++) {
            var sprints = sprintsForTeam(teams[t])
            for (var i = 0; i < sprints.length; i++) {
                var label = sprints[i].chartSlotLabel || ("S" + (i + 1))
                if (!seen[label]) {
                    seen[label] = true
                    labels.push(label)
                }
            }
        }
        return labels
    }

    function sprintByLabel(team, label) {
        var sprints = sprintsForTeam(team)
        for (var i = 0; i < sprints.length; i++) if (sprints[i].chartSlotLabel === label) return sprints[i]
        return null
    }

    function teamColor(index) {
        return palette[index % palette.length]
    }

    function rgbaColor(hex, alpha) {
        var r = parseInt(hex.substring(1, 3), 16)
        var g = parseInt(hex.substring(3, 5), 16)
        var b = parseInt(hex.substring(5, 7), 16)
        return "rgba(" + r + "," + g + "," + b + "," + alpha + ")"
    }

    function numberValue(value) {
        return parseFloat(String(value).replace("%", "")) || 0
    }

    function toggleTeam(team) {
        var next = selectedTeams.slice()
        var index = next.indexOf(team)
        if (index >= 0) next.splice(index, 1)
        else next.push(team)
        selectedTeams = next
    }

    function repaintCharts() {
        velocityChart.requestPaint()
        completionChart.requestPaint()
        flowEfficiencyChart.requestPaint()
        committedChart.requestPaint()
        throughputChart.requestPaint()
        leadTimeChart.requestPaint()
        agingChart.requestPaint()
        cfdChart.requestPaint()
        predictabilityChart.requestPaint()
        spilloverChart.requestPaint()
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 108
        color: theme.card
        radius: 16
        border.color: theme.border

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 14
            spacing: 8

            RowLayout {
                Layout.fillWidth: true
                spacing: 12

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 3
                    Label { text: qsTr("Charts"); color: theme.text; font.pixelSize: 22; font.bold: true }
                    Text {
                        text: selectedTeams.length > 0 ? qsTr("Selected teams: %1").arg(selectedTeams.join(", ")) : qsTr("All teams")
                        color: selectedTeams.length > 0 ? theme.blue : theme.muted
                        font.pixelSize: 13
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                }

                Button { text: qsTr("All"); onClicked: selectedTeams = [] }
            }

            Flickable {
                Layout.fillWidth: true
                Layout.preferredHeight: 34
                contentWidth: teamPicker.implicitWidth
                clip: true

                RowLayout {
                    id: teamPicker
                    spacing: 10
                    Repeater {
                        model: chartsTab.allTeams()
                        CheckBox {
                            text: modelData
                            checked: chartsTab.selectedTeams.indexOf(modelData) >= 0
                            onClicked: chartsTab.toggleTeam(modelData)
                        }
                    }
                }
            }
        }
    }

    Flickable {
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        contentHeight: graphsColumn.implicitHeight
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        ColumnLayout {
            id: graphsColumn
            width: parent.width
            spacing: 12

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: velocityChart.height + 78
                color: theme.card
                radius: 14
                border.color: theme.border

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 8
                    Text { text: qsTr("Velocity (done SP per sprint)"); color: theme.text; font.pixelSize: 15; font.bold: true }
                    Text { text: qsTr("Sum of completed story points in each sprint — measures team throughput in SP"); color: theme.muted; font.pixelSize: 12 }
                    RowLayout {
                        spacing: 14
                        Repeater {
                            model: chartsTab.visibleTeams()
                            RowLayout {
                                spacing: 4
                                Rectangle { width: 10; height: 10; radius: 2; color: chartsTab.teamColor(index) }
                                Text { text: modelData; color: theme.muted; font.pixelSize: 11; elide: Text.ElideRight; Layout.maximumWidth: 120 }
                            }
                        }
                    }

                    Canvas {
                        id: velocityChart
                        Layout.fillWidth: true
                        Layout.preferredHeight: 200
                        onWidthChanged: requestPaint()
                        onHeightChanged: requestPaint()

                        onPaint: {
                            var ctx = getContext("2d")
                            var w = width, h = height
                            ctx.clearRect(0, 0, w, h)
                            var teams = chartsTab.visibleTeams()
                            var labels = chartsTab.axisLabels(teams)
                            if (teams.length === 0 || labels.length === 0) {
                                ctx.fillStyle = theme.muted; ctx.font = "13px sans-serif"; ctx.textAlign = "center"
                                ctx.fillText(qsTr("No data"), w / 2, h / 2)
                                return
                            }

                            var maxVal = 1
                            for (var t = 0; t < teams.length; t++) {
                                for (var i = 0; i < labels.length; i++) {
                                    var sprint = chartsTab.sprintByLabel(teams[t], labels[i])
                                    if (sprint) maxVal = Math.max(maxVal, chartsTab.numberValue(sprint.points))
                                }
                            }

                            var pad = { top: 10, right: 12, bottom: 32, left: 34 }
                            var cw = w - pad.left - pad.right
                            var ch = h - pad.top - pad.bottom
                            ctx.strokeStyle = theme.border; ctx.lineWidth = 0.5; ctx.fillStyle = theme.muted; ctx.font = "10px sans-serif"; ctx.textAlign = "right"
                            for (var g = 0; g <= 4; g++) {
                                var gy = pad.top + ch * g / 4
                                ctx.beginPath(); ctx.moveTo(pad.left, gy); ctx.lineTo(w - pad.right, gy); ctx.stroke()
                                ctx.fillText(Math.round(maxVal * (4 - g) / 4), pad.left - 4, gy + 3)
                            }

                            var groupW = cw / labels.length
                            var barW = Math.max(2, Math.min(18, (groupW - 8) / teams.length))
                            for (i = 0; i < labels.length; i++) {
                                var groupX = pad.left + i * groupW
                                for (t = 0; t < teams.length; t++) {
                                    var item = chartsTab.sprintByLabel(teams[t], labels[i])
                                    var value = item ? chartsTab.numberValue(item.points) : 0
                                    var bh = value / maxVal * ch
                                    var bx = groupX + (groupW - barW * teams.length) / 2 + t * barW
                                    ctx.fillStyle = chartsTab.teamColor(t)
                                    ctx.fillRect(bx, pad.top + ch - bh, barW, bh)
                                }
                                ctx.fillStyle = theme.muted; ctx.textAlign = "center"; ctx.font = "10px sans-serif"
                                ctx.fillText(labels[i], groupX + groupW / 2, pad.top + ch + 14)
                            }
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: completionChart.height + 78
                color: theme.card
                radius: 14
                border.color: theme.border

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 8
                    Text { text: qsTr("Completion (% per sprint)"); color: theme.text; font.pixelSize: 15; font.bold: true }
                    Text { text: qsTr("Share of completed story points from the total sprint scope. 100%% = all committed work delivered"); color: theme.muted; font.pixelSize: 12 }
                    RowLayout {
                        spacing: 14
                        Repeater {
                            model: chartsTab.visibleTeams()
                            RowLayout {
                                spacing: 4
                                Rectangle { width: 10; height: 10; radius: 2; color: chartsTab.teamColor(index) }
                                Text { text: modelData; color: theme.muted; font.pixelSize: 11; elide: Text.ElideRight; Layout.maximumWidth: 120 }
                            }
                        }
                    }

                    Canvas {
                        id: completionChart
                        Layout.fillWidth: true
                        Layout.preferredHeight: 200
                        onWidthChanged: requestPaint()
                        onHeightChanged: requestPaint()

                        onPaint: {
                            var ctx = getContext("2d")
                            var w = width, h = height
                            ctx.clearRect(0, 0, w, h)
                            var teams = chartsTab.visibleTeams()
                            var labels = chartsTab.axisLabels(teams)
                            if (teams.length === 0 || labels.length === 0) {
                                ctx.fillStyle = theme.muted; ctx.font = "13px sans-serif"; ctx.textAlign = "center"
                                ctx.fillText(qsTr("No data"), w / 2, h / 2)
                                return
                            }

                            var pad = { top: 10, right: 12, bottom: 32, left: 34 }
                            var cw = w - pad.left - pad.right
                            var ch = h - pad.top - pad.bottom
                            ctx.strokeStyle = theme.border; ctx.lineWidth = 0.5; ctx.fillStyle = theme.muted; ctx.font = "10px sans-serif"; ctx.textAlign = "right"
                            for (var g = 0; g <= 4; g++) {
                                var gy = pad.top + ch * g / 4
                                ctx.beginPath(); ctx.moveTo(pad.left, gy); ctx.lineTo(w - pad.right, gy); ctx.stroke()
                                ctx.fillText((100 - 25 * g) + "%", pad.left - 4, gy + 3)
                            }

                            for (var t = 0; t < teams.length; t++) {
                                var started = false
                                ctx.strokeStyle = chartsTab.teamColor(t); ctx.lineWidth = 2; ctx.beginPath()
                                for (var i = 0; i < labels.length; i++) {
                                    var sprint = chartsTab.sprintByLabel(teams[t], labels[i])
                                    if (!sprint) continue
                                    var px = pad.left + i * (cw / Math.max(1, labels.length - 1))
                                    var py = pad.top + ch * (1 - chartsTab.numberValue(sprint.completion) / 100)
                                    if (!started) { ctx.moveTo(px, py); started = true } else ctx.lineTo(px, py)
                                }
                                ctx.stroke()
                                ctx.fillStyle = chartsTab.teamColor(t)
                                for (i = 0; i < labels.length; i++) {
                                    sprint = chartsTab.sprintByLabel(teams[t], labels[i])
                                    if (!sprint) continue
                                    px = pad.left + i * (cw / Math.max(1, labels.length - 1))
                                    py = pad.top + ch * (1 - chartsTab.numberValue(sprint.completion) / 100)
                                    ctx.beginPath(); ctx.arc(px, py, 3, 0, Math.PI * 2); ctx.fill()
                                }
                            }
                            ctx.fillStyle = theme.muted; ctx.textAlign = "center"; ctx.font = "10px sans-serif"
                            for (i = 0; i < labels.length; i++) ctx.fillText(labels[i], pad.left + i * (cw / Math.max(1, labels.length - 1)), pad.top + ch + 14)
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: flowEfficiencyChart.height + 78
                color: theme.card
                radius: 14
                border.color: theme.border

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 8
                    Text { text: qsTr("Flow Efficiency (% per sprint)"); color: theme.text; font.pixelSize: 15; font.bold: true }
                    Text { text: qsTr("Share of active time (In Progress, Review, Testing) in total task lifecycle. Higher = less waiting, lower = tasks stuck in queues"); color: theme.muted; font.pixelSize: 12 }
                    RowLayout {
                        spacing: 14
                        Repeater {
                            model: chartsTab.visibleTeams()
                            RowLayout {
                                spacing: 4
                                Rectangle { width: 10; height: 10; radius: 2; color: chartsTab.teamColor(index) }
                                Text { text: modelData; color: theme.muted; font.pixelSize: 11; elide: Text.ElideRight; Layout.maximumWidth: 120 }
                            }
                        }
                    }

                    Canvas {
                        id: flowEfficiencyChart
                        Layout.fillWidth: true
                        Layout.preferredHeight: 200
                        onWidthChanged: requestPaint()
                        onHeightChanged: requestPaint()

                        onPaint: {
                            var ctx = getContext("2d")
                            var w = width, h = height
                            ctx.clearRect(0, 0, w, h)
                            var teams = chartsTab.visibleTeams()
                            var labels = chartsTab.axisLabels(teams)
                            if (teams.length === 0 || labels.length === 0) {
                                ctx.fillStyle = theme.muted; ctx.font = "13px sans-serif"; ctx.textAlign = "center"
                                ctx.fillText(qsTr("No data"), w / 2, h / 2)
                                return
                            }

                            var pad = { top: 10, right: 12, bottom: 32, left: 34 }
                            var cw = w - pad.left - pad.right
                            var ch = h - pad.top - pad.bottom
                            ctx.strokeStyle = theme.border; ctx.lineWidth = 0.5; ctx.fillStyle = theme.muted; ctx.font = "10px sans-serif"; ctx.textAlign = "right"
                            for (var g = 0; g <= 4; g++) {
                                var gy = pad.top + ch * g / 4
                                ctx.beginPath(); ctx.moveTo(pad.left, gy); ctx.lineTo(w - pad.right, gy); ctx.stroke()
                                ctx.fillText((100 - 25 * g) + "%", pad.left - 4, gy + 3)
                            }

                            for (var t = 0; t < teams.length; t++) {
                                var started = false
                                ctx.strokeStyle = chartsTab.teamColor(t); ctx.lineWidth = 2; ctx.beginPath()
                                for (var i = 0; i < labels.length; i++) {
                                    var sprint = chartsTab.sprintByLabel(teams[t], labels[i])
                                    if (!sprint) continue
                                    var px = pad.left + i * (cw / Math.max(1, labels.length - 1))
                                    var py = pad.top + ch * (1 - chartsTab.numberValue(sprint.flowEfficiency) / 100)
                                    if (!started) { ctx.moveTo(px, py); started = true } else ctx.lineTo(px, py)
                                }
                                ctx.stroke()
                                ctx.fillStyle = chartsTab.teamColor(t)
                                for (i = 0; i < labels.length; i++) {
                                    sprint = chartsTab.sprintByLabel(teams[t], labels[i])
                                    if (!sprint) continue
                                    px = pad.left + i * (cw / Math.max(1, labels.length - 1))
                                    py = pad.top + ch * (1 - chartsTab.numberValue(sprint.flowEfficiency) / 100)
                                    ctx.beginPath(); ctx.arc(px, py, 3, 0, Math.PI * 2); ctx.fill()
                                }
                            }
                            ctx.fillStyle = theme.muted; ctx.textAlign = "center"; ctx.font = "10px sans-serif"
                            for (i = 0; i < labels.length; i++) ctx.fillText(labels[i], pad.left + i * (cw / Math.max(1, labels.length - 1)), pad.top + ch + 14)
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: committedChart.height + 78
                color: theme.card
                radius: 14
                border.color: theme.border

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 8
                    Text { text: qsTr("Committed vs Completed (SP per sprint)"); color: theme.text; font.pixelSize: 15; font.bold: true }
                    Text { text: qsTr("Planned scope (lighter) compared to actually delivered (darker) per sprint. Shows whether teams over-commit"); color: theme.muted; font.pixelSize: 12 }
                    RowLayout {
                        spacing: 14
                        Repeater {
                            model: [
                                { color: theme.muted, text: qsTr("Committed") },
                                { color: theme.blue, text: qsTr("Completed") }
                            ]
                            RowLayout {
                                spacing: 4
                                Rectangle { width: 10; height: 10; radius: 2; color: modelData.color }
                                Text { text: modelData.text; color: theme.muted; font.pixelSize: 11 }
                            }
                        }
                    }

                    Canvas {
                        id: committedChart
                        Layout.fillWidth: true
                        Layout.preferredHeight: 200
                        onWidthChanged: requestPaint()
                        onHeightChanged: requestPaint()

                        onPaint: {
                            var ctx = getContext("2d")
                            var w = width, h = height
                            ctx.clearRect(0, 0, w, h)
                            var teams = chartsTab.visibleTeams()
                            var labels = chartsTab.axisLabels(teams)
                            if (teams.length === 0 || labels.length === 0) {
                                ctx.fillStyle = theme.muted; ctx.font = "13px sans-serif"; ctx.textAlign = "center"
                                ctx.fillText(qsTr("No data"), w / 2, h / 2)
                                return
                            }

                            var maxVal = 1
                            for (var t = 0; t < teams.length; t++) {
                                for (var i = 0; i < labels.length; i++) {
                                    var sprint = chartsTab.sprintByLabel(teams[t], labels[i])
                                    if (sprint) maxVal = Math.max(maxVal, chartsTab.numberValue(sprint.totalPoints))
                                }
                            }

                            var pad = { top: 10, right: 12, bottom: 32, left: 34 }
                            var cw = w - pad.left - pad.right
                            var ch = h - pad.top - pad.bottom
                            ctx.strokeStyle = theme.border; ctx.lineWidth = 0.5; ctx.fillStyle = theme.muted; ctx.font = "10px sans-serif"; ctx.textAlign = "right"
                            for (var g = 0; g <= 4; g++) {
                                var gy = pad.top + ch * g / 4
                                ctx.beginPath(); ctx.moveTo(pad.left, gy); ctx.lineTo(w - pad.right, gy); ctx.stroke()
                                ctx.fillText(Math.round(maxVal * (4 - g) / 4), pad.left - 4, gy + 3)
                            }

                            var groupW = cw / labels.length
                            var barW = Math.max(2, Math.min(14, (groupW - 12) / (teams.length * 2)))
                            for (i = 0; i < labels.length; i++) {
                                var groupX = pad.left + i * groupW
                                for (t = 0; t < teams.length; t++) {
                                    var item = chartsTab.sprintByLabel(teams[t], labels[i])
                                    var committed = item ? chartsTab.numberValue(item.totalPoints) : 0
                                    var done = item ? chartsTab.numberValue(item.points) : 0
                                    var pairW = barW * 2 + 3
                                    var pairX = groupX + (groupW - pairW * teams.length) / 2 + t * pairW

                                    ctx.globalAlpha = 0.35; ctx.fillStyle = theme.muted
                                    ctx.fillRect(pairX, pad.top + ch - committed / maxVal * ch, barW, committed / maxVal * ch)
                                    ctx.globalAlpha = 1.0; ctx.fillStyle = theme.blue
                                    ctx.fillRect(pairX + barW + 3, pad.top + ch - done / maxVal * ch, barW, done / maxVal * ch)
                                }
                                ctx.fillStyle = theme.muted; ctx.textAlign = "center"; ctx.font = "10px sans-serif"
                                ctx.fillText(labels[i], groupX + groupW / 2, pad.top + ch + 14)
                            }
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: throughputChart.height + 78
                color: theme.card
                radius: 14
                border.color: theme.border

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 8
                    Text { text: qsTr("Throughput (done issues per sprint)"); color: theme.text; font.pixelSize: 15; font.bold: true }
                    Text { text: qsTr("Number of completed tasks regardless of their size (SP). Useful for teams with uneven task sizing"); color: theme.muted; font.pixelSize: 12 }
                    RowLayout {
                        spacing: 14
                        Repeater {
                            model: chartsTab.visibleTeams()
                            RowLayout {
                                spacing: 4
                                Rectangle { width: 10; height: 10; radius: 2; color: chartsTab.teamColor(index) }
                                Text { text: modelData; color: theme.muted; font.pixelSize: 11; elide: Text.ElideRight; Layout.maximumWidth: 120 }
                            }
                        }
                    }

                    Canvas {
                        id: throughputChart
                        Layout.fillWidth: true
                        Layout.preferredHeight: 200
                        onWidthChanged: requestPaint()
                        onHeightChanged: requestPaint()

                        onPaint: {
                            var ctx = getContext("2d")
                            var w = width, h = height
                            ctx.clearRect(0, 0, w, h)
                            var teams = chartsTab.visibleTeams()
                            var labels = chartsTab.axisLabels(teams)
                            if (teams.length === 0 || labels.length === 0) {
                                ctx.fillStyle = theme.muted; ctx.font = "13px sans-serif"; ctx.textAlign = "center"
                                ctx.fillText(qsTr("No data"), w / 2, h / 2)
                                return
                            }

                            var maxVal = 1
                            for (var t = 0; t < teams.length; t++) {
                                for (var i = 0; i < labels.length; i++) {
                                    var sprint = chartsTab.sprintByLabel(teams[t], labels[i])
                                    if (sprint) maxVal = Math.max(maxVal, sprint.done || 0)
                                }
                            }

                            var pad = { top: 10, right: 12, bottom: 32, left: 34 }
                            var cw = w - pad.left - pad.right
                            var ch = h - pad.top - pad.bottom
                            ctx.strokeStyle = theme.border; ctx.lineWidth = 0.5; ctx.fillStyle = theme.muted; ctx.font = "10px sans-serif"; ctx.textAlign = "right"
                            for (var g = 0; g <= 4; g++) {
                                var gy = pad.top + ch * g / 4
                                ctx.beginPath(); ctx.moveTo(pad.left, gy); ctx.lineTo(w - pad.right, gy); ctx.stroke()
                                ctx.fillText(Math.round(maxVal * (4 - g) / 4), pad.left - 4, gy + 3)
                            }

                            var groupW = cw / labels.length
                            var barW = Math.max(2, Math.min(18, (groupW - 8) / teams.length))
                            for (i = 0; i < labels.length; i++) {
                                var groupX = pad.left + i * groupW
                                for (t = 0; t < teams.length; t++) {
                                    var item = chartsTab.sprintByLabel(teams[t], labels[i])
                                    var value = item ? (item.done || 0) : 0
                                    var bh = value / maxVal * ch
                                    var bx = groupX + (groupW - barW * teams.length) / 2 + t * barW
                                    ctx.fillStyle = chartsTab.teamColor(t)
                                    ctx.fillRect(bx, pad.top + ch - bh, barW, bh)
                                }
                                ctx.fillStyle = theme.muted; ctx.textAlign = "center"; ctx.font = "10px sans-serif"
                                ctx.fillText(labels[i], groupX + groupW / 2, pad.top + ch + 14)
                            }
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: leadTimeChart.height + 78
                color: theme.card
                radius: 14
                border.color: theme.border

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 8
                    Text { text: qsTr("Lead time p50 (days per sprint)"); color: theme.text; font.pixelSize: 15; font.bold: true }
                    Text { text: qsTr("Median time from task creation to completion, in days. Lower = faster delivery, rising trend = growing delays"); color: theme.muted; font.pixelSize: 12 }
                    RowLayout {
                        spacing: 14
                        Repeater {
                            model: chartsTab.visibleTeams()
                            RowLayout {
                                spacing: 4
                                Rectangle { width: 10; height: 10; radius: 2; color: chartsTab.teamColor(index) }
                                Text { text: modelData; color: theme.muted; font.pixelSize: 11; elide: Text.ElideRight; Layout.maximumWidth: 120 }
                            }
                        }
                    }

                    Canvas {
                        id: leadTimeChart
                        Layout.fillWidth: true
                        Layout.preferredHeight: 200
                        onWidthChanged: requestPaint()
                        onHeightChanged: requestPaint()

                        onPaint: {
                            var ctx = getContext("2d")
                            var w = width, h = height
                            ctx.clearRect(0, 0, w, h)
                            var teams = chartsTab.visibleTeams()
                            var labels = chartsTab.axisLabels(teams)
                            if (teams.length === 0 || labels.length === 0) {
                                ctx.fillStyle = theme.muted; ctx.font = "13px sans-serif"; ctx.textAlign = "center"
                                ctx.fillText(qsTr("No data"), w / 2, h / 2)
                                return
                            }

                            var maxVal = 1
                            for (var t = 0; t < teams.length; t++) {
                                for (var i = 0; i < labels.length; i++) {
                                    var sprint = chartsTab.sprintByLabel(teams[t], labels[i])
                                    if (sprint) maxVal = Math.max(maxVal, chartsTab.numberValue(sprint.leadTimeP50))
                                }
                            }

                            var pad = { top: 10, right: 12, bottom: 32, left: 34 }
                            var cw = w - pad.left - pad.right
                            var ch = h - pad.top - pad.bottom
                            ctx.strokeStyle = theme.border; ctx.lineWidth = 0.5; ctx.fillStyle = theme.muted; ctx.font = "10px sans-serif"; ctx.textAlign = "right"
                            for (var g = 0; g <= 4; g++) {
                                var gy = pad.top + ch * g / 4
                                ctx.beginPath(); ctx.moveTo(pad.left, gy); ctx.lineTo(w - pad.right, gy); ctx.stroke()
                                ctx.fillText(Math.round(maxVal * (4 - g) / 4), pad.left - 4, gy + 3)
                            }

                            for (var t = 0; t < teams.length; t++) {
                                var started = false
                                ctx.strokeStyle = chartsTab.teamColor(t); ctx.lineWidth = 2; ctx.beginPath()
                                for (var i = 0; i < labels.length; i++) {
                                    var sprint = chartsTab.sprintByLabel(teams[t], labels[i])
                                    if (!sprint) continue
                                    var px = pad.left + i * (cw / Math.max(1, labels.length - 1))
                                    var py = pad.top + ch * (1 - Math.min(1, chartsTab.numberValue(sprint.leadTimeP50) / maxVal))
                                    if (!started) { ctx.moveTo(px, py); started = true } else ctx.lineTo(px, py)
                                }
                                ctx.stroke()
                                ctx.fillStyle = chartsTab.teamColor(t)
                                for (i = 0; i < labels.length; i++) {
                                    sprint = chartsTab.sprintByLabel(teams[t], labels[i])
                                    if (!sprint) continue
                                    px = pad.left + i * (cw / Math.max(1, labels.length - 1))
                                    py = pad.top + ch * (1 - Math.min(1, chartsTab.numberValue(sprint.leadTimeP50) / maxVal))
                                    ctx.beginPath(); ctx.arc(px, py, 3, 0, Math.PI * 2); ctx.fill()
                                }
                            }
                            ctx.fillStyle = theme.muted; ctx.textAlign = "center"; ctx.font = "10px sans-serif"
                            for (i = 0; i < labels.length; i++) ctx.fillText(labels[i], pad.left + i * (cw / Math.max(1, labels.length - 1)), pad.top + ch + 14)
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: agingChart.height + 78
                color: theme.card
                radius: 14
                border.color: theme.border

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 8
                    Text { text: qsTr("Aging (days, not-done tasks)"); color: theme.text; font.pixelSize: 15; font.bold: true }
                    Text { text: qsTr("Age of unfinished tasks: average (lighter) and maximum (red). High values mean incomplete work is accumulating"); color: theme.muted; font.pixelSize: 12 }
                    RowLayout {
                        spacing: 14
                        Repeater {
                            model: [
                                { color: theme.muted, text: qsTr("Average") },
                                { color: theme.red, text: qsTr("Max") }
                            ]
                            RowLayout {
                                spacing: 4
                                Rectangle { width: 10; height: 10; radius: 2; color: modelData.color }
                                Text { text: modelData.text; color: theme.muted; font.pixelSize: 11 }
                            }
                        }
                    }

                    Canvas {
                        id: agingChart
                        Layout.fillWidth: true
                        Layout.preferredHeight: 200
                        onWidthChanged: requestPaint()
                        onHeightChanged: requestPaint()

                        onPaint: {
                            var ctx = getContext("2d")
                            var w = width, h = height
                            ctx.clearRect(0, 0, w, h)
                            var teams = chartsTab.visibleTeams()
                            var labels = chartsTab.axisLabels(teams)
                            if (teams.length === 0 || labels.length === 0) {
                                ctx.fillStyle = theme.muted; ctx.font = "13px sans-serif"; ctx.textAlign = "center"
                                ctx.fillText(qsTr("No data"), w / 2, h / 2)
                                return
                            }

                            var maxVal = 1
                            for (var t = 0; t < teams.length; t++) {
                                for (var i = 0; i < labels.length; i++) {
                                    var sprint = chartsTab.sprintByLabel(teams[t], labels[i])
                                    if (sprint) maxVal = Math.max(maxVal, chartsTab.numberValue(sprint.agingMax), chartsTab.numberValue(sprint.agingAvg))
                                }
                            }

                            var pad = { top: 10, right: 12, bottom: 32, left: 34 }
                            var cw = w - pad.left - pad.right
                            var ch = h - pad.top - pad.bottom
                            ctx.strokeStyle = theme.border; ctx.lineWidth = 0.5; ctx.fillStyle = theme.muted; ctx.font = "10px sans-serif"; ctx.textAlign = "right"
                            for (var g = 0; g <= 4; g++) {
                                var gy = pad.top + ch * g / 4
                                ctx.beginPath(); ctx.moveTo(pad.left, gy); ctx.lineTo(w - pad.right, gy); ctx.stroke()
                                ctx.fillText(Math.round(maxVal * (4 - g) / 4), pad.left - 4, gy + 3)
                            }

                            var groupW = cw / Math.max(1, labels.length)
                            var barW = Math.max(2, Math.min(14, (groupW - 12) / 2))
                            for (i = 0; i < labels.length; i++) {
                                var groupX = pad.left + i * groupW
                                var avgSum = 0
                                var avgWeight = 0
                                var max = 0
                                for (t = 0; t < teams.length; t++) {
                                    var item = chartsTab.sprintByLabel(teams[t], labels[i])
                                    if (!item) continue
                                    var weight = Math.max(1, chartsTab.numberValue(item.notDone))
                                    avgSum += chartsTab.numberValue(item.agingAvg) * weight
                                    avgWeight += weight
                                    max = Math.max(max, chartsTab.numberValue(item.agingMax))
                                }
                                var avg = avgWeight > 0 ? avgSum / avgWeight : 0
                                var pairW = barW * 2 + 3
                                var pairX = groupX + (groupW - pairW) / 2

                                ctx.globalAlpha = 0.5; ctx.fillStyle = theme.muted
                                ctx.fillRect(pairX, pad.top + ch - avg / maxVal * ch, barW, avg / maxVal * ch)
                                ctx.globalAlpha = 1.0; ctx.fillStyle = theme.red
                                ctx.fillRect(pairX + barW + 3, pad.top + ch - max / maxVal * ch, barW, max / maxVal * ch)

                                ctx.fillStyle = theme.muted; ctx.textAlign = "center"; ctx.font = "10px sans-serif"
                                ctx.fillText(labels[i], groupX + groupW / 2, pad.top + ch + 14)
                            }
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: cfdChart.height + 100
                color: theme.card
                radius: 14
                border.color: theme.border

                property var statusLegend: []

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 6
                    Text { text: qsTr("Cumulative Flow (issues by status per sprint)"); color: theme.text; font.pixelSize: 15; font.bold: true }
                    Text { text: qsTr("Distribution of tasks by status at the end of each sprint. Wide bands = bottlenecks, narrowing = work is being completed"); color: theme.muted; font.pixelSize: 12 }

                    Flickable {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 26
                        contentWidth: cfdLegendRow.implicitWidth
                        clip: true

                        RowLayout {
                            id: cfdLegendRow
                            spacing: 10
                            Repeater {
                                model: parent.parent.parent.statusLegend
                                RowLayout {
                                    spacing: 4
                                    Rectangle { width: 10; height: 10; radius: 2; color: modelData.color }
                                    Text { text: modelData.name; color: theme.muted; font.pixelSize: 11 }
                                }
                            }
                        }
                    }

                    Canvas {
                        id: cfdChart
                        Layout.fillWidth: true
                        Layout.preferredHeight: 200
                        onWidthChanged: requestPaint()
                        onHeightChanged: requestPaint()

                        property var statusColors: ["#2563eb", "#dc2626", "#16a34a", "#d97706", "#9333ea", "#0891b2", "#c026d3", "#667085"]

                        onPaint: {
                            var ctx = getContext("2d")
                            var w = width, h = height
                            ctx.clearRect(0, 0, w, h)
                            var teams = chartsTab.visibleTeams()
                            var labels = chartsTab.axisLabels(teams)
                            if (teams.length === 0 || labels.length === 0) {
                                ctx.fillStyle = theme.muted; ctx.font = "13px sans-serif"; ctx.textAlign = "center"
                                ctx.fillText(qsTr("No data"), w / 2, h / 2)
                                return
                            }

                            var statusOrder = []
                            var statusIndex = {}
                            var perSprint = []

                            for (var i = 0; i < labels.length; i++) {
                                var merged = {}
                                var total = 0
                                for (var t = 0; t < teams.length; t++) {
                                    var sprint = chartsTab.sprintByLabel(teams[t], labels[i])
                                    if (!sprint || !sprint.statusDistribution) continue
                                    var dist = JSON.parse(sprint.statusDistribution)
                                    for (var sn in dist) {
                                        merged[sn] = (merged[sn] || 0) + dist[sn]
                                        total += dist[sn]
                                        if (statusIndex[sn] === undefined) {
                                            statusIndex[sn] = statusOrder.length
                                            statusOrder.push(sn)
                                        }
                                    }
                                }
                                perSprint.push({ dist: merged, total: total })
                            }

                            var legendData = []
                            for (var si = 0; si < statusOrder.length; si++) {
                                legendData.push({ name: statusOrder[si], color: cfdChart.statusColors[si % cfdChart.statusColors.length] })
                            }
                            parent.parent.statusLegend = legendData

                            var maxVal = 1
                            for (i = 0; i < perSprint.length; i++) maxVal = Math.max(maxVal, perSprint[i].total)

                            var pad = { top: 10, right: 12, bottom: 38, left: 34 }
                            var cw = w - pad.left - pad.right
                            var ch = h - pad.top - pad.bottom
                            ctx.strokeStyle = theme.border; ctx.lineWidth = 0.5; ctx.fillStyle = theme.muted; ctx.font = "10px sans-serif"; ctx.textAlign = "right"
                            for (var g = 0; g <= 4; g++) {
                                var gy = pad.top + ch * g / 4
                                ctx.beginPath(); ctx.moveTo(pad.left, gy); ctx.lineTo(w - pad.right, gy); ctx.stroke()
                                ctx.fillText(Math.round(maxVal * (4 - g) / 4), pad.left - 4, gy + 3)
                            }

                            var groupW = cw / labels.length
                            var barW = Math.max(4, Math.min(40, groupW - 8))
                            for (i = 0; i < labels.length; i++) {
                                var groupX = pad.left + i * groupW + (groupW - barW) / 2
                                var yOff = 0
                                var sp = perSprint[i]
                                for (si = 0; si < statusOrder.length; si++) {
                                    var sn = statusOrder[si]
                                    var count = sp.dist[sn] || 0
                                    var segH = count / maxVal * ch
                                    ctx.fillStyle = cfdChart.statusColors[si % cfdChart.statusColors.length]
                                    ctx.fillRect(groupX, pad.top + ch - yOff - segH, barW, segH)
                                    yOff += segH
                                }
                                ctx.fillStyle = theme.muted; ctx.textAlign = "center"; ctx.font = "9px sans-serif"
                                ctx.fillText(labels[i], groupX + barW / 2, pad.top + ch + 14)
                            }
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: predictabilityChart.height + 78
                color: theme.card
                radius: 14
                border.color: theme.border

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 8
                    Text { text: qsTr("Predictability (completion ±1σ per sprint)"); color: theme.text; font.pixelSize: 15; font.bold: true }
                    Text { text: qsTr("Dashed line = average completion. Shaded band = ±1 standard deviation. Narrow band = stable delivery, wide band = unpredictable"); color: theme.muted; font.pixelSize: 12 }
                    RowLayout {
                        spacing: 14
                        Repeater {
                            model: chartsTab.visibleTeams()
                            RowLayout {
                                spacing: 4
                                Rectangle { width: 10; height: 10; radius: 2; color: chartsTab.teamColor(index) }
                                Text { text: modelData; color: theme.muted; font.pixelSize: 11; elide: Text.ElideRight; Layout.maximumWidth: 120 }
                            }
                        }
                    }

                    Canvas {
                        id: predictabilityChart
                        Layout.fillWidth: true
                        Layout.preferredHeight: 200
                        onWidthChanged: requestPaint()
                        onHeightChanged: requestPaint()

                        onPaint: {
                            var ctx = getContext("2d")
                            var w = width, h = height
                            ctx.clearRect(0, 0, w, h)
                            var teams = chartsTab.visibleTeams()
                            var labels = chartsTab.axisLabels(teams)
                            if (teams.length === 0 || labels.length === 0) {
                                ctx.fillStyle = theme.muted; ctx.font = "13px sans-serif"; ctx.textAlign = "center"
                                ctx.fillText(qsTr("No data"), w / 2, h / 2)
                                return
                            }

                            var pad = { top: 10, right: 12, bottom: 32, left: 34 }
                            var cw = w - pad.left - pad.right
                            var ch = h - pad.top - pad.bottom
                            ctx.strokeStyle = theme.border; ctx.lineWidth = 0.5; ctx.fillStyle = theme.muted; ctx.font = "10px sans-serif"; ctx.textAlign = "right"
                            for (var g = 0; g <= 4; g++) {
                                var gy = pad.top + ch * g / 4
                                ctx.beginPath(); ctx.moveTo(pad.left, gy); ctx.lineTo(w - pad.right, gy); ctx.stroke()
                                ctx.fillText((100 - 25 * g) + "%", pad.left - 4, gy + 3)
                            }

                            for (var t = 0; t < teams.length; t++) {
                                var vals = []
                                for (var i = 0; i < labels.length; i++) {
                                    var sprint = chartsTab.sprintByLabel(teams[t], labels[i])
                                    if (sprint) vals.push(chartsTab.numberValue(sprint.completion))
                                }
                                if (vals.length === 0) continue
                                var sum = 0; for (i = 0; i < vals.length; i++) sum += vals[i]
                                var avg = sum / vals.length
                                var variance = 0; for (i = 0; i < vals.length; i++) variance += (vals[i] - avg) * (vals[i] - avg)
                                var stddev = Math.sqrt(variance / vals.length)

                                var avg_y = pad.top + ch * (1 - avg / 100)
                                var hi_y = pad.top + ch * (1 - Math.min(100, (avg + stddev)) / 100)
                                var lo_y = pad.top + ch * (1 - Math.max(0, (avg - stddev)) / 100)

                                var bandH = lo_y - hi_y
                                if (bandH > 0.5) {
                                    ctx.fillStyle = chartsTab.rgbaColor(chartsTab.teamColor(t), 0.18)
                                    ctx.fillRect(pad.left, hi_y, cw, bandH)
                                }

                                ctx.strokeStyle = chartsTab.teamColor(t); ctx.setLineDash([6, 4]); ctx.lineWidth = 1.5; ctx.beginPath()
                                ctx.moveTo(pad.left, avg_y); ctx.lineTo(pad.left + cw, avg_y); ctx.stroke()
                                ctx.setLineDash([])

                                ctx.fillStyle = chartsTab.rgbaColor(chartsTab.teamColor(t), 0.6)
                                for (i = 0; i < vals.length; i++) {
                                    var px = pad.left + i * (cw / Math.max(1, vals.length - 1))
                                    var py = pad.top + ch * (1 - vals[i] / 100)
                                    ctx.beginPath(); ctx.arc(px, py, 3, 0, Math.PI * 2); ctx.fill()
                                }
                            }
                            ctx.fillStyle = theme.muted; ctx.textAlign = "center"; ctx.font = "10px sans-serif"
                            for (var i = 0; i < labels.length; i++) ctx.fillText(labels[i], pad.left + i * (cw / Math.max(1, labels.length - 1)), pad.top + ch + 14)
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: spilloverChart.height + 78
                color: theme.card
                radius: 14
                border.color: theme.border

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 8
                    Text { text: qsTr("Spillover totals by team"); color: theme.text; font.pixelSize: 15; font.bold: true }
                    Text { text: qsTr("Observed routes for work unfinished at sprint cutoff"); color: theme.muted; font.pixelSize: 12 }

                    RowLayout {
                        spacing: 16
                        Repeater {
                            model: [
                                { color: "#d97706", text: qsTr("Spillover") },
                                { color: "#667085", text: qsTr("Backlog") },
                                { color: "#7c3aed", text: qsTr("Other board") },
                                { color: "#dc2626", text: qsTr("Not observed") },
                                { color: "#f59e0b", text: qsTr("Unknown") }
                            ]
                            RowLayout {
                                spacing: 4
                                Rectangle { width: 10; height: 10; radius: 2; color: modelData.color }
                                Text { text: modelData.text; color: theme.muted; font.pixelSize: 11 }
                            }
                        }
                    }

                    Canvas {
                        id: spilloverChart
                        Layout.fillWidth: true
                        Layout.preferredHeight: 200
                        onWidthChanged: requestPaint()
                        onHeightChanged: requestPaint()

                        onPaint: {
                            var ctx = getContext("2d")
                            var w = width, h = height
                            ctx.clearRect(0, 0, w, h)
                            var teams = chartsTab.visibleTeams()
                            if (teams.length === 0) {
                                ctx.fillStyle = theme.muted; ctx.font = "13px sans-serif"; ctx.textAlign = "center"
                                ctx.fillText(qsTr("No data"), w / 2, h / 2)
                                return
                            }

                            var totals = []
                            var maxVal = 1
                            for (var t = 0; t < teams.length; t++) {
                                var row = { spillover: 0, backlog: 0, observedOtherBoard: 0, notObserved: 0, unknown: 0 }
                                var sprints = chartsTab.sprintsForTeam(teams[t])
                                for (var i = 0; i < sprints.length; i++) {
                                    row.spillover += chartsTab.numberValue(sprints[i].spillover)
                                    row.backlog += chartsTab.numberValue(sprints[i].backlog)
                                    row.observedOtherBoard += chartsTab.numberValue(sprints[i].observedOtherBoard)
                                    row.notObserved += chartsTab.numberValue(sprints[i].notObserved)
                                    row.unknown += chartsTab.numberValue(sprints[i].unknown)
                                }
                                row.total = row.spillover + row.backlog + row.observedOtherBoard + row.notObserved + row.unknown
                                maxVal = Math.max(maxVal, row.total)
                                totals.push(row)
                            }

                            var pad = { top: 10, right: 12, bottom: 40, left: 34 }
                            var cw = w - pad.left - pad.right
                            var ch = h - pad.top - pad.bottom
                            ctx.strokeStyle = theme.border; ctx.lineWidth = 0.5; ctx.fillStyle = theme.muted; ctx.font = "10px sans-serif"; ctx.textAlign = "right"
                            for (var g = 0; g <= 4; g++) {
                                var gy = pad.top + ch * g / 4
                                ctx.beginPath(); ctx.moveTo(pad.left, gy); ctx.lineTo(w - pad.right, gy); ctx.stroke()
                                ctx.fillText(Math.round(maxVal * (4 - g) / 4), pad.left - 4, gy + 3)
                            }

                            var colors = { spillover: "#d97706", backlog: "#667085", observedOtherBoard: "#7c3aed", notObserved: "#dc2626", unknown: "#f59e0b" }
                            var keys = ["spillover", "backlog", "observedOtherBoard", "notObserved", "unknown"]
                            var barW = Math.min(46, cw / teams.length - 8)
                            for (t = 0; t < teams.length; t++) {
                                var bx = pad.left + t * (cw / teams.length) + (cw / teams.length - barW) / 2
                                var yOff = 0
                                for (var k = 0; k < keys.length; k++) {
                                    var val = totals[t][keys[k]]
                                    var segH = val / maxVal * ch
                                    ctx.fillStyle = colors[keys[k]]
                                    ctx.fillRect(bx, pad.top + ch - yOff - segH, barW, segH)
                                    yOff += segH
                                }
                                ctx.fillStyle = theme.muted; ctx.font = "10px sans-serif"; ctx.textAlign = "center"
                                var label = teams[t].length > 8 ? teams[t].substring(0, 7) + ".." : teams[t]
                                ctx.fillText(label, bx + barW / 2, pad.top + ch + 14)
                            }
                        }
                    }
                }
            }
        }
    }

    onSelectedTeamsChanged: repaintCharts()
    Connections { target: chartsTab.sprintModel(); function onModelReset() { chartsTab.repaintCharts() } }

}
