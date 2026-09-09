#!/usr/bin/env python3
import json
import signal
import sys
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
from pathlib import Path

from PyQt5.QtCore import QAbstractListModel, QModelIndex, QObject, QSortFilterProxyModel, QThread, QTimer, Qt, QUrl, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QDesktopServices, QIcon, QPainter, QPixmap
from PyQt5.QtQml import QQmlApplicationEngine, QQmlComponent
from PyQt5.QtWidgets import QAction, QApplication, QMenu, QSystemTrayIcon

from jira_analytics import (
    Config, DEFAULT_CONFIG, DEFAULT_ENV, ISSUE_RE, _config_signature,
    collect_alert_result, collect_period_result, ensure_user_files,
    default_period, env_values, items_from_text, load_config, load_period_cache,
    http_status_code, normalize_jira_url, notify, save_config, save_env, save_period_cache,
    message_from_alert,
)

from jira_modules import discover_modules
from jira_period_analytics import build_chart_slots, is_forgotten

from translations import _, _c, get_translations_path, GettextTranslatorQt

# Built-in module titles for gettext extraction (context: Main.qml)
_BUILTIN_TITLES = [
    _c("Main.qml", "Target end"),
    _c("Main.qml", "Period analytics"),
    _c("Main.qml", "Backlog health"),
    _c("Main.qml", "Assignees"),
    _c("Main.qml", "Charts"),
]


def format_period_date(value: date | None) -> str:
    return value.strftime("%d-%m-%Y") if value else ""


def parse_period_date(value: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%d-%m-%Y").date()
        return parsed if format_period_date(parsed) == value else None
    except ValueError:
        return None


def worker_error_details(exc: BaseException) -> tuple[str, int]:
    message = str(exc).strip()
    current: object = exc
    seen: set[int] = set()
    status_code = None
    response_reason = ""
    while isinstance(current, BaseException) and id(current) not in seen:
        seen.add(id(current))
        response = getattr(current, "response", None)
        if response is not None:
            status_code = getattr(response, "status_code", None)
            response_reason = str(getattr(response, "reason", "") or "").strip()
            break
        current = getattr(current, "reason", None)
    details = message or response_reason or (f"HTTP {status_code}" if status_code else exc.__class__.__name__)
    return details, http_status_code(exc)


def _period_number(day: date) -> int:
    return (day.month - 1) // 3 + 1


def period_label_for_sprint(start: date, end: date) -> str:
    days: dict[tuple[int, int], int] = {}
    current = start
    while current <= end:
        key = (current.year, _period_number(current))
        days[key] = days.get(key, 0) + 1
        current += timedelta(days=1)
    total = sum(days.values()) or 1
    for (_year, period_num), count in sorted(days.items(), key=lambda item: item[1], reverse=True):
        if count * 3 >= total * 2:
            return f"Q{period_num}"
    return f"Q{_period_number(start)}"


class TaskModel(QAbstractListModel):
    roles = {
        Qt.UserRole + 1: b"key",
        Qt.UserRole + 2: b"summary",
        Qt.UserRole + 3: b"status",
        Qt.UserRole + 4: b"target_end",
        Qt.UserRole + 5: b"days",
        Qt.UserRole + 6: b"kind",
        Qt.UserRole + 7: b"url",
        Qt.UserRole + 8: b"assignee",
    }

    def __init__(self) -> None:
        super().__init__()
        self.items: list[dict[str, object]] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.items)

    def data(self, index: QModelIndex, role: int) -> object:
        if not index.isValid() or index.row() >= len(self.items):
            return None
        key = self.roles.get(role)
        if not key:
            return None
        return self.items[index.row()].get(key.decode())

    def roleNames(self) -> dict[int, bytes]:
        return self.roles

    def set_items(self, items: list[dict[str, object]]) -> None:
        self.beginResetModel()
        self.items = items
        self.endResetModel()


class DictListModel(QAbstractListModel):
    countChanged = pyqtSignal()

    def __init__(self, role_names: list[str]) -> None:
        super().__init__()
        self.roles = {Qt.UserRole + index + 1: name.encode() for index, name in enumerate(role_names)}
        self.items: list[dict[str, object]] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.items)

    def data(self, index: QModelIndex, role: int) -> object:
        if not index.isValid() or index.row() >= len(self.items):
            return None
        key = self.roles.get(role)
        return self.items[index.row()].get(key.decode()) if key else None

    def roleNames(self) -> dict[int, bytes]:
        return self.roles

    @pyqtProperty(int, notify=countChanged)
    def count(self) -> int:
        return len(self.items)

    @pyqtSlot(int, result="QVariantMap")
    def get(self, row: int) -> dict[str, object]:
        if 0 <= row < len(self.items):
            return self.items[row]
        return {}

    def set_items(self, items: list[dict[str, object]]) -> None:
        self.beginResetModel()
        self.items = items
        self.endResetModel()
        self.countChanged.emit()


class AssigneeFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent: QObject = None) -> None:
        super().__init__(parent)
        self.setDynamicSortFilter(True)
        self._board_filter = ""
        self._hide_external = True

    def set_board_filter(self, board: str) -> None:
        self._board_filter = board
        self.invalidateFilter()

    def set_hide_external(self, hide: bool) -> None:
        self._hide_external = hide
        self.invalidateFilter()

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:
        if self._board_filter:
            val = self.sourceModel().data(self.sourceModel().index(row, 0), Qt.UserRole + 2)
            if val != self._board_filter:
                return False
        if self._hide_external:
            val = self.sourceModel().data(self.sourceModel().index(row, 0), Qt.UserRole + 1)
            if val == "External / Other":
                return False
        return True


class RefreshWorker(QObject):
    finished = pyqtSignal(object, bool)
    failed = pyqtSignal(str, int)

    def __init__(self, env, config, automatic: bool) -> None:
        super().__init__()
        self.env = dict(env)
        self.config = deepcopy(config)
        self.automatic = automatic

    @pyqtSlot()
    def run(self) -> None:
        try:
            self.finished.emit(collect_alert_result(self.env, self.config), self.automatic)
        except SystemExit as exc:
            self.failed.emit(*worker_error_details(exc))
        except Exception as exc:
            self.failed.emit(*worker_error_details(exc))


class PeriodRefreshWorker(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str, int)
    progress = pyqtSignal(str, int, int)

    def __init__(self, env, config, full_refresh: bool = False) -> None:
        super().__init__()
        self.env = dict(env)
        self.config = deepcopy(config)
        self.full_refresh = full_refresh

    @pyqtSlot()
    def run(self) -> None:
        try:
            self.finished.emit(collect_period_result(
                self.env, self.config, self._progress, full_refresh=self.full_refresh
            ))
        except SystemExit as exc:
            self.failed.emit(*worker_error_details(exc))
        except Exception as exc:
            self.failed.emit(*worker_error_details(exc))

    def _progress(self, text: str, current: int, total: int) -> None:
        self.progress.emit(text, current, total)


class PeriodCacheLoadWorker(QObject):
    finished = pyqtSignal(object, str)

    def __init__(self, env, config) -> None:
        super().__init__()
        self.env = dict(env)
        self.config = deepcopy(config)
        self.signature = _config_signature(self.env, self.config)

    @pyqtSlot()
    def run(self) -> None:
        try:
            self.finished.emit(load_period_cache(self.signature), self.signature)
        except Exception:
            self.finished.emit(None, self.signature)


class PeriodCacheSaveWorker(QObject):
    finished = pyqtSignal()
    failed = pyqtSignal(str, str)
    succeeded = pyqtSignal(str)

    def __init__(self, result, env, config) -> None:
        super().__init__()
        self.result = result
        self.env = dict(env)
        self.config = deepcopy(config)
        self.signature = _config_signature(self.env, self.config)

    @pyqtSlot()
    def run(self) -> None:
        try:
            save_period_cache(self.result, self.signature)
        except Exception as exc:
            self.failed.emit(str(exc), self.signature)
        else:
            self.succeeded.emit(self.signature)
        finally:
            self.finished.emit()


