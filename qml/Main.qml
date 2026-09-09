import QtQuick 2.12
import QtQuick.Controls 2.12
import QtQuick.Layouts 1.12

ApplicationWindow {
    id: root
    property int setupStep: backend && backend.needsLogin ? 0 : 1
    property int pendingSetupStep: setupStep
    width: backend && backend.setupRequired ? 520 : 980
    height: backend && backend.setupRequired ? 620 : 640
    minimumWidth: backend && backend.setupRequired ? 520 : 760
    minimumHeight: backend && backend.setupRequired ? Math.max(loginContent.implicitHeight, periodContent.implicitHeight) + 104 : 500
    visible: true
    title: "Jira Analytics"
    color: "#f6f7f9"

    readonly property color bg: "#f6f7f9"
    readonly property color card: "#ffffff"
    readonly property color border: "#dde1e7"
    readonly property color text: "#172033"
    readonly property color muted: "#667085"
    readonly property color blue: "#2563eb"
    readonly property color red: "#dc2626"
    readonly property color redSoft: "#fef2f2"
    readonly property color amber: "#d97706"
    readonly property color amberSoft: "#fff7ed"

    QtObject {
        id: moduleTheme
        readonly property color bg: root.bg
        readonly property color card: root.card
        readonly property color border: root.border
        readonly property color text: root.text
        readonly property color muted: root.muted
        readonly property color blue: root.blue
        readonly property color red: root.red
        readonly property color redSoft: root.redSoft
        readonly property color amber: root.amber
        readonly property color amberSoft: root.amberSoft
    }

    function currentModuleId() {
        var idx = mainTabs.currentIndex
        if (idx < 0) return ""
        var item = moduleTabsModel ? moduleTabsModel.get(idx) : null
        return item ? item.moduleId || "" : ""
    }

    function openSettings() {
        settingsJiraUrl.text = backend ? backend.jiraUrl : ""
        settingsUsername.text = backend ? backend.username : ""
        if (backend && backend.password && !backend.token) {
            settingsUsePasswordBtn.checked = true
            settingsUseTokenBtn.checked = false
        } else {
            settingsUseTokenBtn.checked = true
            settingsUsePasswordBtn.checked = false
        }
        settingsAuthVisible.checked = false
        warnDays.text = backend ? backend.warnDays : "7"
        firstDelay.text = backend ? backend.firstCheckDelayMinutes : "2"
        checkInterval.text = backend ? backend.checkIntervalMinutes : "60"
        connectTimeout.text = backend ? backend.connectTimeoutSeconds : "15"
        forgottenAge.text = backend ? backend.forgottenAgeDays : "30"
        targetEndField.text = backend ? backend.targetEndField : "Target end"
        settingsIncludeActiveSprints.checked = backend ? backend.includeActiveSprints : true
        settingsStoryPointsField.text = backend ? backend.storyPointsField : "Story points"
        settingsChangelogTtl.text = backend ? backend.changelogCacheTtlHours : "24"
        settingsShowExternalAssignees.checked = backend ? backend.showExternalAssignees : false
        settingsParallelWorkers.text = backend ? backend.parallelWorkers : "3"
        settingsDoneStatuses.text = backend ? backend.doneStatusesText : ""
        settingsActiveStatuses.text = backend ? backend.activeStatusesText : ""
        settingsWaitingStatuses.text = backend ? backend.waitingStatusesText : ""
        settingsDialog.open()
    }

    function saveSettings() {
        if (!backend) return
        backend.saveSettings(
            settingsJiraUrl.text,
            settingsUsername.text,
            settingsUseTokenBtn.checked ? settingsAuthField.text : "",
            settingsUseTokenBtn.checked ? "" : settingsAuthField.text,
            parseInt(warnDays.text) || 0,
            parseInt(firstDelay.text) || 0,
            parseInt(checkInterval.text) || 0,
            Math.max(1, parseInt(connectTimeout.text) || 1),
            parseInt(forgottenAge.text) || 0,
            targetEndField.text,
            settingsIncludeActiveSprints.checked
        )
        backend.saveAnalyticsSettings(
            settingsStoryPointsField.text,
            parseInt(settingsChangelogTtl.text) || 1,
            settingsShowExternalAssignees.checked,
            parseInt(settingsParallelWorkers.text) || 1,
            settingsDoneStatuses.text,
            settingsActiveStatuses.text,
            settingsWaitingStatuses.text
        )
        settingsDialog.close()
    }

    function showSetupStep(step) {
        if (step === root.setupStep || setupTransition.running) return
        root.pendingSetupStep = step
        setupTransition.start()
    }

    onClosing: {
        close.accepted = false
        root.hide()
    }

    Connections {
        target: backend
        onAuthenticationFailed: root.showSetupStep(0)
    }

    SequentialAnimation {
        id: setupTransition
        NumberAnimation { target: setupStack; property: "opacity"; to: 0; duration: 120; easing.type: Easing.InCubic }
        ScriptAction { script: root.setupStep = root.pendingSetupStep }
        PauseAnimation { duration: 180 }
        NumberAnimation { target: setupStack; property: "opacity"; to: 1; duration: 150; easing.type: Easing.OutCubic }
    }

    Dialog {
        id: settingsDialog
        title: qsTr("Settings")
        modal: true
        focus: true
        standardButtons: Dialog.NoButton
        x: Math.max(18, (root.width - width) / 2)
        y: Math.max(18, (root.height - height) / 2)
        width: Math.min(root.width - 36, 720)
        height: Math.min(root.height - 36, 520)

        contentItem: ColumnLayout {
            spacing: 14

            Text {
                text: qsTr("Jira access and monitoring parameters")
                color: root.muted
                font.pixelSize: 14
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            TabBar {
                id: settingsTabs
                Layout.fillWidth: true
                TabButton { text: qsTr("Access") }
                TabButton { text: qsTr("Monitoring") }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: root.card
                radius: 12
                border.color: root.border
                clip: true

                StackLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    currentIndex: settingsTabs.currentIndex

                    ColumnLayout {
                        spacing: 10
                        Layout.fillWidth: true

                        Label { text: qsTr("Jira URL"); color: root.text; font.pixelSize: 13; font.bold: true }
                        TextField {
                            id: settingsJiraUrl
                            placeholderText: "https://jira.example.com"
                            Layout.fillWidth: true
                        }

                        Label { text: qsTr("Username"); color: root.text; font.pixelSize: 13; font.bold: true }
                        TextField {
                            id: settingsUsername
                            placeholderText: "JIRA_USERNAME"
                            Layout.fillWidth: true
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            Button {
                                id: settingsUseTokenBtn
                                checkable: true
                                checked: true
                                text: qsTr("Token")
                                Layout.preferredWidth: 100
                                Layout.preferredHeight: 36
                                contentItem: Text {
                                    text: settingsUseTokenBtn.text
                                    color: settingsUseTokenBtn.checked ? "#ffffff" : (settingsUseTokenBtn.enabled ? root.text : "#98a2b3")
                                    font.pixelSize: settingsUseTokenBtn.font.pixelSize
                                    font.bold: settingsUseTokenBtn.checked
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }
                                background: Rectangle {
                                    radius: 10
                                    color: settingsUseTokenBtn.checked ? root.blue : (settingsUseTokenBtn.down ? "#e8edf5" : "#f8fafc")
                                    border.color: root.border
                                }
                                onClicked: { settingsUseTokenBtn.checked = true; settingsUsePasswordBtn.checked = false }
                            }

                            Button {
                                id: settingsUsePasswordBtn
                                checkable: true
                                text: qsTr("Password")
                                Layout.preferredWidth: 100
                                Layout.preferredHeight: 36
                                contentItem: Text {
                                    text: settingsUsePasswordBtn.text
                                    color: settingsUsePasswordBtn.checked ? "#ffffff" : (settingsUsePasswordBtn.enabled ? root.text : "#98a2b3")
                                    font.pixelSize: settingsUsePasswordBtn.font.pixelSize
                                    font.bold: settingsUsePasswordBtn.checked
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }
                                background: Rectangle {
                                    radius: 10
                                    color: settingsUsePasswordBtn.checked ? root.blue : (settingsUsePasswordBtn.down ? "#e8edf5" : "#f8fafc")
                                    border.color: root.border
                                }
                                onClicked: { settingsUsePasswordBtn.checked = true; settingsUseTokenBtn.checked = false }
                            }
                        }

                        Label {
                            text: settingsUseTokenBtn.checked ? qsTr("Token") : qsTr("Password")
                            color: root.text
                            font.pixelSize: 13
                            font.bold: true
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            TextField {
                                id: settingsAuthField
                                text: settingsUseTokenBtn.checked ? (backend ? backend.token : "") : (backend ? backend.password : "")
                                placeholderText: settingsUseTokenBtn.checked ? "JIRA_TOKEN" : "JIRA_PASSWORD"
                                echoMode: settingsAuthVisible.checked ? TextInput.Normal : TextInput.Password
                                Layout.fillWidth: true
                            }

                            Button {
                                id: settingsAuthVisible
                                checkable: true
                                text: checked ? qsTr("Hide") : qsTr("Show")
                                Layout.preferredWidth: 100
                                Layout.preferredHeight: 40
                                contentItem: Text {
                                    text: settingsAuthVisible.text
                                    color: settingsAuthVisible.enabled ? root.text : "#98a2b3"
                                    font: settingsAuthVisible.font
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }
                                background: Rectangle {
                                    radius: 10
                                    color: settingsAuthVisible.down ? "#e8edf5" : "#f8fafc"
                                    border.color: root.border
                                }
                            }
                        }

                        Text {
                            text: qsTr("Token or password are encrypted and stored in user env-file with 0600 permissions.")
                            color: root.muted
                            font.pixelSize: 12
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }

                    Flickable {
                        id: settingsMonitoringFlick
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentHeight: monitoringForm.implicitHeight
                        boundsBehavior: Flickable.StopAtBounds

                        ScrollBar.vertical: ScrollBar {
                            id: settingsMonitoringScrollBar
                            policy: ScrollBar.AsNeeded
                            width: 8
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.bottom: parent.bottom

                            background: Rectangle { color: "transparent" }
                            contentItem: Rectangle {
                                implicitWidth: 6
                                implicitHeight: 100
                                radius: 3
                                color: settingsMonitoringScrollBar.pressed ? "#98a2b3" : "#cbd5e1"
                                opacity: settingsMonitoringScrollBar.active ? 1 : 0.65
                            }
                        }

                        ColumnLayout {
                            id: monitoringForm
                            width: Math.max(0, settingsMonitoringFlick.width - 18)
                            spacing: 10

                            Label { text: qsTr("Warning threshold, days"); color: root.text; font.pixelSize: 13; font.bold: true }
                            TextField {
                                id: warnDays
                                validator: IntValidator {
                                    bottom: 0
                                    top: 3650
                                }
                                Layout.fillWidth: true
                            }

                            Label { text: qsTr("First check after, minutes"); color: root.text; font.pixelSize: 13; font.bold: true }
                            TextField {
                                id: firstDelay
                                validator: IntValidator {
                                    bottom: 0
                                    top: 10080
                                }
                                Layout.fillWidth: true
                            }

                            Label { text: qsTr("Check interval, minutes"); color: root.text; font.pixelSize: 13; font.bold: true }
                            TextField {
                                id: checkInterval
                                validator: IntValidator {
                                    bottom: 0
                                    top: 10080
                                }
                                Layout.fillWidth: true
                            }

                            Label { text: qsTr("Connection timeout, seconds"); color: root.text; font.pixelSize: 13; font.bold: true }
                            TextField {
                                id: connectTimeout
                                validator: IntValidator {
                                    bottom: 1
                                    top: 300
                                }
                                Layout.fillWidth: true
                            }

                            Label { text: qsTr("Forgotten age threshold, days"); color: root.text; font.pixelSize: 13; font.bold: true }
                            TextField {
                                id: forgottenAge
                                validator: IntValidator {
                                    bottom: 1
                                    top: 3650
                                }
                                Layout.fillWidth: true
                            }

                            Label { text: qsTr("Target date field in Jira"); color: root.text; font.pixelSize: 13; font.bold: true }
                            TextField { id: targetEndField; placeholderText: "Target end"; Layout.fillWidth: true }

                            CheckBox {
                                id: settingsIncludeActiveSprints
                                text: qsTr("Include active sprints")
                                checked: true
                                Layout.fillWidth: true
                            }

                            Label { text: qsTr("Story points field"); color: root.text; font.pixelSize: 13; font.bold: true }
                            TextField {
                                id: settingsStoryPointsField
                                placeholderText: "Story points"
                                Layout.fillWidth: true
                            }

                            Label { text: qsTr("Changelog cache TTL, hours"); color: root.text; font.pixelSize: 13; font.bold: true }
                            TextField {
                                id: settingsChangelogTtl
                                validator: IntValidator {
                                    bottom: 1
                                    top: 8760
                                }
                                Layout.fillWidth: true
                            }

                            Button {
                                id: fullRefreshButton
                                text: qsTr("Full refresh history")
                                enabled: backend && !backend.checking && !backend.periodChecking
                                Layout.preferredHeight: 40
                                Layout.fillWidth: true
                                contentItem: Text {
                                    text: fullRefreshButton.text
                                    color: fullRefreshButton.enabled ? root.text : "#98a2b3"
                                    font: fullRefreshButton.font
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }
                                background: Rectangle {
                                    radius: 10
                                    color: fullRefreshButton.down ? "#e8edf5" : "#f8fafc"
                                    border.color: root.border
                                }
                                onClicked: {
                                    root.saveSettings()
                                    backend.refreshPeriod(true)
                                }
                            }

                            CheckBox {
                                id: settingsShowExternalAssignees
                                text: qsTr("Show external assignees")
                                Layout.fillWidth: true
                            }

                            Label { text: qsTr("Parallel workers"); color: root.text; font.pixelSize: 13; font.bold: true }
                            TextField {
                                id: settingsParallelWorkers
                                validator: IntValidator {
                                    bottom: 1
                                    top: 32
                                }
                                Layout.fillWidth: true
                            }

                            Label { text: qsTr("Done statuses"); color: root.text; font.pixelSize: 13; font.bold: true }
                            ScrollView {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 90
                                TextArea {
                                    id: settingsDoneStatuses
                                    wrapMode: TextEdit.Wrap
                                }
                            }

                            Label { text: qsTr("Active statuses"); color: root.text; font.pixelSize: 13; font.bold: true }
                            ScrollView {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 90
                                TextArea {
                                    id: settingsActiveStatuses
                                    wrapMode: TextEdit.Wrap
                                }
                            }

                            Label { text: qsTr("Waiting statuses"); color: root.text; font.pixelSize: 13; font.bold: true }
                            ScrollView {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 90
                                TextArea {
                                    id: settingsWaitingStatuses
                                    wrapMode: TextEdit.Wrap
                                }
                            }
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 44
                Item { Layout.fillWidth: true }

                Button {
                    id: cancelSettingsButton
                    text: qsTr("Cancel")
                    Layout.preferredWidth: 110
                    Layout.preferredHeight: 40
                    contentItem: Text {
                        text: cancelSettingsButton.text
                        color: cancelSettingsButton.enabled ? root.text : "#98a2b3"
                        font: cancelSettingsButton.font
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        radius: 10
                        color: cancelSettingsButton.down ? "#e8edf5" : "#f8fafc"
                        border.color: root.border
                    }
                    onClicked: settingsDialog.close()
                }

                Button {
                    id: saveSettingsButton
                    text: qsTr("Save")
                    Layout.preferredWidth: 120
                    Layout.preferredHeight: 40
                    enabled: settingsJiraUrl.text.length > 0 && settingsUsername.text.length > 0 && settingsAuthField.text.length > 0 && backend && !backend.checking && !backend.periodChecking
                    contentItem: Text {
                        text: saveSettingsButton.text
                        color: saveSettingsButton.enabled ? "#ffffff" : "#98a2b3"
                        font: saveSettingsButton.font
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        radius: 10
                        color: saveSettingsButton.enabled ? (saveSettingsButton.down ? "#1d4ed8" : root.blue) : "#eef1f5"
                    }
                    onClicked: root.saveSettings()
                }
            }
        }
    }

    StackLayout {
        anchors.fill: parent
        currentIndex: backend && backend.setupRequired ? 0 : 1

        Item {
            Rectangle { anchors.fill: parent; color: root.bg }

            Rectangle {
                id: setupCard
                width: Math.min(parent.width - 48, 460)
                height: (root.setupStep === 0 ? loginContent.implicitHeight : periodContent.implicitHeight) + 56
                anchors.centerIn: parent
                color: root.card
                radius: 14
                border.color: root.border

                Behavior on height {
                    enabled: setupTransition.running
                    NumberAnimation { duration: 180; easing.type: Easing.InOutCubic }
                }

                StackLayout {
                    id: setupStack
                    anchors.fill: parent
                    anchors.margins: 28
                    currentIndex: root.setupStep
                    enabled: !setupTransition.running

                    ColumnLayout {
                        id: loginContent
                        spacing: 14

                    Label {
                        text: qsTr("Connect to Jira")
                        color: root.text
                        font.pixelSize: 28
                        font.bold: true
                        Layout.fillWidth: true
                    }

                    Text {
                        text: qsTr("Enter credentials and task list or JQL. If empty, all epics with Target end will be loaded.")
                        color: root.muted
                        font.pixelSize: 14
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        Label { text: qsTr("Jira URL"); color: root.text; font.pixelSize: 13; font.bold: true }
                        TextField {
                            id: jiraUrl
                            text: backend ? backend.jiraUrl : ""
                            placeholderText: "https://jira.example.com"
                            Layout.fillWidth: true
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        Label { text: qsTr("Username"); color: root.text; font.pixelSize: 13; font.bold: true }
                        TextField {
                            id: username
                            text: backend ? backend.username : ""
                            placeholderText: "JIRA_USERNAME"
                            Layout.fillWidth: true
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        Button {
                            id: loginUseTokenBtn
                            checkable: true
                            checked: !(backend && backend.password && !backend.token)
                            text: qsTr("Token")
                            Layout.preferredWidth: 100
                            Layout.preferredHeight: 36
                            contentItem: Text {
                                text: loginUseTokenBtn.text
                                color: loginUseTokenBtn.checked ? "#ffffff" : (loginUseTokenBtn.enabled ? root.text : "#98a2b3")
                                font.pixelSize: loginUseTokenBtn.font.pixelSize
                                font.bold: loginUseTokenBtn.checked
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                            background: Rectangle {
                                radius: 10
                                color: loginUseTokenBtn.checked ? root.blue : (loginUseTokenBtn.down ? "#e8edf5" : "#f8fafc")
                                border.color: root.border
                            }
                            onClicked: { loginUseTokenBtn.checked = true; loginUsePasswordBtn.checked = false }
                        }

                        Button {
                            id: loginUsePasswordBtn
                            checkable: true
                            checked: backend && backend.password && !backend.token
                            text: qsTr("Password")
                            Layout.preferredWidth: 100
                            Layout.preferredHeight: 36
                            contentItem: Text {
                                text: loginUsePasswordBtn.text
                                color: loginUsePasswordBtn.checked ? "#ffffff" : (loginUsePasswordBtn.enabled ? root.text : "#98a2b3")
                                font.pixelSize: loginUsePasswordBtn.font.pixelSize
                                font.bold: loginUsePasswordBtn.checked
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                            background: Rectangle {
                                radius: 10
                                color: loginUsePasswordBtn.checked ? root.blue : (loginUsePasswordBtn.down ? "#e8edf5" : "#f8fafc")
                                border.color: root.border
                            }
                            onClicked: { loginUsePasswordBtn.checked = true; loginUseTokenBtn.checked = false }
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        Label {
                            text: loginUseTokenBtn.checked ? qsTr("Token") : qsTr("Password")
                            color: root.text
                            font.pixelSize: 13
                            font.bold: true
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            TextField {
                                id: authField
                                text: loginUseTokenBtn.checked ? (backend ? backend.token : "") : (backend ? backend.password : "")
                                placeholderText: loginUseTokenBtn.checked ? "JIRA_TOKEN" : "JIRA_PASSWORD"
                                echoMode: loginAuthVisible.checked ? TextInput.Normal : TextInput.Password
                                Layout.fillWidth: true
                            }

                            Button {
                                id: loginAuthVisible
                                checkable: true
                                text: checked ? qsTr("Hide") : qsTr("Show")
                                Layout.preferredWidth: 100
                                Layout.preferredHeight: 40
                                contentItem: Text {
                                    text: loginAuthVisible.text
                                    color: loginAuthVisible.enabled ? root.text : "#98a2b3"
                                    font: loginAuthVisible.font
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }
                                background: Rectangle {
                                    radius: 10
                                    color: loginAuthVisible.down ? "#e8edf5" : "#f8fafc"
                                    border.color: root.border
                                }
                            }
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        Label { text: qsTr("Sources"); color: root.text; font.pixelSize: 13; font.bold: true }
                        ScrollView {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 110

                            TextArea {
                                id: initialItems
                                text: backend ? backend.itemsText : ""
                                placeholderText: qsTr("Issues, links or JQL, one per line")
                                wrapMode: TextEdit.Wrap
                            }
                        }
                    }

                    Button {
                        id: saveButton
                        text: qsTr("Continue")
                        font.pixelSize: 14
                        font.bold: true
                        enabled: jiraUrl.text.length > 0 && username.text.length > 0 && authField.text.length > 0
                        Layout.fillWidth: true
                        Layout.preferredHeight: 42
                        contentItem: Text {
                            text: saveButton.text
                            color: saveButton.enabled ? "#ffffff" : "#98a2b3"
                            font: saveButton.font
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle {
                            radius: 10
                            color: saveButton.enabled ? (saveButton.down ? "#1d4ed8" : root.blue) : "#eef1f5"
                        }
                        onClicked: root.showSetupStep(1)
                    }

                    Text {
                        text: backend ? backend.statusText : ""
                        color: root.muted
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }

                    }

                    ColumnLayout {
                        id: periodContent
                        spacing: 16

                        Label {
                            text: qsTr("Analytics period")
                            color: root.text
                            font.pixelSize: 28
                            font.bold: true
                            Layout.fillWidth: true
                        }

                        Text {
                            text: qsTr("Choose the reporting period. You can change it later in the YAML config.")
                            color: root.muted
                            font.pixelSize: 14
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }

                        CheckBox {
                            id: setupAnalyticsEnabled
                            text: qsTr("Enable period analytics")
                            checked: backend ? backend.analyticsEnabled : true
                            Layout.fillWidth: true
                        }

                        ColumnLayout {
                            id: periodFields
                            Layout.fillWidth: true
                            spacing: 14
                            enabled: setupAnalyticsEnabled.checked
                            opacity: enabled ? 1 : 0.5

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 6

                                Label { text: qsTr("Period start"); color: root.text; font.pixelSize: 13; font.bold: true }
                                TextField {
                                    id: setupPeriodStart
                                    text: backend ? backend.periodStart : ""
                                    inputMask: "99-99-9999"
                                    validator: RegExpValidator {
                                        regExp: /(0[1-9]|[12][0-9]|3[01])-(0[1-9]|1[0-2])-[0-9]{4}/
                                    }
                                    placeholderText: "DD-MM-YYYY"
                                    Layout.fillWidth: true
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 6

                                Label { text: qsTr("Period end"); color: root.text; font.pixelSize: 13; font.bold: true }
                                TextField {
                                    id: setupPeriodEnd
                                    text: backend ? backend.periodEnd : ""
                                    inputMask: "99-99-9999"
                                    validator: RegExpValidator {
                                        regExp: /(0[1-9]|[12][0-9]|3[01])-(0[1-9]|1[0-2])-[0-9]{4}/
                                    }
                                    placeholderText: "DD-MM-YYYY"
                                    Layout.fillWidth: true
                                }
                            }

                            Text {
                                text: qsTr("Date format: DD-MM-YYYY")
                                color: root.muted
                                font.pixelSize: 12
                                Layout.fillWidth: true
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10

                            Button {
                                id: backSetupButton
                                text: qsTr("Back")
                                Layout.preferredWidth: 110
                                Layout.preferredHeight: 42
                                contentItem: Text {
                                    text: backSetupButton.text
                                    color: backSetupButton.enabled ? root.text : "#98a2b3"
                                    font: backSetupButton.font
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }
                                background: Rectangle {
                                    radius: 10
                                    color: backSetupButton.down ? "#e8edf5" : "#f8fafc"
                                    border.color: root.border
                                }
                                onClicked: root.showSetupStep(0)
                            }

                            Button {
                                id: finishSetupButton
                                text: qsTr("Save and check")
                                font.pixelSize: 14
                                font.bold: true
                                enabled: !setupAnalyticsEnabled.checked || (setupPeriodStart.acceptableInput && setupPeriodEnd.acceptableInput)
                                Layout.fillWidth: true
                                Layout.preferredHeight: 42
                                contentItem: Text {
                                    text: finishSetupButton.text
                                    color: finishSetupButton.enabled ? "#ffffff" : "#98a2b3"
                                    font: finishSetupButton.font
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }
                                background: Rectangle {
                                    radius: 10
                                    color: finishSetupButton.enabled ? (finishSetupButton.down ? "#1d4ed8" : root.blue) : "#eef1f5"
                                }
                                onClicked: if (backend) backend.saveInitialSetup(
                                    jiraUrl.text,
                                    username.text,
                                    loginUseTokenBtn.checked ? authField.text : "",
                                    loginUseTokenBtn.checked ? "" : authField.text,
                                    initialItems.text,
                                    setupAnalyticsEnabled.checked,
                                    setupAnalyticsEnabled.checked ? setupPeriodStart.text : "",
                                    setupAnalyticsEnabled.checked ? setupPeriodEnd.text : ""
                                )
                            }
                        }

                        Text {
                            text: backend ? backend.statusText : ""
                            color: root.muted
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }
                }
            }
        }

        Item {
            Rectangle { anchors.fill: parent; color: root.bg }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 18
                spacing: 14

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 14

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4


                        Label {
                            text: qsTr("Jira Analytics")
                            color: root.text
                            font.pixelSize: 26
                            font.bold: true
                            Layout.fillWidth: true
                        }

                    }

                    Button {
                        id: settingsButton
                        text: qsTr("Settings")
                        font.pixelSize: 14
                        Layout.preferredWidth: 120
                        Layout.preferredHeight: 40
                        contentItem: Text {
                            text: settingsButton.text
                            color: settingsButton.enabled ? root.text : "#98a2b3"
                            font: settingsButton.font
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle {
                            radius: 10
                            color: settingsButton.down ? "#e8edf5" : "#f8fafc"
                            border.color: root.border
                        }
                        onClicked: root.openSettings()
                    }

                    Button {
                        id: notifyButton
                        text: backend && backend.notificationsEnabled ? qsTr("Notifications: on") : qsTr("Notifications: off")
                        font.pixelSize: 14
                        Layout.preferredWidth: 160
                        Layout.preferredHeight: 40
                        contentItem: Text {
                            text: notifyButton.text
                            color: notifyButton.enabled ? root.text : "#98a2b3"
                            font: notifyButton.font
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle {
                            radius: 10
                            color: notifyButton.down ? "#e8edf5" : "#f8fafc"
                            border.color: root.border
                        }
                        onClicked: if (backend) backend.toggleNotifications()
                    }

                }

                TabBar {
                    id: mainTabs
                    Layout.fillWidth: true
                    Repeater {
                        model: moduleTabsModel
                        TabButton { text: title }
                    }
                }

                Text {
                    visible: moduleWarningsText && moduleWarningsText.length > 0
                    Layout.fillWidth: true
                    text: moduleWarningsText || ""
                    color: moduleTheme.amber
                    font.pixelSize: 12
                    elide: Text.ElideRight
                }

                Rectangle {
                    visible: currentModuleId() !== "target_end" && moduleApi && moduleApi.periodChecking
                    Layout.fillWidth: true
                    Layout.preferredHeight: visible ? 60 : 0
                    color: "#eff6ff"
                    radius: 12
                    border.color: "#bfdbfe"
                    clip: true

                    Behavior on Layout.preferredHeight { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }

                    Timer {
                        id: progressTimer
                        running: parent.visible
                        repeat: true
                        interval: 1000
                        property int elapsed: 0
                        onTriggered: elapsed += 1
                        onRunningChanged: if (!running) elapsed = 0
                    }

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 12

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 4

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8

                                Text {
                                    text: "\u23f3"
                                    font.pixelSize: 16
                                }

                                Text {
                                    text: backend ? backend.periodProgressText : ""
                                    color: "#1e40af"
                                    font.pixelSize: 13
                                    font.bold: true
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }

                                Text {
                                    text: progressTimer.elapsed + "s"
                                    color: root.muted
                                    font.pixelSize: 12
                                }
                            }

                            ProgressBar {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 6
                                value: backend ? backend.periodProgressCurrent / 100 : 0

                                background: Rectangle { radius: 3; color: "#dbeafe" }
                                contentItem: Item {
                                    Rectangle {
                                        width: parent.width * (backend ? backend.periodProgressCurrent / 100 : 0)
                                        height: parent.height
                                        radius: 3
                                        color: root.blue
                                    }
                                }
                            }
                        }

                        Text {
                            text: (backend ? backend.periodProgressCurrent : 0) + "%"
                            color: root.muted
                            font.pixelSize: 12
                        }
                    }
                }

                StackLayout {
                    id: moduleStack
                    currentIndex: mainTabs.currentIndex
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    Repeater {
                        model: moduleTabsModel

                        Item {
                            Layout.fillWidth: true
                            Layout.fillHeight: true

                            Loader {
                                id: moduleLoader
                                anchors.fill: parent
                                Component.onCompleted: setSource(qmlSource, {
                                    "api": moduleApi,
                                    "theme": moduleTheme
                                })
                            }

                            Label {
                                anchors.centerIn: parent
                                visible: moduleLoader.status === Loader.Error
                                text: qsTr("Failed to load module: %1").arg(title)
                                color: moduleTheme.red
                            }
                        }
                    }
                }
            }
        }
    }

    Timer {
        id: startupTimer
        interval: 200
        running: true
        repeat: false
        onTriggered: { if (backend) backend.loadCachedAnalytics() }
    }
}
