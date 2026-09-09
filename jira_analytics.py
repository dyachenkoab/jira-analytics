#!/usr/bin/env python3
import base64
import calendar
import concurrent.futures
import dataclasses
import json
import logging
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from translations import _
from jira_period_analytics import AssigneeIssue, AssigneeSummary, BacklogHealth, BacklogItem, BoardAggregate, BoardConfig, BoardStatuses, PeriodReport, PortfolioAggregate, SprintIssues, SprintSummary, StatusClassification, TeamScopeItem, TeamScopeSummary, build_period_report, classify_status, compute_backlog_health, compute_team_scope, is_done_status, parse_date, parse_datetime, resolve_board_statuses, resolve_historical_status, sprint_overlaps_period
from logs_sanitizer import sanitize_json

log = logging.getLogger("jira_analytics")

if os.environ.get("JIRA_ANALYTICS_DEBUG"):
    logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s %(message)s")

APP_NAME = "jira-analytics"
DEFAULT_CONFIG = Path.home() / ".config" / "jira-analytics.yaml"
DEFAULT_ENV = Path.home() / ".config" / "jira-analytics.env"
SYSTEM_CONFIG = Path("/etc/jira-analytics/jira-analytics.yaml")
OLD_DEFAULT_CONFIG = Path.home() / ".config" / "jira-target-alerts.yaml"
OLD_DEFAULT_ENV = Path.home() / ".config" / "jira-target-alerts.env"
OLD_SYSTEM_CONFIG = Path("/etc/jira-target-alerts/jira-target-alerts.yaml")
DEFAULT_ENV_TEXT = "JIRA_URL=\nJIRA_USERNAME=\nJIRA_TOKEN=\nJIRA_PASSWORD=\n"
CHANGELOG_CACHE_DIR = Path.home() / ".cache" / "jira-analytics" / "changelog"
PERIOD_SOURCE_CACHE = Path.home() / ".cache" / "jira-analytics" / "period_source_cache.json"
_PERIOD_SOURCE_SCHEMA = 1
DEFAULT_ITEMS = ['issuetype = Epic AND statusCategory != Done AND "Target end" is not EMPTY']
DEFAULT_DONE_STATUSES = [
    "done",
    "Завершен",
    "Закрыта",
    "Завершена",
    "На ревизии",
    "Ожидает релиз",
    "Отменена",
    "QA-RC",
    "Ожидает закрытия подзадач",
    "Код написан",
]
DEFAULT_ACTIVE_STATUSES = ["In Progress", "Code Review", "Testing", "в работе"]
DEFAULT_WAITING_STATUSES = ["To Do", "Blocked", "Waiting", "Backlog", "Анализ", "Новая", "Открыт", "Пауза", "Нужна информация", "На тестировании"]
ISSUE_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")
BROWSE_RE = re.compile(r"^https?://\S+/browse/([A-Z][A-Z0-9]+-\d+)(?:[/?#].*)?$")

_SENSITIVE_KEYS = ("JIRA_TOKEN", "JIRA_PASSWORD")
_ENCRYPTION_SALT = b"jira-target-alerts-encryption"

_fernet: Fernet | None = None


def http_status_code(exc: BaseException) -> int:
    current: object = exc
    seen: set[int] = set()
    while isinstance(current, BaseException) and id(current) not in seen:
        seen.add(id(current))
        response = getattr(current, "response", None)
        status = getattr(response, "status_code", None) if response is not None else None
        if isinstance(status, int):
            return status
        match = re.search(r"(?:\bHTTP(?: status)?\s*|\bstatus(?: code)?\s*[:=]?\s*|\b(?:Unauthorized|Forbidden)\s*\()([45]\d\d)\b", str(current), re.IGNORECASE)
        if match:
            return int(match.group(1))
        current = getattr(current, "reason", None)
    return 0


def _raise_access_error(exc: BaseException) -> None:
    if http_status_code(exc) in (401, 403):
        raise exc


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        machine_id = Path("/etc/machine-id")
        if not machine_id.exists():
            machine_id = Path("/var/lib/dbus/machine-id")
        raw = machine_id.read_text(encoding="utf-8").strip().encode()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=_ENCRYPTION_SALT,
            iterations=100_000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(raw))
        _fernet = Fernet(key)
    return _fernet


def encrypt_value(value: str) -> str:
    if not value:
        return ""
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt_value(value: str) -> str:
    if not value:
        return ""
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except Exception:
        return value


@dataclass
class Config:
    warn_days: int = 7
    first_check_delay_minutes: int = 2
    check_interval_minutes: int = 60
    connect_timeout_seconds: int = 15
    notifications_enabled: bool = True
    target_end_field: str = "Target end"
    items: list[str] | None = None
    analytics_enabled: bool = True
    period_start: date | None = None
    period_end: date | None = None
    boards: list[BoardConfig] = field(default_factory=list)
    story_points_field: str = "Story points"
    include_active_sprints: bool = True
    changelog_cache_ttl_hours: int = 24
    forgotten_age_days: int = 30
    show_external_assignees: bool = False
    parallel_workers: int = 3
    done_statuses: list[str] = field(default_factory=lambda: DEFAULT_DONE_STATUSES.copy())
    active_statuses: list[str] = field(default_factory=lambda: DEFAULT_ACTIVE_STATUSES.copy())
    waiting_statuses: list[str] = field(default_factory=lambda: DEFAULT_WAITING_STATUSES.copy())


@dataclass
class AlertIssue:
    key: str
    summary: str
    status: str
    assignee: str
    target_end: date
    days: int
    kind: str
    url: str


@dataclass
class CollectResult:
    alerts: list[AlertIssue]
    errors: list[str]


def normalize_jira_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if value and "://" not in value:
        return f"https://{value}"
    return value