class Backend(QObject):
    needsLoginChanged = pyqtSignal()
    setupRequiredChanged = pyqtSignal()
    jiraUrlChanged = pyqtSignal()
    usernameChanged = pyqtSignal()
    tokenChanged = pyqtSignal()
    passwordChanged = pyqtSignal()
    statusTextChanged = pyqtSignal()
    itemsTextChanged = pyqtSignal()
    errorsTextChanged = pyqtSignal()
    notificationsEnabledChanged = pyqtSignal()
    checkingChanged = pyqtSignal()
    periodCheckingChanged = pyqtSignal()
    periodStatusTextChanged = pyqtSignal()
    periodErrorsTextChanged = pyqtSignal()
    periodKpiChanged = pyqtSignal()
    assigneeModelChanged = pyqtSignal()
    assigneeKpiChanged = pyqtSignal()
    assigneeBoardFilterChanged = pyqtSignal()
    assigneeBoardNamesChanged = pyqtSignal()
    periodProgressTextChanged = pyqtSignal()
    periodProgressCurrentChanged = pyqtSignal()
    periodProgressTotalChanged = pyqtSignal()
    settingsChanged = pyqtSignal()
    periodLastRefreshChanged = pyqtSignal()
    periodFromCacheChanged = pyqtSignal()
    backlogHealthChanged = pyqtSignal()
    backlogSortChanged = pyqtSignal()
    backlogAgeFilterChanged = pyqtSignal()
    authenticationFailed = pyqtSignal()

    def __init__(self, task_model: TaskModel, period_board_model: DictListModel, period_sprint_model: DictListModel, period_all_sprint_model: DictListModel, assignee_model: DictListModel, backlog_health_model: DictListModel, app: QApplication) -> None:
        super().__init__()
        ensure_user_files()
        self.task_model = task_model
        self.period_board_model = period_board_model
        self.period_sprint_model = period_sprint_model
        self.period_all_sprint_model = period_all_sprint_model
        self.assignee_model = assignee_model
        self._assignee_proxy = AssigneeFilterProxy(self)
        self._assignee_proxy.setSourceModel(assignee_model)
        self.backlog_health_model = backlog_health_model
        self.app = app
        self.config = load_config(DEFAULT_CONFIG) if DEFAULT_CONFIG.exists() else None
        self.env = env_values(DEFAULT_ENV)
        self._jira_url = self.env.get("JIRA_URL", "")
        self._username = self.env.get("JIRA_USERNAME", "")
        self._token = self.env.get("JIRA_TOKEN", "")
        self._password = self.env.get("JIRA_PASSWORD", "")
        self._status_text = ""
        self._errors_text = ""
        self._auth_failed = False
        self._api_forbidden = False
        self._checking = False
        self._period_checking = False
        self._period_status_text = ""
        self._period_errors_text = ""
        self._period_kpi: dict[str, object] = {}
        self._assignee_kpi: dict[str, object] = {}
        self._assignee_board_filter: str = ""
        self._assignee_all_items: list[dict[str, object]] = []
        self._assignee_board_names: list[str] = []
        self._backlog_sort = ""
        self._period_progress_text = ""
        self._period_progress_current = 0
        self._period_progress_total = 0
        self._period_last_refresh = ""
        self._period_from_cache = False
        self._period_fresh_applied = False
        self._selected_team = ""
        self._all_boards: list[dict[str, object]] = []
        self._all_sprints: list[dict[str, object]] = []
        self._chart_slots: list[dict[str, object]] = []
        self._chart_slots_model = DictListModel(["team", "period", "startDate", "endDate", "chartSlotLabel",
            "done", "total", "totalPoints", "donePoints", "points",
            "spillover", "spilloverCompleted", "backlog", "reassigned",
            "notDone", "removed", "removedPoints",
            "completion", "flowEfficiency",
            "leadTimeP50", "leadTimeP85", "leadTimeP95",
            "agingAvg", "agingMax", "statusDistribution",
            "completedLater", "observedOtherBoard", "notObserved", "unknown", "active"])
        self._all_board_aggregates: list[object] = []
        self._board_id_by_name: dict[str, int] = {}
        self._thread: QThread | None = None
        self._worker: RefreshWorker | None = None
        self._period_thread: QThread | None = None
        self._period_worker: PeriodRefreshWorker | None = None
        self._period_cache_thread: QThread | None = None
        self._period_cache_worker: PeriodCacheLoadWorker | None = None
        self._period_cache_save_thread: QThread | None = None
        self._period_cache_save_worker: PeriodCacheSaveWorker | None = None
        self._period_cache_save_pending: tuple[object, dict[str, str], Config] | None = None
        self._period_cache_save_error = ""
        self._shutting_down = False
        self._quit_after_refresh = False
        self.first_timer = QTimer(self)
        self.first_timer.setSingleShot(True)
        self.first_timer.timeout.connect(lambda: self.refresh(True))
        self.interval_timer = QTimer(self)
        self.interval_timer.timeout.connect(lambda: self.refresh(True))

    @pyqtProperty(bool, notify=needsLoginChanged)
    def needsLogin(self) -> bool:
        return not (self._jira_url and self._username and (self._token or self._password))

    @pyqtProperty(bool, notify=setupRequiredChanged)
    def setupRequired(self) -> bool:
        return self._auth_failed or self.needsLogin or not self.config or bool(
            self.config.analytics_enabled and (not self.config.period_start or not self.config.period_end)
        )

    @pyqtProperty(str, notify=jiraUrlChanged)
    def jiraUrl(self) -> str:
        return self._jira_url

    @pyqtProperty(str, notify=usernameChanged)
    def username(self) -> str:
        return self._username

    @pyqtProperty(str, notify=tokenChanged)
    def token(self) -> str:
        return self._token

    @pyqtProperty(str, notify=passwordChanged)
    def password(self) -> str:
        return self._password

    @pyqtProperty(str, notify=statusTextChanged)
    def statusText(self) -> str:
        return self._status_text

    @pyqtProperty(str, notify=itemsTextChanged)
    def itemsText(self) -> str:
        return "\n".join(self.config.items or []) if self.config else ""

    @pyqtProperty(str, notify=errorsTextChanged)
    def errorsText(self) -> str:
        return self._errors_text

    @pyqtProperty(bool, notify=notificationsEnabledChanged)
    def notificationsEnabled(self) -> bool:
        return bool(self.config and self.config.notifications_enabled)

    @pyqtProperty(bool, notify=checkingChanged)
    def checking(self) -> bool:
        return self._checking

    @pyqtProperty(bool, notify=periodCheckingChanged)
    def periodChecking(self) -> bool:
        return self._period_checking

    @pyqtProperty(str, notify=periodStatusTextChanged)
    def periodStatusText(self) -> str:
        return self._period_status_text

    @pyqtProperty(str, notify=periodErrorsTextChanged)
    def periodErrorsText(self) -> str:
        return self._period_errors_text

    @pyqtProperty("QVariantMap", notify=periodKpiChanged)
    def periodKpi(self) -> dict[str, object]:
        return self._period_kpi

    @pyqtProperty(str, notify=periodProgressTextChanged)
    def periodProgressText(self) -> str:
        return self._period_progress_text

    @pyqtProperty(int, notify=periodProgressCurrentChanged)
    def periodProgressCurrent(self) -> int:
        return self._period_progress_current

    @pyqtProperty(int, notify=periodProgressTotalChanged)
    def periodProgressTotal(self) -> int:
        return self._period_progress_total

    @pyqtProperty(str, notify=periodLastRefreshChanged)
    def periodLastRefresh(self) -> str:
        return self._period_last_refresh

    @pyqtProperty(bool, notify=periodFromCacheChanged)
    def periodFromCache(self) -> bool:
        return self._period_from_cache

    @pyqtProperty(str, notify=backlogSortChanged)
    def backlogSort(self) -> str:
        return self._backlog_sort

    @pyqtProperty(str, notify=backlogAgeFilterChanged)
    def backlogAgeFilter(self) -> str:
        if self._backlog_sort == "stale":
            return "stale"
        if self._backlog_sort == "aging365":
            return "365+"
        if self._backlog_sort == "forgotten":
            return "forgotten"
        return ""

    @pyqtProperty(str, notify=settingsChanged)
    def backlogActiveStatuses(self) -> str:
        if not self.config:
            return ""
        return "|" + "|".join(s.lower() for s in self.config.active_statuses) + "|"

    @pyqtProperty(str, notify=settingsChanged)
    def selectedTeam(self) -> str:
        return self._selected_team

    @pyqtSlot(str)
    def setSelectedTeam(self, team: str) -> None:
        if self._selected_team == team:
            self._selected_team = ""
        else:
            self._selected_team = team
        self.settingsChanged.emit()
        self._apply_team_filter()

    def _apply_team_filter(self) -> None:
        if self._selected_team:
            self.period_sprint_model.set_items([s for s in self._all_sprints if s["team"] == self._selected_team])
            board = next((b for b in self._all_board_aggregates if b.board_name == self._selected_team), None)
            if board:
                self._period_kpi = {
                    "boards": 1,
                    "sprints": board.sprints_count,
                    "donePoints": self.format_number(board.done_points),
                    "avgVelocity": self.format_number(board.avg_velocity),
                    "completion": self.format_percent(board.completion_pct),
                    "notDone": board.not_done_count,
                    "spillover": board.spillover_count,
                    "spilloverCompleted": board.spillover_completed,
                    "active": board.active_count,
                    "backlog": board.backlog_count,
                    "reassigned": board.reassigned_count,
                    "completedLater": board.completed_later_count,
                    "observedOtherBoard": board.observed_other_board_count,
                    "notObserved": board.not_observed_count,
                    "unknown": board.unknown_count,
                    "unestimated": board.unestimated_count,
                    "removed": board.removed_count,
                    "removedPoints": self.format_number(board.removed_points),
                    "flowEfficiency": self.format_percent(board.flow_efficiency_pct),
                }
                self.periodKpiChanged.emit()
        else:
            self.period_sprint_model.set_items(self._all_sprints)
            self._period_kpi = dict(self._all_portfolio_kpi)
            self.periodKpiChanged.emit()

    @pyqtSlot(str)
    def setBacklogSort(self, sort_key: str) -> None:
        allowed = {"stale", "aging365", "forgotten"}
        self._backlog_sort = "" if self._backlog_sort == sort_key else (sort_key if sort_key in allowed else "")
        self.backlogSortChanged.emit()
        self.backlogAgeFilterChanged.emit()
        
    @pyqtProperty(int, notify=settingsChanged)
    def warnDays(self) -> int:
        return self.config.warn_days if self.config else Config().warn_days

    @pyqtProperty(int, notify=settingsChanged)
    def forgottenAgeDays(self) -> int:
        return self.config.forgotten_age_days if self.config else Config().forgotten_age_days

    @pyqtProperty(int, notify=settingsChanged)
    def firstCheckDelayMinutes(self) -> int:
        return self.config.first_check_delay_minutes if self.config else Config().first_check_delay_minutes

    @pyqtProperty(int, notify=settingsChanged)
    def checkIntervalMinutes(self) -> int:
        return self.config.check_interval_minutes if self.config else Config().check_interval_minutes

    @pyqtProperty(int, notify=settingsChanged)
    def connectTimeoutSeconds(self) -> int:
        return self.config.connect_timeout_seconds if self.config else Config().connect_timeout_seconds

    @pyqtProperty(str, notify=settingsChanged)
    def targetEndField(self) -> str:
        return self.config.target_end_field if self.config else Config().target_end_field

    @pyqtProperty(bool, notify=settingsChanged)
    def includeActiveSprints(self) -> bool:
        return self.config.include_active_sprints if self.config else Config().include_active_sprints

    @pyqtProperty(bool, notify=settingsChanged)
    def analyticsEnabled(self) -> bool:
        return self.config.analytics_enabled if self.config else Config().analytics_enabled

    @pyqtProperty(str, notify=settingsChanged)
    def periodStart(self) -> str:
        value = self.config.period_start if self.config else None
        return format_period_date(value or default_period()[0])

    @pyqtProperty(str, notify=settingsChanged)
    def periodEnd(self) -> str:
        value = self.config.period_end if self.config else None
        return format_period_date(value or default_period()[1])

    @pyqtProperty(str, notify=settingsChanged)
    def storyPointsField(self) -> str:
        return self.config.story_points_field if self.config else Config().story_points_field

    @pyqtProperty(int, notify=settingsChanged)
    def changelogCacheTtlHours(self) -> int:
        return self.config.changelog_cache_ttl_hours if self.config else Config().changelog_cache_ttl_hours

    @pyqtProperty(bool, notify=settingsChanged)
    def showExternalAssignees(self) -> bool:
        return self.config.show_external_assignees if self.config else Config().show_external_assignees

    @pyqtProperty(int, notify=settingsChanged)
    def parallelWorkers(self) -> int:
        return self.config.parallel_workers if self.config else Config().parallel_workers

    @pyqtProperty(str, notify=settingsChanged)
    def doneStatusesText(self) -> str:
        return "\n".join(self.config.done_statuses) if self.config else ""

    @pyqtProperty(str, notify=settingsChanged)
    def activeStatusesText(self) -> str:
        return "\n".join(self.config.active_statuses) if self.config else ""

    @pyqtProperty(str, notify=settingsChanged)
    def waitingStatusesText(self) -> str:
        return "\n".join(self.config.waiting_statuses) if self.config else ""

    def set_status(self, text: str) -> None:
        self._status_text = text
        self.statusTextChanged.emit()

    def set_period_status(self, text: str) -> None:
        self._period_status_text = text
        self.periodStatusTextChanged.emit()

    def start_timers(self) -> None:
        if not self.config or self.setupRequired or self._api_forbidden:
            return
        self.first_timer.start(max(0, self.config.first_check_delay_minutes) * 60 * 1000)

    def start_interval_timer(self) -> None:
        if not self._api_forbidden and self.config and self.config.check_interval_minutes > 0:
            self.interval_timer.start(self.config.check_interval_minutes * 60 * 1000)

    def save_items_text(self, text: str) -> None:
        if not self.config:
            self.config = Config()
        self.config.items = items_from_text(text)
        save_config(DEFAULT_CONFIG, self.config)
        self.itemsTextChanged.emit()

    def emit_settings_saved(self) -> None:
        self._api_forbidden = False
        self.jiraUrlChanged.emit()
        self.usernameChanged.emit()
        self.tokenChanged.emit()
        self.passwordChanged.emit()
        self.needsLoginChanged.emit()
        self.setupRequiredChanged.emit()
        self.settingsChanged.emit()

    def _clear_account_data(self) -> None:
        for model in (
            self.task_model, self.period_board_model, self.period_sprint_model,
            self.period_all_sprint_model, self.assignee_model, self.backlog_health_model,
            self._chart_slots_model,
        ):
            if model is not None:
                model.set_items([])
        self._errors_text = ""
        self._period_errors_text = ""
        self._period_kpi = {}
        self._all_portfolio_kpi = {}
        self._assignee_kpi = {}
        self._assignee_board_filter = ""
        self._assignee_all_items = []
        self._assignee_board_names = []
        self._all_boards = []
        self._all_sprints = []
        self._chart_slots = []
        self._all_board_aggregates = []
        self._board_id_by_name = {}
        self._selected_team = ""
        self._period_last_refresh = ""
        self._period_from_cache = False
        self._period_fresh_applied = False
        self.errorsTextChanged.emit()
        self.periodErrorsTextChanged.emit()
        self.periodKpiChanged.emit()
        self.assigneeKpiChanged.emit()
        self.assigneeBoardFilterChanged.emit()
        self.assigneeBoardNamesChanged.emit()
        self.periodLastRefreshChanged.emit()
        self.periodFromCacheChanged.emit()
        self.backlogHealthChanged.emit()

    @pyqtSlot(str, str, str, str, str)
    def saveCredentials(self, jira_url: str, username: str, token: str, password: str, items_text: str) -> None:
        if self._checking or self._period_checking:
            return
        previous_account = (self._jira_url, self._username)
        self._jira_url = normalize_jira_url(jira_url)
        self._username = username.strip()
        self._token = token.strip()
        self._password = password.strip()
        self._auth_failed = False
        save_env(DEFAULT_ENV, {"JIRA_URL": self._jira_url, "JIRA_USERNAME": self._username, "JIRA_TOKEN": self._token, "JIRA_PASSWORD": self._password})
        self.save_items_text(items_text)
        self.env = env_values(DEFAULT_ENV)
        if previous_account != (self._jira_url, self._username):
            self._clear_account_data()
        self.emit_settings_saved()
        self.set_status(_("Data saved"))
        self.refresh(False)
        self.start_timers()

    @pyqtSlot(str, str, str, str, str, bool, str, str, result=bool)
    def saveInitialSetup(
        self,
        jira_url: str,
        username: str,
        token: str,
        password: str,
        items_text: str,
        analytics_enabled: bool,
        period_start_text: str,
        period_end_text: str,
    ) -> bool:
        jira_url = normalize_jira_url(jira_url)
        username = username.strip()
        token = token.strip()
        password = password.strip()
        if not jira_url or not username or not (token or password):
            self.set_status(_("Jira URL, username and token or password are required"))
            return False

        start_raw = period_start_text.strip()
        end_raw = period_end_text.strip()
        start = parse_period_date(start_raw)
        end = parse_period_date(end_raw)
        if analytics_enabled and (not start_raw or not end_raw):
            self.set_status(_("Both period dates are required when analytics is enabled"))
            return False
        if bool(start_raw) != bool(end_raw):
            self.set_status(_("Both period dates must be set or both be empty"))
            return False
        if (start_raw and not start) or (end_raw and not end):
            self.set_status(_("Period dates must use DD-MM-YYYY"))
            return False
        if start and end and end < start:
            self.set_status(_("Period end must be greater than or equal to period start"))
            return False

        config = replace(
            self.config or Config(),
            items=items_from_text(items_text),
            analytics_enabled=analytics_enabled,
            period_start=start,
            period_end=end,
        )
        snapshots = {
            path: (path.exists(), path.read_bytes() if path.exists() else b"", path.stat().st_mode & 0o777 if path.exists() else 0)
            for path in (DEFAULT_ENV, DEFAULT_CONFIG)
        }
        try:
            save_env(DEFAULT_ENV, {"JIRA_URL": jira_url, "JIRA_USERNAME": username, "JIRA_TOKEN": token, "JIRA_PASSWORD": password})
            save_config(DEFAULT_CONFIG, config)
        except OSError as exc:
            for path, (existed, content, mode) in snapshots.items():
                try:
                    if existed:
                        path.write_bytes(content)
                        path.chmod(mode)
                    elif path.exists():
                        path.unlink()
                except OSError:
                    pass
            self.set_status(_("Failed to save setup: %s") % exc)
            return False

        self.config = config
        if (self._jira_url, self._username) != (jira_url, username):
            self._clear_account_data()
        self._jira_url = jira_url
        self._username = username
        self._token = token
        self._password = password
        self._auth_failed = False
        self.env = env_values(DEFAULT_ENV)
        self.itemsTextChanged.emit()
        self.emit_settings_saved()
        self.set_status(_("Setup saved"))
        self.refresh(False)
        self.start_timers()
        return True

    @pyqtSlot(str, str, str, str, int, int, int, int, int, str, bool)
    def saveSettings(
        self,
        jira_url: str,
        username: str,
        token: str,
        password: str,
        warn_days: int,
        first_delay: int,
        interval: int,
        timeout: int,
        forgotten_age_days: int,
        target_field: str,
        include_active_sprints: bool,
    ) -> None:
        if self._checking or self._period_checking:
            return
        previous_account = (self._jira_url, self._username)
        self._jira_url = normalize_jira_url(jira_url)
        self._username = username.strip()
        self._token = token.strip()
        self._password = password.strip()
        self._auth_failed = False
        if not self.config:
            self.config = Config()
        self.config.warn_days = max(0, warn_days)
        self.config.forgotten_age_days = max(1, forgotten_age_days)
        self.config.first_check_delay_minutes = max(0, first_delay)
        self.config.check_interval_minutes = max(0, interval)
        self.config.connect_timeout_seconds = max(1, timeout)
        self.config.target_end_field = target_field.strip() or Config().target_end_field
        self.config.include_active_sprints = include_active_sprints
        save_env(DEFAULT_ENV, {"JIRA_URL": self._jira_url, "JIRA_USERNAME": self._username, "JIRA_TOKEN": self._token, "JIRA_PASSWORD": self._password})
        save_config(DEFAULT_CONFIG, self.config)
        self.env = env_values(DEFAULT_ENV)
        if previous_account != (self._jira_url, self._username):
            self._clear_account_data()
        self.emit_settings_saved()
        self.set_status(_("Settings saved"))

    @pyqtSlot(str, int, bool, int, str, str, str)
    def saveAnalyticsSettings(
        self,
        story_points_field: str,
        changelog_cache_ttl_hours: int,
        show_external_assignees: bool,
        parallel_workers: int,
        done_statuses_text: str,
        active_statuses_text: str,
        waiting_statuses_text: str,
    ) -> None:
        if self._checking or self._period_checking:
            return
        if not self.config:
            self.config = Config()
        self.config.story_points_field = story_points_field.strip() or Config().story_points_field
        self.config.changelog_cache_ttl_hours = max(1, changelog_cache_ttl_hours)
        self.config.show_external_assignees = show_external_assignees
        self.config.parallel_workers = max(1, parallel_workers)
        done = [line.strip() for line in done_statuses_text.splitlines() if line.strip()]
        active = [line.strip() for line in active_statuses_text.splitlines() if line.strip()]
        waiting = [line.strip() for line in waiting_statuses_text.splitlines() if line.strip()]
        if done:
            self.config.done_statuses = done
        if active:
            self.config.active_statuses = active
        if waiting:
            self.config.waiting_statuses = waiting
        save_config(DEFAULT_CONFIG, self.config)
        self.settingsChanged.emit()
        self.set_status(_("Analytics settings saved"))

    @pyqtSlot(bool)
    def refresh(self, automatic: bool = False) -> None:
        if self._checking:
            return
        if not self.config:
            self.set_status(_("Config file not found: %s") % DEFAULT_CONFIG)
            return
        if self.setupRequired:
            self.set_status(_("Initial setup required"))
            return
        self.set_status(_("Connecting"))
        self._checking = True
        self.checkingChanged.emit()
        self._thread = QThread(self)
        self._worker = RefreshWorker(self.env, self.config, automatic)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self.refresh_done)
        self._worker.failed.connect(self.refresh_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self.thread_done)
        self._thread.start()

    @pyqtSlot(object, bool)
    def refresh_done(self, result: object, automatic: bool) -> None:
        alerts = result.alerts
        self._errors_text = "\n".join(result.errors)
        self.errorsTextChanged.emit()
        self.task_model.set_items([{**asdict(alert), "target_end": alert.target_end.isoformat()} for alert in alerts])
        if automatic and self.config and self.config.notifications_enabled:
            for alert in alerts:
                notify(*message_from_alert(alert))
        if alerts:
            status = _("Found issues: %s") % len(alerts)
        else:
            status = _("No issues found")
        if result.errors:
            status = _("%s, errors: %s") % (status, len(result.errors))
        self.set_status(status)
        if not automatic:
            self._api_forbidden = False
        if not self._api_forbidden:
            self.start_interval_timer()

    def set_auth_failed(self, error: str) -> None:
        self._auth_failed = True
        self.first_timer.stop()
        self.interval_timer.stop()
        self.set_status(_("Authentication failed: %s") % error)
        self.setupRequiredChanged.emit()
        self.authenticationFailed.emit()

    def set_api_forbidden(self, error: str) -> None:
        self._api_forbidden = True
        self.first_timer.stop()
        self.interval_timer.stop()
        self.set_status(_("Jira API access forbidden: %s") % error)

    @pyqtSlot(str, int)
    def refresh_failed(self, error: str, status_code: int = 0) -> None:
        if status_code == 401:
            message = _("Authentication failed: %s") % error
        elif status_code == 403:
            message = _("Jira API access forbidden: %s") % error
        else:
            message = _("Connection error: %s") % error
        print(message, file=sys.stderr)
        self._errors_text = error
        self.errorsTextChanged.emit()
        if status_code == 401:
            self.set_auth_failed(error)
        elif status_code == 403:
            self.set_api_forbidden(error)
        else:
            self.set_status(message)
            self.start_interval_timer()

    @pyqtSlot()
    @pyqtSlot(bool)
    def refreshPeriod(self, full_refresh: bool = False) -> None:
        if self._period_checking:
            return
        if not self.config:
            self.set_period_status(_("Config file not found: %s") % DEFAULT_CONFIG)
            return
        if not self.config.analytics_enabled:
            self.set_period_status(_("Period analytics disabled in config"))
            return
        if self.setupRequired:
            self.set_period_status(_("Initial setup required"))
            return

        self._period_checking = True
        self._period_fresh_applied = False
        self.periodCheckingChanged.emit()

        if not self._period_kpi:
            self.start_period_cache_load()
        self.set_period_status(_("Loading period analytics"))
        self._period_thread = QThread(self)
        self._period_worker = PeriodRefreshWorker(self.env, self.config, full_refresh)
        self._period_worker.moveToThread(self._period_thread)
        self._period_thread.started.connect(self._period_worker.run)
        self._period_worker.finished.connect(self.period_refresh_done)
        self._period_worker.failed.connect(self.period_refresh_failed)
        self._period_worker.finished.connect(self._period_thread.quit)
        self._period_worker.failed.connect(self._period_thread.quit)
        self._period_worker.progress.connect(self._on_period_progress)
        self._period_thread.finished.connect(self._period_worker.deleteLater)
        self._period_thread.finished.connect(self.period_thread_done)
        self._period_thread.start()

    @pyqtSlot()
    def loadCachedAnalytics(self) -> None:
        if self._period_kpi or self._period_checking:
            return
        if self.config and self.config.analytics_enabled and not self.setupRequired:
            self.start_period_cache_load()

    def start_period_cache_load(self) -> None:
        if self._period_cache_thread and self._period_cache_thread.isRunning():
            return
        if not self.config:
            return
        self._period_cache_thread = QThread(self)
        self._period_cache_worker = PeriodCacheLoadWorker(self.env, self.config)
        self._period_cache_worker.moveToThread(self._period_cache_thread)
        self._period_cache_thread.started.connect(self._period_cache_worker.run)
        self._period_cache_worker.finished.connect(self.period_cache_loaded)
        self._period_cache_worker.finished.connect(self._period_cache_thread.quit)
        self._period_cache_thread.finished.connect(self._period_cache_worker.deleteLater)
        self._period_cache_thread.finished.connect(self.period_cache_thread_done)
        self._period_cache_thread.start()

    @pyqtSlot(object, str)
    def period_cache_loaded(self, result: object, signature: str) -> None:
        if not self.config or signature != _config_signature(self.env, self.config):
            return
        if result is None:
            return
        if self._period_fresh_applied or (self._period_kpi and not self._period_checking):
            return
        self.period_refresh_done(result, from_cache=True)
        if self._period_checking:
            self.set_period_status(_("Updating from Jira..."))
        else:
            self.set_period_status(_("Loaded from cache — click Refresh to update"))

    @pyqtSlot()
    def period_cache_thread_done(self) -> None:
        self._period_cache_worker = None
        self._period_cache_thread = None

    @pyqtSlot(str, int, int)
    def _on_period_progress(self, text: str, current: int, total: int) -> None:
        self._period_progress_text = text
        self._period_progress_current = current
        self._period_progress_total = total
        self.periodProgressTextChanged.emit()
        self.periodProgressCurrentChanged.emit()
        self.periodProgressTotalChanged.emit()

    @pyqtSlot(object)
    def period_refresh_done(self, result: object, from_cache: bool = False) -> None:
        portfolio = result.portfolio
        self._period_kpi = {
            "boards": portfolio.boards_count,
            "sprints": portfolio.sprints_count,
            "donePoints": self.format_number(portfolio.done_points),
            "avgVelocity": self.format_number(portfolio.avg_velocity),
            "completion": self.format_percent(portfolio.completion_pct),
            "notDone": portfolio.not_done_count,
            "spillover": portfolio.spillover_count,
            "spilloverCompleted": portfolio.spillover_completed,
            "active": portfolio.active_count,
            "backlog": portfolio.backlog_count,
            "reassigned": portfolio.reassigned_count,
            "completedLater": portfolio.completed_later_count,
            "observedOtherBoard": portfolio.observed_other_board_count,
            "notObserved": portfolio.not_observed_count,
            "unknown": portfolio.unknown_count,
            "unestimated": portfolio.unestimated_count,
            "removed": portfolio.removed_count,
            "removedPoints": self.format_number(portfolio.removed_points),
            "flowEfficiency": self.format_percent(portfolio.flow_efficiency_pct),
        }
        self.periodKpiChanged.emit()
        self._all_portfolio_kpi = dict(self._period_kpi)
        self._period_errors_text = "\n".join(result.errors)
        self.periodErrorsTextChanged.emit()
        self._all_board_aggregates = list(result.boards)
        self._board_id_by_name = {b.name: b.id for b in self.config.boards} if self.config else {}
        self._all_boards = [self.board_item(board) for board in result.boards]
        self._all_sprints = [self.sprint_item(sprint) for sprint in result.sprints]
        self._all_sprints.sort(key=lambda s: (str(s["team"]), str(s["startDate"]), str(s["endDate"]), int(s.get("sprintId", 0))))
        self.add_period_sprint_labels(self._all_sprints)
        self.period_board_model.set_items(self._all_boards)
        self.period_all_sprint_model.set_items(self._all_sprints)
        self._chart_slots = self._build_chart_slots(result)
        self._chart_slots_model.set_items(self._chart_slots)
        self._selected_team = ""
        self._apply_team_filter()
        self._assignee_all_items = [self.assignee_item(a) for a in result.assignees]
        self.assignee_model.set_items(self._assignee_all_items)
        assignees_total = len(result.assignees)
        self._assignee_kpi = {
            "people": assignees_total,
            "doneSp": self.format_number(sum(a.done_sp for a in result.assignees)),
            "avgCompletion": self.format_percent(sum(a.completion_pct for a in result.assignees) / assignees_total) if assignees_total else "0%",
            "totalSpillover": sum(a.spillover for a in result.assignees),
        }
        self.assigneeKpiChanged.emit()
        self._assignee_board_names = sorted({a.board_name for a in result.assignees})
        self.assigneeBoardNamesChanged.emit()
        self._apply_assignee_filter()
        health_items: list[object] = []
        for bh in getattr(result, "backlog_health", []) or []:
            health_items.append({
                "team": bh.board_name,
                "totalOpen": bh.total_open,
                "aging30": bh.aging_30plus,
                "aging90": bh.aging_90plus,
                "aging180": bh.aging_180plus,
                "aging365": bh.aging_365plus,
                "agingAvg": self.format_number(bh.aging_avg_days),
                "agingMax": bh.aging_max_days,
                "staleCount": bh.stale_count,
                "items": [{"key": i.key, "summary": i.summary,
                           "age": i.age_days, "sinceUpdate": i.days_since_update,
                           "status": i.status, "assignee": i.assignee}
                           for i in getattr(bh, "items", []) or []],
            })
        self.backlog_health_model.set_items(health_items)
        self.backlogHealthChanged.emit()
        if self._period_checking and not from_cache:
            self._api_forbidden = False
            self._period_fresh_applied = True
            self.start_period_cache_save(result)
            self._period_last_refresh = datetime.now().strftime("%H:%M")
            self._period_from_cache = False
        else:
            self._period_from_cache = True
        self.periodLastRefreshChanged.emit()
        self.periodFromCacheChanged.emit()
        status = _("Period analytics loaded: %s sprints") % portfolio.sprints_count
        if result.errors:
            status = _("%s, errors: %s") % (status, len(result.errors))
        self.set_period_status(status)

    def start_period_cache_save(self, result: object, env: dict[str, str] | None = None, config: Config | None = None) -> None:
        env_snapshot = dict(env if env is not None else self.env)
        config_snapshot = deepcopy(config if config is not None else self.config)
        if self._period_cache_save_thread is not None:
            self._period_cache_save_pending = (result, env_snapshot, config_snapshot)
            return
        if not config_snapshot:
            return
        self._period_cache_save_thread = QThread(self)
        self._period_cache_save_worker = PeriodCacheSaveWorker(result, env_snapshot, config_snapshot)
        self._period_cache_save_worker.moveToThread(self._period_cache_save_thread)
        self._period_cache_save_thread.started.connect(self._period_cache_save_worker.run)
        self._period_cache_save_worker.failed.connect(self.period_cache_save_failed)
        self._period_cache_save_worker.succeeded.connect(self.period_cache_save_succeeded)
        self._period_cache_save_worker.finished.connect(self._period_cache_save_thread.quit)
        self._period_cache_save_thread.finished.connect(self._period_cache_save_worker.deleteLater)
        self._period_cache_save_thread.finished.connect(self.period_cache_save_thread_done)
        self._period_cache_save_thread.start()

    @pyqtSlot()
    def period_cache_save_thread_done(self) -> None:
        pending = self._period_cache_save_pending
        self._period_cache_save_pending = None
        self._period_cache_save_worker = None
        self._period_cache_save_thread = None
        if pending and not self._shutting_down:
            self.start_period_cache_save(*pending)

    @pyqtSlot(str, str)
    def period_cache_save_failed(self, error: str, signature: str) -> None:
        if not self.config or signature != _config_signature(self.env, self.config):
            return
        message = _("Failed to save period cache: %s") % error
        print(message, file=sys.stderr)
        errors = [item for item in self._period_errors_text.splitlines() if item]
        if self._period_cache_save_error in errors:
            errors.remove(self._period_cache_save_error)
        if message not in errors:
            errors.append(message)
        self._period_cache_save_error = message
        self._period_errors_text = "\n".join(errors)
        self.periodErrorsTextChanged.emit()
        self.set_period_status(message)

    @pyqtSlot(str)
    def period_cache_save_succeeded(self, signature: str) -> None:
        if not self.config or signature != _config_signature(self.env, self.config) or not self._period_cache_save_error:
            return
        error = self._period_cache_save_error
        self._period_cache_save_error = ""
        self._period_errors_text = "\n".join(
            item for item in self._period_errors_text.splitlines() if item and item != error
        )
        self.periodErrorsTextChanged.emit()
        if self._period_status_text == error:
            sprints = self._period_kpi.get("sprints")
            self.set_period_status(_("Period analytics loaded: %s sprints") % sprints if sprints is not None else "")

    @pyqtSlot(str, int)
    def period_refresh_failed(self, error: str, status_code: int = 0) -> None:
        print(_("Period analytics error: %s") % error, file=sys.stderr)
        self._period_errors_text = error
        self.periodErrorsTextChanged.emit()
        if status_code == 401:
            self.set_auth_failed(error)
            self.set_period_status(_("Authentication failed: %s") % error)
        elif status_code == 403:
            self.set_api_forbidden(error)
            self.set_period_status(_("Jira API access forbidden: %s") % error)
        else:
            self.set_period_status(_("Period analytics failed: %s") % error)

    @pyqtSlot()
    def period_thread_done(self) -> None:
        self._period_checking = False
        self._period_worker = None
        self._period_thread = None
        self.periodCheckingChanged.emit()

    def board_item(self, board: object) -> dict[str, object]:
        return {
            "team": board.board_name,
            "sprints": board.sprints_count,
            "totalPoints": self.format_number(board.total_points),
            "donePoints": self.format_number(board.done_points),
            "avgVelocity": self.format_number(board.avg_velocity),
            "completion": self.format_percent(board.completion_pct),
            "notDone": board.not_done_count,
            "spillover": board.spillover_count,
            "spilloverCompleted": board.spillover_completed,
            "active": board.active_count,
            "backlog": board.backlog_count,
            "reassigned": board.reassigned_count,
            "completedLater": board.completed_later_count,
            "observedOtherBoard": board.observed_other_board_count,
            "notObserved": board.not_observed_count,
            "unknown": board.unknown_count,
            "unestimated": board.unestimated_count,
            "removed": board.removed_count,
            "removedPoints": self.format_number(board.removed_points),
            "flowEfficiency": self.format_percent(board.flow_efficiency_pct),
        }

    def sprint_item(self, sprint: object) -> dict[str, object]:
        board_id = self._board_id_by_name.get(sprint.board_name, 0)
        return {
            "team": sprint.board_name,
            "sprint": sprint.sprint_name,
            "sprintId": sprint.sprint_id,
            "boardId": board_id,
            "startDate": sprint.start_date.isoformat(),
            "endDate": sprint.end_date.isoformat(),
            "period": period_label_for_sprint(sprint.start_date, sprint.end_date),
            "periodSprintLabel": "",
            "dates": f"{sprint.start_date.isoformat()} - {sprint.end_date.isoformat()}",
            "total": sprint.total_issues,
            "done": sprint.done_issues,
            "points": self.format_number(sprint.done_points),
            "totalPoints": self.format_number(sprint.total_points),
            "completion": self.format_percent(sprint.completion_pct),
            "notDone": sprint.not_done_count,
            "spillover": sprint.spillover_count,
            "spilloverCompleted": sprint.spillover_completed,
            "active": sprint.active_count,
            "backlog": sprint.backlog_count,
            "reassigned": sprint.reassigned_count,
            "completedLater": sprint.completed_later_count,
            "observedOtherBoard": sprint.observed_other_board_count,
            "notObserved": sprint.not_observed_count,
            "unknown": sprint.unknown_count,
            "removed": sprint.removed_count,
            "removedPoints": self.format_number(sprint.removed_points),
            "flowEfficiency": self.format_percent(sprint.flow_efficiency_pct),
            "leadTimeP50": self.format_number(sprint.lead_time_p50),
            "leadTimeP85": self.format_number(sprint.lead_time_p85),
            "leadTimeP95": self.format_number(sprint.lead_time_p95),
            "agingAvg": self.format_number(sprint.aging_avg_days),
            "agingMax": self.format_number(sprint.aging_max_days),
            "statusDistribution": json.dumps(sprint.status_distribution, ensure_ascii=False),
        }

    @staticmethod
    def add_period_sprint_labels(sprints: list[dict[str, object]]) -> None:
        counters: dict[tuple[str, str], int] = {}
        for sprint in sorted(sprints, key=lambda item: (str(item["team"]), str(item["startDate"]), str(item["endDate"]))):
            key = (str(sprint["team"]), str(sprint["period"]))
            counters[key] = counters.get(key, 0) + 1
            sprint["periodSprintLabel"] = f"{sprint['period']}S{counters[key]}"

    def _build_chart_slots(self, result: object) -> list[dict[str, object]]:
        return build_chart_slots(result.sprints, period_fn=period_label_for_sprint)

    @staticmethod
    def format_number(value: float) -> str:
        return str(int(value)) if value == int(value) else f"{value:.1f}"

    @staticmethod
    def format_percent(value: float) -> str:
        return f"{value:.0f}%"

    def assignee_item(self, a: object) -> dict[str, object]:
        return {
            "name": a.display_name,
            "team": a.board_name,
            "tasks": a.total_assigned,
            "done": a.done,
            "doneSp": self.format_number(a.done_sp),
            "totalSp": self.format_number(a.total_sp),
            "completion": self.format_percent(a.completion_pct),
            "spillover": a.spillover,
            "spilloverCompleted": a.spillover_completed,
            "active": a.active,
            "backlog": a.backlog,
            "reassigned": a.reassigned,
            "completedLater": a.completed_later,
            "observedOtherBoard": a.observed_other_board,
            "notObserved": a.not_observed,
            "unknown": a.unknown,
            "items": [{"key": i.key, "summary": i.summary, "sprint": i.sprint_name,
                       "points": self.format_number(i.points) if i.points is not None else "–",
                       "category": i.category,
                       "routeCategory": i.route_category,
                       "completedLater": i.completed_later,
                       "currentStatus": i.current_status,
                       "currentStatusCategory": i.current_status_category,
                       "originalName": i.original_name or ""} for i in a.items],
        }

    @pyqtProperty("QVariantMap", notify=assigneeKpiChanged)
    def assigneeKpi(self) -> dict[str, object]:
        return self._assignee_kpi

    @pyqtProperty(str, notify=assigneeBoardFilterChanged)
    def assigneeBoardFilter(self) -> str:
        return self._assignee_board_filter

    @pyqtProperty("QVariantList", notify=assigneeBoardNamesChanged)
    def assigneeBoardNames(self) -> list[str]:
        return self._assignee_board_names

    @pyqtSlot(str)
    def setAssigneeBoardFilter(self, board_name: str) -> None:
        self._assignee_board_filter = board_name
        self.assigneeBoardFilterChanged.emit()
        self._apply_assignee_filter()

    def _apply_assignee_filter(self) -> None:
        self._assignee_proxy.set_board_filter(self._assignee_board_filter)
        hide_external = not (self.config and self.config.show_external_assignees)
        self._assignee_proxy.set_hide_external(hide_external)
        filtered = self._assignee_all_items
        if self._assignee_board_filter:
            filtered = [item for item in filtered if item["team"] == self._assignee_board_filter]
        if hide_external:
            filtered = [item for item in filtered if item["name"] != "External / Other"]
        self.assigneeModelChanged.emit()
        unique_names = len({item["name"] for item in filtered})
        total_spillover = sum(item["spillover"] for item in filtered)
        done_sp = sum(float(str(item["doneSp"])) for item in filtered)
        completions = [float(item["completion"].rstrip("%")) for item in filtered if item["completion"]]
        avg_completion = sum(completions) / len(completions) if completions else 0
        self._assignee_kpi = {
            "people": unique_names,
            "doneSp": self.format_number(done_sp),
            "avgCompletion": self.format_percent(avg_completion) if completions else "0%",
            "totalSpillover": total_spillover,
        }
        self.assigneeKpiChanged.emit()

    @pyqtSlot(str)
    def saveItems(self, text: str) -> None:
        self.save_items_text(text)
        self.set_status(_("List saved"))
        self.refresh(False)

    @pyqtSlot()
    def thread_done(self) -> None:
        self._checking = False
        self._worker = None
        self._thread = None
        self.checkingChanged.emit()
        if self._quit_after_refresh:
            self.app.quit()

    @pyqtSlot()
    def toggleNotifications(self) -> None:
        if not self.config or self.setupRequired:
            return
        self.config.notifications_enabled = not self.config.notifications_enabled
        save_config(DEFAULT_CONFIG, self.config)
        self.notificationsEnabledChanged.emit()

    @pyqtSlot(str)
    def openIssue(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))


class ModuleApi(QObject):
    checkingChanged = pyqtSignal()
    errorsTextChanged = pyqtSignal()
    itemsTextChanged = pyqtSignal()
    periodCheckingChanged = pyqtSignal()
    periodStatusTextChanged = pyqtSignal()
    periodErrorsTextChanged = pyqtSignal()
    periodFromCacheChanged = pyqtSignal()
    periodLastRefreshChanged = pyqtSignal()
    periodKpiChanged = pyqtSignal()
    selectedTeamChanged = pyqtSignal()
    backlogSortChanged = pyqtSignal()
    backlogAgeFilterChanged = pyqtSignal()
    backlogActiveStatusesChanged = pyqtSignal()
    forgottenAgeDaysChanged = pyqtSignal()
    assigneeBoardNamesChanged = pyqtSignal()
    assigneeKpiChanged = pyqtSignal()

    def __init__(
        self,
        backend: Backend,
        task_model: QObject,
        period_board_model: QObject,
        period_sprint_model: QObject,
        backlog_health_model: QObject,
        assignee_model: QObject,
        chart_slots_model: QObject,
        open_url: Callable[[QUrl], object] = QDesktopServices.openUrl,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend = backend
        self._task_model = task_model
        self._period_board_model = period_board_model
        self._period_sprint_model = period_sprint_model
        self._backlog_health_model = backlog_health_model
        self._assignee_model = assignee_model
        self._chart_slots_model = chart_slots_model
        self._open_url = open_url

        backend.checkingChanged.connect(self.checkingChanged)
        backend.errorsTextChanged.connect(self.errorsTextChanged)
        backend.itemsTextChanged.connect(self.itemsTextChanged)
        backend.periodCheckingChanged.connect(self.periodCheckingChanged)
        backend.periodStatusTextChanged.connect(self.periodStatusTextChanged)
        backend.periodErrorsTextChanged.connect(self.periodErrorsTextChanged)
        backend.periodFromCacheChanged.connect(self.periodFromCacheChanged)
        backend.periodLastRefreshChanged.connect(self.periodLastRefreshChanged)
        backend.periodKpiChanged.connect(self.periodKpiChanged)
        backend.settingsChanged.connect(self.selectedTeamChanged)
        backend.settingsChanged.connect(self.backlogActiveStatusesChanged)
        backend.settingsChanged.connect(self.forgottenAgeDaysChanged)
        backend.backlogSortChanged.connect(self.backlogSortChanged)
        backend.backlogAgeFilterChanged.connect(self.backlogAgeFilterChanged)
        backend.assigneeBoardNamesChanged.connect(self.assigneeBoardNamesChanged)
        backend.assigneeKpiChanged.connect(self.assigneeKpiChanged)

    @pyqtProperty(QObject, constant=True)
    def taskModel(self) -> QObject:
        return self._task_model

    @pyqtProperty(QObject, constant=True)
    def periodBoardModel(self) -> QObject:
        return self._period_board_model

    @pyqtProperty(QObject, constant=True)
    def periodSprintModel(self) -> QObject:
        return self._period_sprint_model

    @pyqtProperty(QObject, constant=True)
    def backlogHealthModel(self) -> QObject:
        return self._backlog_health_model

    @pyqtProperty(QObject, constant=True)
    def assigneeModel(self) -> QObject:
        return self._assignee_model

    @pyqtProperty(QObject, constant=True)
    def chartSlotsModel(self) -> QObject:
        return self._chart_slots_model

    @pyqtProperty(bool, notify=checkingChanged)
    def checking(self) -> bool:
        return self._backend.checking

    @pyqtProperty(str, notify=errorsTextChanged)
    def errorsText(self) -> str:
        return self._backend.errorsText

    @pyqtProperty(str, notify=itemsTextChanged)
    def itemsText(self) -> str:
        return self._backend.itemsText

    @pyqtProperty(bool, notify=periodCheckingChanged)
    def periodChecking(self) -> bool:
        return self._backend.periodChecking

    @pyqtProperty(str, notify=periodStatusTextChanged)
    def periodStatusText(self) -> str:
        return self._backend.periodStatusText

    @pyqtProperty(str, notify=periodErrorsTextChanged)
    def periodErrorsText(self) -> str:
        return self._backend.periodErrorsText

    @pyqtProperty(bool, notify=periodFromCacheChanged)
    def periodFromCache(self) -> bool:
        return self._backend.periodFromCache

    @pyqtProperty(str, notify=periodLastRefreshChanged)
    def periodLastRefresh(self) -> str:
        return self._backend.periodLastRefresh

    @pyqtProperty("QVariantMap", notify=periodKpiChanged)
    def periodKpi(self) -> dict[str, object]:
        return self._backend.periodKpi

    @pyqtProperty(str, notify=selectedTeamChanged)
    def selectedTeam(self) -> str:
        return self._backend.selectedTeam

    @pyqtProperty(str, notify=backlogSortChanged)
    def backlogSort(self) -> str:
        return self._backend.backlogSort

    @pyqtProperty(str, notify=backlogAgeFilterChanged)
    def backlogAgeFilter(self) -> str:
        return self._backend.backlogAgeFilter

    @pyqtProperty(str, notify=backlogActiveStatusesChanged)
    def backlogActiveStatuses(self) -> str:
        return self._backend.backlogActiveStatuses

    @pyqtProperty(int, notify=forgottenAgeDaysChanged)
    def forgottenAgeDays(self) -> int:
        return self._backend.forgottenAgeDays

    @pyqtProperty("QVariantList", notify=assigneeBoardNamesChanged)
    def assigneeBoardNames(self) -> list[str]:
        return self._backend.assigneeBoardNames

    @pyqtProperty("QVariantMap", notify=assigneeKpiChanged)
    def assigneeKpi(self) -> dict[str, object]:
        return self._backend.assigneeKpi

    @pyqtSlot()
    def refreshTarget(self) -> None:
        self._backend.refresh(False)

    @pyqtSlot(str)
    def saveItems(self, text: str) -> None:
        self._backend.saveItems(text)

    @pyqtSlot()
    def refreshPeriod(self) -> None:
        self._backend.refreshPeriod()

    @pyqtSlot(str)
    def selectTeam(self, team: str) -> None:
        self._backend.setSelectedTeam(team)

    @pyqtSlot(str)
    def setBacklogSort(self, sort_key: str) -> None:
        self._backend.setBacklogSort(sort_key)

    @pyqtSlot(str)
    def setAssigneeBoardFilter(self, name: str) -> None:
        self._backend.setAssigneeBoardFilter(name)

    @pyqtSlot(str, result=bool)
    def openIssue(self, key: str) -> bool:
        jira_url = normalize_jira_url(self._backend.jiraUrl)
        if not jira_url or not key or not ISSUE_RE.fullmatch(key.strip()):
            return False
        self._open_url(QUrl(f"{jira_url}/browse/{key.strip()}"))
        return True

    @pyqtSlot(int, int, result=bool)
    def openSprintReport(self, board_id: int, sprint_id: int) -> bool:
        jira_url = normalize_jira_url(self._backend.jiraUrl)
        if not jira_url or board_id <= 0 or sprint_id <= 0:
            return False
        self._open_url(QUrl(f"{jira_url}/secure/RapidBoard.jspa#?rapidView={board_id}&view=reporting&chart=sprintRetrospective&sprint={sprint_id}"))
        return True

    @pyqtSlot(str, int, result=bool)
    def isForgotten(self, status: str, since_update_days: int) -> bool:
        cfg = self._backend.config
        if not cfg:
            return False
        return is_forgotten(status, since_update_days, cfg.forgotten_age_days, cfg.active_statuses)


class AppController(QObject):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.task_model = TaskModel()
        self.period_board_model = DictListModel(["team", "sprints", "totalPoints", "donePoints", "avgVelocity", "completion", "notDone", "spillover", "spilloverCompleted", "active", "backlog", "reassigned", "unestimated", "removed", "removedPoints", "flowEfficiency", "completedLater", "observedOtherBoard", "notObserved", "unknown"])
        sprint_roles = ["team", "sprint", "sprintId", "boardId", "startDate", "endDate", "period", "periodSprintLabel", "dates", "total", "done", "points", "totalPoints", "completion", "notDone", "spillover", "spilloverCompleted", "active", "backlog", "reassigned", "removed", "removedPoints", "flowEfficiency", "leadTimeP50", "leadTimeP85", "leadTimeP95", "agingAvg", "agingMax", "statusDistribution", "completedLater", "observedOtherBoard", "notObserved", "unknown"]
        self.period_sprint_model = DictListModel(sprint_roles)
        self.period_all_sprint_model = DictListModel(sprint_roles)
        self.assignee_model = DictListModel(["name", "team", "tasks", "done", "doneSp", "totalSp", "completion", "spillover", "spilloverCompleted", "active", "backlog", "reassigned", "items", "completedLater", "observedOtherBoard", "notObserved", "unknown"])
        self.backlog_health_model = DictListModel(["team", "totalOpen", "aging30", "aging90", "aging180", "aging365", "agingAvg", "agingMax", "staleCount", "items"])
        self.backend = Backend(self.task_model, self.period_board_model, self.period_sprint_model, self.period_all_sprint_model, self.assignee_model, self.backlog_health_model, app)
        self.module_api = ModuleApi(
            self.backend,
            self.task_model,
            self.period_board_model,
            self.period_sprint_model,
            self.backlog_health_model,
            self.backend._assignee_proxy,
            self.backend._chart_slots_model,
            parent=self,
        )

        required_builtin_ids = {"target_end", "period_analytics", "backlog_health", "assignees", "charts"}
        all_modules, discovery_errors = discover_modules(
            builtin_modules_path(), user_dir=user_modules_path(),
        )
        self._all_modules = all_modules
        found_builtin_ids = {m.module_id for m in all_modules if m.builtin}
        missing = required_builtin_ids - found_builtin_ids
        self._missing_builtins = missing

        for err in discovery_errors:
            print(err, file=sys.stderr)

        self.module_warnings_text = ""
        if discovery_errors:
            self.module_warnings_text = _("Some modules were skipped: %s") % len(discovery_errors)

        self._module_tabs_model = DictListModel(["moduleId", "title", "qmlSource"])
        tabs_data = []
        for m in all_modules:
            title = _c("Main.qml", m.title) if m.builtin else m.title
            tabs_data.append({
                "moduleId": m.module_id,
                "title": title,
                "qmlSource": QUrl.fromLocalFile(str(m.qml_path)).toString(),
            })
        self._module_tabs_model.set_items(tabs_data)

        self.engine = QQmlApplicationEngine()
        self.window = None
        self.tray: QSystemTrayIcon | None = None
        self.menu: QMenu | None = None
        self.actions: list[QAction] = []
        self.sigint_timer: QTimer | None = None
        self._quitting = False

    def setup(self) -> bool:
        if self._missing_builtins:
            print(f"Missing built-in modules: {self._missing_builtins}", file=sys.stderr)
            return False

        for m in self._all_modules:
            if not m.builtin:
                continue
            component = QQmlComponent(self.engine, QUrl.fromLocalFile(str(m.qml_path)))
            if component.isError():
                for err in component.errors():
                    print(f"Preflight error in {m.qml_path}: {err.toString()}", file=sys.stderr)
                return False

        context = self.engine.rootContext()
        context.setContextProperty("backend", self.backend)
        context.setContextProperty("moduleApi", self.module_api)
        context.setContextProperty("moduleTabsModel", self._module_tabs_model)
        context.setContextProperty("moduleWarningsText", self.module_warnings_text)
        self.engine.load(QUrl.fromLocalFile(str(qml_path())))
        if not self.engine.rootObjects():
            return False
        self.window = self.engine.rootObjects()[0]
        if not self.backend.setupRequired:
            self.window.hide()

        icon = app_icon()
        self.app.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self)
        self.menu = QMenu()
        refresh_action = QAction(_("Refresh"), self.menu)
        notify_action = QAction(self.menu)
        quit_action = QAction(_("Quit"), self.menu)
        self.actions = [refresh_action, notify_action, quit_action]

        def update_notify_action() -> None:
            notify_action.setText(_("Notifications: on") if self.backend.notificationsEnabled else _("Notifications: off"))

        refresh_action.triggered.connect(lambda checked=False: self.backend.refresh(False))
        notify_action.triggered.connect(self.backend.toggleNotifications)
        self.backend.setupRequiredChanged.connect(self.show_main_window)
        self.backend.notificationsEnabledChanged.connect(update_notify_action)
        quit_action.triggered.connect(self.quit)
        for action in self.actions:
            self.menu.addAction(action)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(lambda reason: self.show_window(reason))
        update_notify_action()
        self.tray.show()
        if not self.backend.setupRequired:
            self.backend.refresh(False)
        self.backend.start_timers()
        return True

    def show_window(self, reason: object) -> None:
        if reason == QSystemTrayIcon.Trigger and self.window:
            self.show_main_window()

    @pyqtSlot()
    def show_main_window(self) -> None:
        if not self.window:
            return
        self.window.setWindowState(self.window.windowState() & ~Qt.WindowMinimized)
        self.window.show()
        self.window.raise_()
        self.window.requestActivate()

    @pyqtSlot()
    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        if self.backend.checking:
            self.backend._quit_after_refresh = True
            self.backend.set_status(_("Exiting after check"))
            return
        self.app.quit()

    @pyqtSlot()
    def cleanup(self) -> None:
        self.backend._shutting_down = True
        self.backend._period_cache_save_pending = None
        self.backend.first_timer.stop()
        self.backend.interval_timer.stop()
        if self.backend._thread and self.backend._thread.isRunning():
            self.backend._thread.quit()
            self.backend._thread.wait(3000)
        if self.backend._period_thread and self.backend._period_thread.isRunning():
            self.backend._period_thread.quit()
            self.backend._period_thread.wait(3000)
        if self.backend._period_cache_thread and self.backend._period_cache_thread.isRunning():
            self.backend._period_cache_thread.quit()
            self.backend._period_cache_thread.wait(3000)
        if self.backend._period_cache_save_thread and self.backend._period_cache_save_thread.isRunning():
            self.backend._period_cache_save_thread.quit()
            self.backend._period_cache_save_thread.wait(3000)
        if self.window:
            self.window.close()
            self.window.deleteLater()
            self.window = None
            self.app.processEvents()
        self.engine.rootContext().setContextProperty("backend", None)
        self.engine.rootContext().setContextProperty("moduleApi", None)
        self.engine.rootContext().setContextProperty("moduleTabsModel", None)
        self.engine.rootContext().setContextProperty("moduleWarningsText", None)
        self.engine.clearComponentCache()
        if self.tray:
            self.tray.hide()
            self.tray.setContextMenu(None)
            self.tray.deleteLater()
            self.tray = None
        if self.menu:
            self.menu.deleteLater()
            self.menu = None
        self.actions = []