def default_period(today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    start_month = ((today.month - 1) // 3) * 3 + 1
    end_month = start_month + 2
    return date(today.year, start_month, 1), date(today.year, end_month, calendar.monthrange(today.year, end_month)[1])


def items_from_text(text: str) -> list[str]:
    items = [line.strip() for line in text.splitlines() if line.strip()]
    return items or DEFAULT_ITEMS.copy()


def issue_key_from_text(text: str) -> str | None:
    text = text.strip()
    if ISSUE_RE.fullmatch(text):
        return text
    match = BROWSE_RE.fullmatch(text)
    return match.group(1) if match else None


def source_kind(text: str) -> str:
    return "issue" if issue_key_from_text(text) else "jql"


def load_config_text(text: str) -> Config:
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit(_("PyYAML not installed: install python3-yaml package")) from exc

    data = yaml.safe_load(text) or {}
    if isinstance(data, list):
        return Config(items=[str(item) for item in data])
    if not isinstance(data, dict):
        raise SystemExit(_("YAML must be a dict or a list of strings"))
    items = data.get("items", [])
    if not isinstance(items, list):
        raise SystemExit(_("Field 'items' must be a list"))
    analytics_enabled = data.get("analytics_enabled", True)
    if not isinstance(analytics_enabled, bool):
        raise SystemExit(_("Field '%s' must be a boolean") % "analytics_enabled")
    period_start_raw = data.get("period_start")
    period_end_raw = data.get("period_end")
    period_start = _parse_config_date(period_start_raw)
    period_end = _parse_config_date(period_end_raw)
    boards = _load_boards(data.get("boards", []))
    done_statuses = _load_string_list(data.get("done_statuses", DEFAULT_DONE_STATUSES), "done_statuses")
    active_statuses = _load_string_list(data.get("active_statuses", DEFAULT_ACTIVE_STATUSES), "active_statuses")
    waiting_statuses = _load_string_list(data.get("waiting_statuses", DEFAULT_WAITING_STATUSES), "waiting_statuses")
    if period_start_raw not in (None, "") and not period_start:
        raise SystemExit(_("period_start must be YYYY-MM-DD"))
    if period_end_raw not in (None, "") and not period_end:
        raise SystemExit(_("period_end must be YYYY-MM-DD"))
    if bool(period_start) != bool(period_end):
        raise SystemExit(_("period_start and period_end must both be set or both be empty"))
    if period_start and period_end and period_end < period_start:
        raise SystemExit(_("period_end must be greater than or equal to period_start"))
    return Config(
        warn_days=int(data.get("warn_days", 7)),
        first_check_delay_minutes=int(data.get("first_check_delay_minutes", 2)),
        check_interval_minutes=int(data.get("check_interval_minutes", 60)),
        connect_timeout_seconds=int(data.get("connect_timeout_seconds", 15)),
        notifications_enabled=bool(data.get("notifications_enabled", True)),
        target_end_field=str(data.get("target_end_field", "Target end")),
        items=[str(item) for item in items],
        analytics_enabled=analytics_enabled,
        period_start=period_start,
        period_end=period_end,
        boards=boards,
        story_points_field=str(data.get("story_points_field", "Story points")),
        include_active_sprints=_parse_bool(data.get("include_active_sprints", True), True, "include_active_sprints"),
        changelog_cache_ttl_hours=int(data.get("changelog_cache_ttl_hours", 24)),
        forgotten_age_days=int(data.get("forgotten_age_days", 30)),
        show_external_assignees=_parse_bool(data.get("show_external_assignees", False), False, "show_external_assignees"),
        parallel_workers=int(data.get("parallel_workers", 3)),
        done_statuses=done_statuses,
        active_statuses=active_statuses,
        waiting_statuses=waiting_statuses,
    )


def _parse_bool(value: object, default: bool, field_name: str) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    raise SystemExit(_("Field '%s' must be a boolean") % field_name)


def _parse_config_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _load_boards(value: object) -> list[BoardConfig]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise SystemExit(_("Field 'boards' must be a list"))
    boards: list[BoardConfig] = []
    for item in value:
        if not isinstance(item, dict):
            raise SystemExit(_("Each board must be a dict with id and name"))
        try:
            board_id = int(item["id"])
            name = str(item["name"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(_("Each board must have id and name")) from exc
        backlog_jql_raw = item.get("backlog_jql")
        if backlog_jql_raw is not None and not isinstance(backlog_jql_raw, str):
            raise SystemExit(_("Each board's backlog_jql must be a string if present"))
        backlog_jql = str(backlog_jql_raw).strip() if isinstance(backlog_jql_raw, str) else None
        done_statuses = _load_optional_status_list(item, "done_statuses")
        active_statuses = _load_optional_status_list(item, "active_statuses")
        waiting_statuses = _load_optional_status_list(item, "waiting_statuses")
        boards.append(BoardConfig(board_id, name, backlog_jql, done_statuses, active_statuses, waiting_statuses))
    return boards


def _load_optional_status_list(item: dict, field_name: str) -> list[str] | None:
    value = item.get(field_name)
    if value is None:
        return None
    return _load_string_list(value, field_name)


def _load_string_list(value: object, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise SystemExit(_("Field '%s' must be a list") % field_name)
    return [str(item) for item in value]


def load_config(path: Path) -> Config:
    if not path.exists():
        raise SystemExit(_("Config file not found: %s") % path)
    return load_config_text(path.read_text(encoding="utf-8"))


def ensure_user_files() -> None:
    DEFAULT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    if not DEFAULT_CONFIG.exists() and OLD_DEFAULT_CONFIG.exists():
        DEFAULT_CONFIG.write_text(OLD_DEFAULT_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    if not DEFAULT_CONFIG.exists() and SYSTEM_CONFIG.exists():
        DEFAULT_CONFIG.write_text(SYSTEM_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    if not DEFAULT_CONFIG.exists() and OLD_SYSTEM_CONFIG.exists():
        DEFAULT_CONFIG.write_text(OLD_SYSTEM_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    if not DEFAULT_ENV.exists() and OLD_DEFAULT_ENV.exists():
        DEFAULT_ENV.write_text(OLD_DEFAULT_ENV.read_text(encoding="utf-8"), encoding="utf-8")
        DEFAULT_ENV.chmod(0o600)
    if not DEFAULT_ENV.exists():
        DEFAULT_ENV.write_text(DEFAULT_ENV_TEXT, encoding="utf-8")
        DEFAULT_ENV.chmod(0o600)


def _changelog_cache_path(key: str, namespace: str = "") -> Path:
    if not namespace:
        return CHANGELOG_CACHE_DIR / f"{key}.json"
    import hashlib
    bucket = hashlib.sha256(namespace.encode()).hexdigest()[:16]
    return CHANGELOG_CACHE_DIR / bucket / f"{key}.json"


def load_changelog_cache(key: str, ttl_hours: int = 24, namespace: str = "") -> list[dict[str, object]] | None:
    path = _changelog_cache_path(key, namespace)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if ttl_hours > 0:
            cached_at = data.get("cached_at", "")
            if cached_at:
                age = datetime.now() - datetime.fromisoformat(cached_at)
                if age.total_seconds() > ttl_hours * 3600:
                    return None
        histories = data.get("histories", []) if isinstance(data, dict) else None
        return histories if isinstance(histories, list) else None
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        return None


def save_changelog_cache(key: str, histories: list[dict[str, object]], namespace: str = "") -> None:
    path = _changelog_cache_path(key, namespace)
    data = {"cached_at": datetime.now().isoformat(), "histories": histories}
    _atomic_write_json(path, data)


def clear_changelog_cache() -> None:
    if CHANGELOG_CACHE_DIR.exists():
        for path in CHANGELOG_CACHE_DIR.rglob("*.json"):
            path.unlink()


PERIOD_CACHE = Path.home() / ".cache" / "jira-analytics" / "period_cache.json"


def _config_signature(env: dict[str, str], config: Config) -> str:
    import hashlib
    key = json.dumps({
        "calc": 5,
        "url": normalize_jira_url(env.get("JIRA_URL", "")),
        "username": env.get("JIRA_USERNAME", ""),
        "period_start": str(config.period_start),
        "period_end": str(config.period_end),
        "boards": sorted((b.id, str(b.name)) for b in config.boards),
        "backlog_jql": {b.id: b.backlog_jql for b in config.boards},
        "board_statuses": {b.id: [b.done_statuses, b.active_statuses, b.waiting_statuses] for b in config.boards},
        "points_field": str(config.story_points_field),
        "include_active_sprints": config.include_active_sprints,
        "done": sorted(config.done_statuses),
        "active": sorted(config.active_statuses),
        "waiting": sorted(config.waiting_statuses),
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _atomic_write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _period_source_identity(env: dict[str, str], config: Config) -> str:
    import hashlib
    value = json.dumps({
        "schema": _PERIOD_SOURCE_SCHEMA,
        "url": normalize_jira_url(env.get("JIRA_URL", "")),
        "username": env.get("JIRA_USERNAME", ""),
        "points_field": config.story_points_field,
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def load_period_source_cache(env: dict[str, str], config: Config) -> dict[str, object] | None:
    if not PERIOD_SOURCE_CACHE.exists():
        return None
    try:
        envelope = json.loads(PERIOD_SOURCE_CACHE.read_text(encoding="utf-8"))
        if not isinstance(envelope, dict) or envelope.get("schema") != _PERIOD_SOURCE_SCHEMA:
            return None
        if envelope.get("identity") != _period_source_identity(env, config):
            return None
        data = envelope.get("data")
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def save_period_source_cache(env: dict[str, str], config: Config, data: dict[str, object]) -> None:
    _atomic_write_json(PERIOD_SOURCE_CACHE, {
        "schema": _PERIOD_SOURCE_SCHEMA,
        "identity": _period_source_identity(env, config),
        "data": data,
    })


def save_period_cache(report: PeriodReport, config_sig: str) -> None:
    data = {
        "cached_at": datetime.now().isoformat(),
        "config_sig": config_sig,
        "report": dataclasses.asdict(report),
    }
    _atomic_write_json(PERIOD_CACHE, data)


def load_period_cache(config_sig: str) -> PeriodReport | None:
    if not PERIOD_CACHE.exists():
        return None
    try:
        data = json.loads(PERIOD_CACHE.read_text(encoding="utf-8"))
        if data.get("config_sig") != config_sig:
            return None
        rep = data.get("report")
        if not rep:
            return None
        portfolio_data = rep.get("portfolio", {})
        routing_fields = {
            "completed_later_count", "observed_other_board_count",
            "not_observed_count", "unknown_count", "active_count",
        }
        if portfolio_data and (not routing_fields.issubset(portfolio_data) or "team_scope" not in rep):
            return None
        portfolio = PortfolioAggregate(**portfolio_data) if portfolio_data else None
        boards = [BoardAggregate(**b) for b in rep.get("boards", [])]
        sprints_raw = rep.get("sprints", [])
        sprints = []
        for sr in sprints_raw:
            sr["board_name"] = str(sr.get("board_name", ""))
            sr["sprint_name"] = str(sr.get("sprint_name", ""))
            sr["sprint_id"] = int(sr.get("sprint_id", 0))
            sr["start_date"] = date.fromisoformat(sr["start_date"]) if sr.get("start_date") else date.today()
            sr["end_date"] = date.fromisoformat(sr["end_date"]) if sr.get("end_date") else date.today()
            sr["status_distribution"] = dict(sr.get("status_distribution", {}) or {})
            sr["assignee_distribution"] = dict(sr.get("assignee_distribution", {}) or {})
            sprints.append(SprintSummary(**sr))
        assignees_raw = rep.get("assignees", [])
        assignees = []
        for ar in assignees_raw:
            ar["display_name"] = str(ar.get("display_name", ""))
            ar["board_name"] = str(ar.get("board_name", ""))
            items_raw = ar.get("items", [])
            items = [AssigneeIssue(
                key=str(i.get("key", "")),
                summary=str(i.get("summary", "")),
                sprint_name=str(i.get("sprint_name", "")),
                points=float(i["points"]) if i.get("points") is not None else None,
                category=str(i.get("category", "")),
                original_name=str(i.get("original_name") or "") or None,
                route_category=str(i.get("route_category", "")),
                completed_later=bool(i.get("completed_later", False)),
                current_status=str(i.get("current_status", "")),
                current_status_category=str(i.get("current_status_category", "unknown")),
            ) for i in items_raw]
            ar["items"] = items
            assignees.append(AssigneeSummary(**ar))
        bh_raw = rep.get("backlog_health", [])
        backlog_health = []
        for b in bh_raw:
            items_raw = b.get("items", [])
            items = [BacklogItem(
                key=str(i.get("key", "")),
                summary=str(i.get("summary", "")),
                age_days=int(i.get("age_days", 0)),
                days_since_update=int(i.get("days_since_update", 0)),
                status=str(i.get("status", "")),
                assignee=str(i.get("assignee", "")),
            ) for i in items_raw]
            backlog_health.append(BacklogHealth(
                board_name=str(b.get("board_name", "")),
                total_open=int(b.get("total_open", 0)),
                aging_30plus=int(b.get("aging_30plus", 0)),
                aging_90plus=int(b.get("aging_90plus", 0)),
                aging_180plus=int(b.get("aging_180plus", 0)),
                aging_365plus=int(b.get("aging_365plus", 0)),
                aging_avg_days=float(b.get("aging_avg_days", 0)),
                aging_max_days=int(b.get("aging_max_days", 0)),
                stale_count=int(b.get("stale_count", 0)),
                items=items,
            ))
        team_scope = []
        for scope in rep.get("team_scope", []):
            items = [TeamScopeItem(
                key=str(i.get("key", "")),
                summary=str(i.get("summary", "")),
                status=str(i.get("status", "")),
                status_category=str(i.get("status_category", "unknown")),
                assignee=str(i.get("assignee", "")),
            ) for i in scope.get("items", [])]
            team_scope.append(TeamScopeSummary(
                board_name=str(scope.get("board_name", "")),
                total=int(scope.get("total", 0)),
                done=int(scope.get("done", 0)),
                active=int(scope.get("active", 0)),
                waiting=int(scope.get("waiting", 0)),
                unknown=int(scope.get("unknown", 0)),
                status_distribution=dict(scope.get("status_distribution", {}) or {}),
                items=items,
            ))
        return PeriodReport(portfolio, boards, sprints, assignees, rep.get("errors", []), backlog_health, team_scope)
    except Exception:
        return None


def fetch_issue_changelog(jira: object, key: str, ttl_hours: int = 24, namespace: str = "", force: bool = False) -> list[dict[str, object]] | None:
    stale = load_changelog_cache(key, 0, namespace)
    if not force:
        cached = load_changelog_cache(key, ttl_hours, namespace)
        if cached is not None:
            return cached
    try:
        all_histories: list[dict[str, object]] = []
        start = 0
        while True:
            result = jira.get_issue_changelog(key, start=start, limit=100)
            batch = result if isinstance(result, list) else (result.get("histories") or result.get("values", []))
            if not batch:
                break
            all_histories.extend(batch)
            if isinstance(result, list):
                break
            start += len(batch)
            if result.get("isLast") or start >= result.get("total", 0):
                break
    except Exception as exc:
        _raise_access_error(exc)
        return stale
    try:
        save_changelog_cache(key, all_histories, namespace)
    except OSError:
        pass
    return all_histories


def _board_dict(board: BoardConfig) -> dict[str, object]:
    item: dict[str, object] = {"id": board.id, "name": board.name}
    if board.backlog_jql:
        item["backlog_jql"] = board.backlog_jql
    if board.done_statuses is not None:
        item["done_statuses"] = board.done_statuses
    if board.active_statuses is not None:
        item["active_statuses"] = board.active_statuses
    if board.waiting_statuses is not None:
        item["waiting_statuses"] = board.waiting_statuses
    return item


def save_config(path: Path, config: Config) -> None:
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit(_("PyYAML not installed: install python3-yaml package")) from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "warn_days": config.warn_days,
        "first_check_delay_minutes": config.first_check_delay_minutes,
        "check_interval_minutes": config.check_interval_minutes,
        "connect_timeout_seconds": config.connect_timeout_seconds,
        "notifications_enabled": config.notifications_enabled,
        "target_end_field": config.target_end_field,
        "items": config.items or [],
        "analytics_enabled": config.analytics_enabled,
        "period_start": config.period_start.isoformat() if config.period_start else None,
        "period_end": config.period_end.isoformat() if config.period_end else None,
        "boards": [_board_dict(board) for board in config.boards],
        "story_points_field": config.story_points_field,
        "include_active_sprints": config.include_active_sprints,
        "changelog_cache_ttl_hours": config.changelog_cache_ttl_hours,
        "forgotten_age_days": config.forgotten_age_days,
        "show_external_assignees": config.show_external_assignees,
        "parallel_workers": config.parallel_workers,
        "done_statuses": config.done_statuses,
        "active_statuses": config.active_statuses,
        "waiting_statuses": config.waiting_statuses,
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def env_values(path: Path = DEFAULT_ENV) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key in _SENSITIVE_KEYS and value:
            value = decrypt_value(value)
        values[key] = value
    return values


def save_env(path: Path, updates: dict[str, str]) -> None:
    values = env_values(path)
    values.update(updates)
    for key in _SENSITIVE_KEYS:
        if values.get(key):
            values[key] = encrypt_value(values[key])
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


def alert_kind(target_end: date, today: date, warn_days: int) -> str | None:
    if target_end < today:
        return "overdue"
    if (target_end - today).days <= warn_days:
        return "soon"
    return None


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (TypeError, ValueError):
        return None


def jira_client(env: dict[str, str], timeout: int | None = None) -> Any:
    try:
        from atlassian import Jira
    except ImportError as exc:
        raise SystemExit(
            _("atlassian-python-api not installed: run python3 -m pip install --user atlassian-python-api")
        ) from exc

    url = normalize_jira_url(env.get("JIRA_URL", ""))
    username = env.get("JIRA_USERNAME")
    token = env.get("JIRA_TOKEN")
    password = env.get("JIRA_PASSWORD")
    if not url or not username or not (token or password):
        raise SystemExit(_("JIRA_URL, JIRA_USERNAME and JIRA_TOKEN or JIRA_PASSWORD are required"))
    kwargs = {"url": url, "username": username}
    if timeout:
        kwargs["timeout"] = timeout
    if token:
        kwargs["token"] = token
    else:
        kwargs["password"] = password
    return Jira(**kwargs)


def field_id(jira: Any, name: str) -> str:
    for field in jira.get_all_fields():
        if field.get("name") == name or field.get("id") == name:
            return field["id"]
    raise SystemExit(_("Jira field not found: %s") % name)


def issue_url(jira_url: str, key: str) -> str:
    return f"{jira_url.rstrip('/')}/browse/{key}"


def build_notify_command(title: str, body: str) -> list[str]:
    return [
        "gdbus",
        "call",
        "--session",
        "--dest",
        "org.freedesktop.Notifications",
        "--object-path",
        "/org/freedesktop/Notifications",
        "--method",
        "org.freedesktop.Notifications.Notify",
        APP_NAME,
        "0",
        "dialog-warning",
        title,
        body,
        "[]",
        "{}",
        "5000",
    ]


def notify(title: str, body: str) -> bool:
    try:
        result = subprocess.run(
            build_notify_command(title, body),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        print(_("Failed to send notification: gdbus command not found"), file=sys.stderr)
        return False
    if result.returncode == 0:
        return True
    print(_("Failed to send notification: %s") % result.stderr.strip(), file=sys.stderr)
    return False


def message_from_alert(alert: AlertIssue) -> tuple[str, str]:
    if alert.kind == "overdue":
        title = _("Jira: Target end overdue")
        line = _("Overdue by %s days") % abs(alert.days)
    else:
        title = _("Jira: Target end approaching")
        line = _("%s days left") % alert.days
    body = f"{alert.key}: {alert.summary}\n" + _("Status: %s\nTarget end: %s\n%s\n%s") % (
        alert.status, alert.target_end.isoformat(), line, alert.url,
    )
    return title, body


def alert_issue(issue: dict[str, Any], target_field: str, warn_days: int, today: date, jira_url: str) -> AlertIssue | None:
    target_end = parse_date(issue.get("fields", {}).get(target_field))
    if not target_end:
        return None
    kind = alert_kind(target_end, today, warn_days)
    if not kind:
        return None
    fields = issue.get("fields", {})
    return AlertIssue(
        key=issue["key"],
        summary=fields.get("summary", ""),
        status=fields.get("status", {}).get("name", "unknown"),
        assignee=fields.get("assignee", {}).get("displayName", _("Unassigned")) if fields.get("assignee") else _("Unassigned"),
        target_end=target_end,
        days=(target_end - today).days,
        kind=kind,
        url=issue_url(jira_url, issue["key"]),
    )


def get_issue(jira: Any, key: str, fields: list[str]) -> dict[str, Any]:
    return jira.issue(key, fields=",".join(fields))


def search_issues(jira: Any, jql: str, fields: list[str]) -> list[dict[str, Any]]:
    start = 0
    issues: list[dict[str, Any]] = []
    while True:
        result = jira.jql(jql, fields=fields, start=start, limit=100)
        batch = result.get("issues", [])
        issues.extend(batch)
        start += len(batch)
        if start >= result.get("total", 0) or not batch:
            return issues


def agile_batch_items(result: object, key: str) -> list[dict[str, Any]]:
    if isinstance(result, list):
        return result
    if not isinstance(result, dict):
        return []
    items = result.get(key) or result.get("values") or result.get("issues") or []
    return items if isinstance(items, list) else []


def agile_done(result: object, start: int, batch_size: int, limit: int) -> bool:
    if isinstance(result, list):
        return batch_size < limit
    if not isinstance(result, dict):
        return True
    if result.get("isLast") is True:
        return True
    total = result.get("total")
    if "isLast" not in result and total is None:
        return True
    return isinstance(total, int) and start + batch_size >= total


def paged_agile(method: Any, key: str, *args: object, limit: int = 100, **kwargs: object) -> list[dict[str, Any]]:
    start = 0
    items: list[dict[str, Any]] = []
    while True:
        result = method(*args, start=start, limit=limit, **kwargs)
        batch = agile_batch_items(result, key)
        items.extend(batch)
        if not batch or agile_done(result, start, len(batch), limit):
            return items
        start += len(batch)


def sprint_report_removed_issues(jira: object, board_id: int, sprint_id: int, points_field: str | None) -> list[dict[str, Any]]:
    if not hasattr(jira, "get"):
        return []
    report = jira.get(f"rest/greenhopper/1.0/rapid/charts/sprintreport?rapidViewId={board_id}&sprintId={sprint_id}")
    contents = report.get("contents", {}) if isinstance(report, dict) else {}
    removed = contents.get("puntedIssues", []) if isinstance(contents, dict) else []
    result: list[dict[str, Any]] = []
    for item in removed if isinstance(removed, list) else []:
        if not isinstance(item, dict):
            continue
        fields: dict[str, Any] = {"summary": item.get("summary", "")}
        stat = item.get("estimateStatistic", {})
        value = stat.get("statFieldValue", {}).get("value") if isinstance(stat, dict) else None
        if points_field and value is not None:
            fields[points_field] = value
        result.append({"key": item.get("key", ""), "fields": fields})
    return result


def _dump_raw_data(path: str, sprint_data: list[SprintIssues], points_field: str | None, config: Config, backlog_keys: dict[str, set[str]], backlog_health: list, team_scope: list[TeamScopeSummary], sprint_sources_complete: dict[str, bool], backlog_complete_boards: set[str], classification_complete_boards: set[str], historical_done: dict[tuple[str, int], bool], errors: list[str], status_changelogs: dict[str, list[dict[str, Any]]], raw: bool = False, env_values: list[str] | None = None) -> None:
    import json as _json
    _sprints = []
    for s in sprint_data:
        _sprints.append({
            "board_name": s.board_name,
            "sprint_id": s.sprint_id,
            "sprint_name": s.sprint_name,
            "start_date": s.start_date.isoformat(),
            "end_date": s.end_date.isoformat(),
            "complete_date": s.complete_date.isoformat() if s.complete_date else None,
            "issues": s.issues,
            "removed_issues": s.removed_issues,
        })
    _dump = {
        "meta": {
            "points_field": points_field,
            "done_statuses": config.done_statuses,
            "active_statuses": config.active_statuses,
            "waiting_statuses": config.waiting_statuses,
            "board_statuses": {b.name: {"done": b.done_statuses, "active": b.active_statuses, "waiting": b.waiting_statuses} for b in config.boards},
            "period_start": config.period_start.isoformat() if config.period_start else None,
            "period_end": config.period_end.isoformat() if config.period_end else None,
        },
        "sprints": _sprints,
        "backlog_keys": {k: sorted(v) for k, v in backlog_keys.items()},
        "historical_done": {f"{key}_{sprint_id}": is_done for (key, sprint_id), is_done in historical_done.items()},
        "errors": errors,
        "status_changelogs": status_changelogs,
        "backlog_health": [dataclasses.asdict(bh) for bh in backlog_health] if backlog_health else [],
        "team_scope": [dataclasses.asdict(scope) for scope in team_scope],
        "sprint_sources_complete": sprint_sources_complete,
        "team_scope_complete_boards": sorted(backlog_complete_boards),
        "classification_complete_boards": sorted(classification_complete_boards),
        "backlog_jqls": {b.name: b.backlog_jql for b in config.boards if b.backlog_jql},
    }
    if not raw:
        _dump = sanitize_json(_dump, env_values or [])
    with open(path, "w", encoding="utf-8") as fh:
        _json.dump(_dump, fh, ensure_ascii=False, default=str, indent=2)
    log.info("Raw data dumped to %s", path)


_jira_local = threading.local()


def _thread_jira(env: dict[str, str], timeout: int | None = None) -> Any:
    if not hasattr(_jira_local, "client"):
        _jira_local.client = jira_client(env, timeout)
    return _jira_local.client


def _parallel_map(items: list, fn: Callable, max_workers: int, progress: Callable[[int, int], None] | None = None) -> tuple[list, list[str]]:
    results: list = []
    errors: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fn, item): item for item in items}
        done = 0
        total = len(items)
        for future in concurrent.futures.as_completed(futures):
            done += 1
            if progress:
                progress(done, total)
            try:
                results.append(future.result())
            except Exception as exc:
                _raise_access_error(exc)
                errors.append(str(exc))
    return results, errors


def collect_period_result(env: dict[str, str], config: Config, progress: Callable[[str, int, int], None] | None = None, dump_path: str | None = None, full_refresh: bool = False, raw: bool = False) -> PeriodReport:
    _p = progress if progress else lambda *_: None
    if not config.analytics_enabled:
        return build_period_report([], None, config.done_statuses)
    if not config.period_start or not config.period_end:
        raise SystemExit(_("period_start and period_end are required when analytics_enabled is true"))
    errors: list[str] = []
    timeout = config.connect_timeout_seconds
    workers = max(1, config.parallel_workers)
    board_statuses = resolve_board_statuses(config.boards, config.done_statuses, config.active_statuses, config.waiting_statuses)
    source_cache = load_period_source_cache(env, config) or {}
    source_sprints = source_cache.get("sprints")
    if not isinstance(source_sprints, dict):
        source_sprints = {}

    # Phase 1: sequential — metadata
    jira = jira_client(env, timeout)
    points_field = None if full_refresh else source_cache.get("points_field")
    if not isinstance(points_field, str) or not points_field:
        try:
            points_field = field_id(jira, config.story_points_field)
        except SystemExit as exc:
            if full_refresh:
                raise
            errors.append(str(exc))
            points_field = None
    cache_enabled = isinstance(points_field, str) and bool(points_field)
    fields = ["summary", "status", "assignee", "issuetype", "priority", "created", "resolutiondate"]
    if points_field:
        fields.append(points_field)
    agile_fields = ",".join(fields)
    sprint_descs: list[dict[str, Any]] = []
    sprint_sources_complete = {board.name: True for board in config.boards}
    num_boards = len(config.boards)
    _p(_("Loading %s boards") % num_boards, 0, 100)
    for idx, board in enumerate(config.boards):
        pct = int((idx + 1) / num_boards * 10)
        _p(_("Board %s: sprints") % board.name, pct, 100)
        try:
            sprint_state = "active,closed,future" if config.include_active_sprints else "closed"
            sprints = paged_agile(jira.get_all_sprints_from_board, "values", board.id, state=sprint_state, limit=50)
        except Exception as exc:
            _raise_access_error(exc)
            sprint_sources_complete[board.name] = False
            errors.append(_("Error loading board %s: %s") % (board.name, exc))
            for cached in source_sprints.values():
                if not isinstance(cached, dict) or cached.get("board_id") != board.id:
                    continue
                start_date = parse_date(cached.get("start_date"))
                end_date = parse_date(cached.get("end_date"))
                if not start_date or not end_date or not sprint_overlaps_period(start_date, end_date, config.period_start, config.period_end):
                    continue
                sprint_descs.append({
                    "board_name": board.name,
                    "board_id": board.id,
                    "sprint_id": int(cached.get("sprint_id", 0)),
                    "sprint_name": str(cached.get("sprint_name", cached.get("sprint_id", 0))),
                    "start_date": start_date,
                    "end_date": end_date,
                    "complete_date": parse_datetime(cached.get("complete_date")),
                    "is_closed": True,
                    "is_future": False,
                })
            continue
        log.debug("Board %s loaded %s sprints (state=%s)", board.name, len(sprints), sprint_state)
        for sprint in sprints:
            start_date = parse_date(sprint.get("startDate"))
            end_date = parse_date(sprint.get("endDate"))
            if not start_date or not end_date:
                errors.append(_("Sprint %s skipped: missing dates") % sprint.get("name", sprint.get("id", "unknown")))
                sprint_sources_complete[board.name] = False
                continue
            is_future = sprint.get("state") == "future"
            if not is_future and not sprint_overlaps_period(start_date, end_date, config.period_start, config.period_end):
                continue
            sprint_id = int(sprint.get("id", 0))
            complete_raw = parse_datetime(sprint.get("completeDate") or sprint.get("endDate"))
            is_closed = sprint.get("state") == "closed"
            complete_date = complete_raw or parse_datetime(end_date) if is_closed else None
            if not is_closed:
                source_sprints.pop(f"{board.id}:{sprint_id}", None)
            sprint_descs.append({
                "board_name": board.name, "board_id": board.id,
                "sprint_id": sprint_id, "sprint_name": str(sprint.get("name", sprint_id)),
                "start_date": start_date, "end_date": end_date,
                "complete_date": complete_date, "is_closed": is_closed,
                "is_future": is_future,
            })
    total_sprints = len(sprint_descs)
    _p(_("Loaded %s sprints") % total_sprints, 15, 100)
    log.info("Period: %s boards, %s sprints in period window", len(config.boards), total_sprints)

    # Phase 2: parallel — sprint issues + removed
    sprint_data: list[SprintIssues] = []
    routing_only_sprints: set[tuple[str, int]] = set()
    if sprint_descs:
        def _sprint_progress(done, total):
            pct = 15 + int(done / total * 10)
            _p(_("Loading sprint issues: %s/%s") % (done, total), pct, 100)

        def _load_sprint(sd):
            tjira = _thread_jira(env, timeout)
            bid = sd["board_id"]
            sid = sd["sprint_id"]
            bname = sd["board_name"]
            sname = sd["sprint_name"]
            cache_key = f"{bid}:{sid}"
            cached = source_sprints.get(cache_key)
            if sd["is_closed"] and cache_enabled and not full_refresh and isinstance(cached, dict) and parse_datetime(cached.get("complete_date")) == sd["complete_date"]:
                issues = cached.get("issues")
                removed_issues = cached.get("removed_issues")
                if isinstance(issues, list) and isinstance(removed_issues, list):
                    if cached.get("removed_complete", True):
                        return sd, issues, removed_issues, "", True
                    try:
                        removed_issues = sprint_report_removed_issues(tjira, bid, sid, points_field)
                        return sd, issues, removed_issues, "", True
                    except Exception as exc:
                        _raise_access_error(exc)
                        return sd, issues, removed_issues, _("Sprint report unavailable, removed data skipped: %s") % bname, False
            try:
                issues = paged_agile(tjira.get_all_issues_for_sprint_in_board, "issues", bid, sid, fields=agile_fields, limit=100)
            except Exception as exc:
                _raise_access_error(exc)
                if sd["is_closed"] and isinstance(cached, dict):
                    cached_issues = cached.get("issues")
                    cached_removed = cached.get("removed_issues")
                    if isinstance(cached_issues, list) and isinstance(cached_removed, list):
                        return sd, cached_issues, cached_removed, _("Error loading sprint %s: %s") % (sname, exc), bool(cached.get("removed_complete", True))
                raise Exception(_("Error loading sprint %s: %s") % (sname, exc))
            removed_issues = []
            removed_error = ""
            try:
                removed_issues = sprint_report_removed_issues(tjira, bid, sid, points_field)
            except Exception as exc:
                _raise_access_error(exc)
                removed_error = _("Sprint report unavailable, removed data skipped: %s") % bname
            return sd, issues, removed_issues, removed_error, not removed_error

        results, load_errors = _parallel_map(sprint_descs, _load_sprint, workers, progress=_sprint_progress)
        errors.extend(load_errors)
        loaded_sprints = {(sd["board_name"], sd["sprint_id"]) for sd, *_rest in results}
        for sd in sprint_descs:
            if (sd["board_name"], sd["sprint_id"]) not in loaded_sprints:
                sprint_sources_complete[sd["board_name"]] = False
        for sd, issues, removed_issues, removed_error, removed_complete in results:
            if removed_error and removed_error not in errors:
                errors.append(removed_error)
            if sd["is_closed"] and cache_enabled:
                cache_key = f'{sd["board_id"]}:{sd["sprint_id"]}'
                previous = source_sprints.get(cache_key)
                if removed_error and isinstance(previous, dict) and isinstance(previous.get("removed_issues"), list):
                    removed_issues = previous["removed_issues"]
                    removed_complete = bool(previous.get("removed_complete", True))
                source_sprints[cache_key] = {
                    "board_id": sd["board_id"],
                    "sprint_id": sd["sprint_id"],
                    "sprint_name": sd["sprint_name"],
                    "start_date": sd["start_date"].isoformat(),
                    "end_date": sd["end_date"].isoformat(),
                    "complete_date": sd["complete_date"].isoformat() if sd["complete_date"] else None,
                    "issues": issues,
                    "removed_issues": removed_issues,
                    "removed_complete": removed_complete,
                }
            sprint_data.append(SprintIssues(
                sd["board_name"], sd["sprint_id"], sd["sprint_name"],
                sd["start_date"], sd["end_date"], sd["complete_date"],
                issues, removed_issues,
            ))
            if sd["is_future"]:
                routing_only_sprints.add((sd["board_name"], sd["sprint_id"]))
    _p(_("Sprint issues loaded: %s sprints") % len(sprint_data), 25, 100)
    report_sprint_data = [
        sprint for sprint in sprint_data
        if (sprint.board_name, sprint.sprint_id) not in routing_only_sprints
    ]

    # Phase 3: parallel — backlog JQL
    backlog_keys: dict[str, set[str]] = {}
    backlog_health: list[BacklogHealth] = []
    team_scope: list[TeamScopeSummary] = []
    scope_statuses: dict[str, dict[str, StatusClassification]] = {}
    backlog_complete_boards: set[str] = set()
    back_boards = [b for b in config.boards if b.backlog_jql]
    if back_boards:
        def _load_backlog(board):
            tjira = _thread_jira(env, timeout)
            try:
                issues = search_issues(tjira, board.backlog_jql, ["key", "created", "updated", "status", "summary", "assignee"])
            except Exception as exc:
                _raise_access_error(exc)
                raise Exception(_("Error loading backlog for %s: %s") % (board.name, exc))
            statuses = board_statuses.get(board.name, BoardStatuses(config.done_statuses, config.active_statuses, config.waiting_statuses))
            classified = {
                i["key"]: classify_status(i.get("fields", {}).get("status", {}), statuses)
                for i in issues if i.get("key")
            }
            waiting_issues = [i for i in issues if classified.get(i.get("key"), StatusClassification("", "unknown")).category == "waiting"]
            return board.name, set(i["key"] for i in waiting_issues), compute_backlog_health(waiting_issues, board.name), compute_team_scope(issues, board.name, statuses), classified
        results, bl_errors = _parallel_map(back_boards, _load_backlog, workers, progress=lambda d, t: _p(
            _("Loading backlog: %s/%s") % (d, t), 25 + int(d / max(t, 1) * 5), 100))
        errors.extend(bl_errors)
        for bname, keys, health, scope, classified in results:
            backlog_keys[bname] = keys
            backlog_health.append(health)
            team_scope.append(scope)
            scope_statuses[bname] = classified
            backlog_complete_boards.add(bname)
    _p(_("Backlog loaded: %s boards") % len(back_boards), 30, 100)

    # Phase 4: sequential — all_keys from sprint_data
    all_keys: set[str] = set()
    for sprint in report_sprint_data:
        if sprint.complete_date is None:
            continue
        for issue in sprint.issues:
            key = issue.get("key")
            if key:
                all_keys.add(key)
    log.info("Historical: %s unique issue keys across %s closed sprints", len(all_keys),
              sum(1 for s in report_sprint_data if s.complete_date is not None))

    # Phase 5: parallel — changelogs
    status_changelogs: dict[str, list[dict[str, Any]]] = {}
    if all_keys:
        keys_list = list(all_keys)
        changelog_namespace = _period_source_identity(env, config)
        def _load_changelog(key):
            tjira = _thread_jira(env, timeout)
            changelog = fetch_issue_changelog(
                tjira, key, config.changelog_cache_ttl_hours, changelog_namespace, force=full_refresh
            )
            return key, changelog
        def _changelog_progress(done, total):
            pct = 30 + int(done / total * 65)
            _p(_("Changelogs: %s/%s") % (done, total), pct, 100)
        results, ch_errors = _parallel_map(keys_list, _load_changelog, workers, progress=_changelog_progress)
        errors.extend(ch_errors)
        for result in results:
            key, changelog = result
            if changelog is None:
                errors.append(_("Changelog not available for %s, using current status") % key)
                continue
            status_changelogs[key] = changelog
    _p(_("Changelogs loaded: %s keys") % len(status_changelogs), 95, 100)

    # Phase 6: sequential — historical_done, metrics
    historical_done: dict[tuple[str, int], bool] = {}
    changed = 0
    for sprint in report_sprint_data:
        if sprint.complete_date is None:
            continue
        bs_done = board_statuses.get(sprint.board_name, BoardStatuses(config.done_statuses, config.active_statuses, config.waiting_statuses)).done
        for issue in sprint.issues:
            key = issue.get("key")
            if not key:
                continue
            changelog = status_changelogs.get(key)
            current_status = issue.get("fields", {}).get("status", {})
            is_hist_done = resolve_historical_status(
                changelog, current_status, sprint.complete_date, bs_done
            )
            current_done = is_done_status(current_status, bs_done)
            if is_hist_done != current_done:
                changed += 1
            historical_done[(key, sprint.sprint_id)] = is_hist_done
    log.info("Historical overrides computed: %s entries, %s changed vs current status",
             len(historical_done), changed)
    _p(_("Calculating metrics"), 95, 100)
    classification_complete_boards = (
        backlog_complete_boards
        if config.include_active_sprints and all(sprint_sources_complete.values())
        else set()
    )
    if dump_path:
        token_values = [env.get(k) for k in _SENSITIVE_KEYS if env.get(k)]
        _dump_raw_data(dump_path, sprint_data, points_field, config, backlog_keys, backlog_health, team_scope, sprint_sources_complete, backlog_complete_boards, classification_complete_boards, historical_done, errors, status_changelogs, raw=raw, env_values=token_values)
    report = build_period_report(
        report_sprint_data,
        points_field,
        config.done_statuses,
        backlog_keys,
        historical_done,
        errors,
        status_changelogs,
        config.active_statuses,
        config.waiting_statuses,
        backlog_health=backlog_health,
        board_statuses=board_statuses,
        classification_complete_boards=classification_complete_boards,
        team_scope=team_scope,
        scope_statuses=scope_statuses,
        routing_sprints=sprint_data,
    )
    if cache_enabled:
        try:
            save_period_source_cache(env, config, {"points_field": points_field, "sprints": source_sprints})
        except OSError as exc:
            report.errors.append(_("Failed to save analytics source cache: %s") % exc)
    return report


def collect_alert_result(env: dict[str, str], config: Config, args: list[str] | None = None) -> CollectResult:
    sources = args or config.items or DEFAULT_ITEMS
    jira = jira_client(env, config.connect_timeout_seconds)
    target_field = field_id(jira, config.target_end_field)
    fields = ["summary", "status", "assignee", target_field]
    today = date.today()
    alerts: list[AlertIssue] = []
    errors: list[str] = []
    seen: set[str] = set()
    jira_url = normalize_jira_url(env.get("JIRA_URL", ""))
    for source in sources:
        try:
            if source_kind(source) == "issue":
                issue = get_issue(jira, issue_key_from_text(source) or source, fields)
                alert = alert_issue(issue, target_field, config.warn_days, today, jira_url)
                if alert and alert.key not in seen:
                    seen.add(alert.key)
                    alerts.append(alert)
            else:
                for issue in search_issues(jira, source, fields):
                    alert = alert_issue(issue, target_field, config.warn_days, today, jira_url)
                    if alert and alert.key not in seen:
                        seen.add(alert.key)
                        alerts.append(alert)
        except Exception as exc:
            _raise_access_error(exc)
            errors.append(_("Error checking %s: %s") % (source, exc))
    return CollectResult(sorted(alerts, key=lambda alert: alert.days), errors)


def check_sources(env: dict[str, str], config: Config, args: list[str]) -> int:
    sent = 0
    result = collect_alert_result(env, config, args)
    for error in result.errors:
        print(error, file=sys.stderr)
    for alert in result.alerts:
        if config.notifications_enabled and notify(*message_from_alert(alert)):
            print(_("Notification sent: %s") % alert.key)
            sent += 1
    print(_("Notifications sent: %s") % sent)
    return 0