def qml_path() -> Path:
    local = Path(__file__).with_name("qml") / "Main.qml"
    if local.exists():
        return local
    return Path("/usr/share/jira-analytics/qml/Main.qml")


def builtin_modules_path() -> Path:
    local = Path(__file__).resolve().parent / "modules"
    return local if local.exists() else Path("/usr/share/jira-analytics/modules")


def user_modules_path() -> Path:
    return Path.home() / ".local" / "share" / "jira-analytics" / "modules"


def app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(Qt.blue)
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 12, 12)
    painter.setPen(Qt.white)
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(42)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "J")
    painter.end()
    return QIcon(pixmap)


def main() -> int:
    app = QApplication(sys.argv)
    translator = GettextTranslatorQt("jira_qt", get_translations_path(__file__))
    app.installTranslator(translator)
    app.setQuitOnLastWindowClosed(False)
    controller = AppController(app)
    app.aboutToQuit.connect(controller.cleanup)
    if not controller.setup():
        return 1
    signal.signal(signal.SIGINT, lambda *_: controller.quit())
    controller.sigint_timer = QTimer()
    controller.sigint_timer.timeout.connect(lambda: None)
    controller.sigint_timer.start(200)
    try:
        return app.exec_()
    except KeyboardInterrupt:
        controller.quit()
        return 130


if __name__ == "__main__":
    sys.exit(main())
