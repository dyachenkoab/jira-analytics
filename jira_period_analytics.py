from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
import json
import logging
import os

log = logging.getLogger("jira_period")

if os.environ.get("JIRA_ANALYTICS_DEBUG"):
    logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s %(message)s")


@dataclass
class BoardConfig:
    id: int
    name: str
    backlog_jql: str | None = None
    done_statuses: list[str] | None = None
    active_statuses: list[str] | None = None
    waiting_statuses: list[str] | None = None


@dataclass
class BoardStatuses:
    done: list[str]
    active: list[str]
    waiting: list[str]


@dataclass(frozen=True)
class StatusClassification:
    name: str
    category: str


def resolve_board_statuses(boards: list[BoardConfig], done: list[str], active: list[str], waiting: list[str]) -> dict[str, BoardStatuses]:
    """Имя доски -> эффективные статусы. Per-board поле, если задано (не None), полностью переписывает глобальное."""
    result: dict[str, BoardStatuses] = {}
    for b in boards:
        result[b.name] = BoardStatuses(
            b.done_statuses if b.done_statuses is not None else done,
            b.active_statuses if b.active_statuses is not None else active,
            b.waiting_statuses if b.waiting_statuses is not None else waiting,
        )
    return result


def _statuses_for(board_name: str, board_statuses: dict[str, BoardStatuses] | None, done: list[str], active: list[str], waiting: list[str]) -> BoardStatuses:
    if board_statuses:
        bs = board_statuses.get(board_name)
        if bs is not None:
            return bs
    return BoardStatuses(done, active, waiting)


@dataclass
class SprintIssues:
    board_name: str
    sprint_id: int
    sprint_name: str
    start_date: date
    end_date: date
    complete_date: datetime | None
    issues: list[dict[str, Any]]
    removed_issues: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SprintSummary:
    board_name: str
    sprint_id: int
    sprint_name: str
    start_date: date
    end_date: date
    total_issues: int
    done_issues: int
    total_points: float
    done_points: float
    completion_pct: float
    not_done_count: int
    spillover_count: int
    spillover_completed: int
    backlog_count: int
    reassigned_count: int
    unestimated_count: int
    removed_count: int
    removed_points: float
    flow_efficiency_pct: float
    lead_time_p50: float
    lead_time_p85: float
    lead_time_p95: float
    aging_avg_days: float
    aging_max_days: float
    status_distribution: dict[str, int]
    assignee_distribution: dict[str, int]
    completed_later_count: int = 0
    observed_other_board_count: int = 0
    not_observed_count: int = 0
    unknown_count: int = 0
    active_count: int = 0


@dataclass
class BoardAggregate:
    board_name: str
    sprints_count: int
    total_issues: int
    done_issues: int
    total_points: float
    done_points: float
    avg_velocity: float
    completion_pct: float
    not_done_count: int
    spillover_count: int
    spillover_completed: int
    spillover_completion_rate: float
    backlog_count: int
    reassigned_count: int
    unestimated_count: int
    removed_count: int
    removed_points: float
    flow_efficiency_pct: float
    completed_later_count: int = 0
    observed_other_board_count: int = 0
    not_observed_count: int = 0
    unknown_count: int = 0
    active_count: int = 0


@dataclass
class PortfolioAggregate:
    boards_count: int
    sprints_count: int
    total_issues: int
    done_issues: int
    total_points: float
    done_points: float
    avg_velocity: float
    completion_pct: float
    not_done_count: int
    spillover_count: int
    spillover_completed: int
    backlog_count: int
    reassigned_count: int
    unestimated_count: int
    removed_count: int
    removed_points: float
    flow_efficiency_pct: float
    completed_later_count: int = 0
    observed_other_board_count: int = 0
    not_observed_count: int = 0
    unknown_count: int = 0
    active_count: int = 0


@dataclass
class AssigneeIssue:
    key: str
    summary: str
    sprint_name: str
    points: float | None
    category: str
    original_name: str | None = None
    route_category: str = ""
    completed_later: bool = False
    current_status: str = ""
    current_status_category: str = "unknown"


@dataclass
class AssigneeSummary:
    display_name: str
    board_name: str
    total_assigned: int
    done: int
    done_sp: float
    total_sp: float
    completion_pct: float
    spillover: int
    spillover_completed: int
    backlog: int
    reassigned: int
    items: list[AssigneeIssue]
    completed_later: int = 0
    observed_other_board: int = 0
    not_observed: int = 0
    unknown: int = 0
    active: int = 0


@dataclass
class PeriodReport:
    portfolio: PortfolioAggregate
    boards: list[BoardAggregate]
    sprints: list[SprintSummary]
    assignees: list[AssigneeSummary]
    errors: list[str]
    backlog_health: list = field(default_factory=list)
    team_scope: list = field(default_factory=list)


@dataclass
class BacklogItem:
    key: str
    summary: str
    age_days: int
    days_since_update: int
    status: str
    assignee: str


@dataclass
class BacklogHealth:
    board_name: str
    total_open: int
    aging_30plus: int
    aging_90plus: int
    aging_180plus: int
    aging_365plus: int
    aging_avg_days: float
    aging_max_days: float
    stale_count: int
    items: list = field(default_factory=list)


@dataclass
class TeamScopeItem:
    key: str
    summary: str
    status: str
    status_category: str
    assignee: str


@dataclass
class TeamScopeSummary:
    board_name: str
    total: int
    done: int
    active: int
    waiting: int
    unknown: int
    status_distribution: dict[str, int]
    items: list[TeamScopeItem]


def compute_team_scope(issues: list[dict[str, Any]], board_name: str, statuses: BoardStatuses) -> TeamScopeSummary:
    counts = {"done": 0, "active": 0, "waiting": 0, "unknown": 0}
    distribution: dict[str, int] = {}
    items: list[TeamScopeItem] = []
    for issue in issues:
        fields = issue.get("fields", {})
        classification = classify_status(fields.get("status", {}), statuses)
        counts[classification.category] += 1
        distribution[classification.name] = distribution.get(classification.name, 0) + 1
        assignee = fields.get("assignee")
        items.append(TeamScopeItem(
            key=str(issue.get("key", "")),
            summary=str(fields.get("summary", "")),
            status=classification.name,
            status_category=classification.category,
            assignee=str(assignee.get("displayName", "")) if isinstance(assignee, dict) else "",
        ))
    return TeamScopeSummary(board_name, len(issues), counts["done"], counts["active"], counts["waiting"], counts["unknown"], distribution, items)


def compute_backlog_health(issues: list[dict[str, Any]], board_name: str, today: date | None = None, done_statuses: list[str] | None = None) -> BacklogHealth:
    if today is None:
        today = date.today()
    if done_statuses:
        issues = [i for i in issues if not is_done_status(i.get("fields", {}).get("status", {}), done_statuses)]
    total = len(issues)
    ages: list[int] = []
    stale = 0
    backlog_items: list = []
    for issue in issues:
        fields = issue.get("fields", {})
        created = parse_date(fields.get("created")) if fields.get("created") else None
        updated = parse_date(fields.get("updated")) if fields.get("updated") else None
        if not created:
            continue
        age = (today - created).days
        ages.append(age)
        days_since = (today - updated).days if updated else age
        if days_since >= 90:
            stale += 1
        status_name = ""
        status_obj = fields.get("status")
        if isinstance(status_obj, dict):
            status_name = status_obj.get("name", "")
        assignee_name = ""
        assignee_obj = fields.get("assignee")
        if isinstance(assignee_obj, dict):
            assignee_name = assignee_obj.get("displayName", "")
        backlog_items.append(BacklogItem(
            key=str(issue.get("key", "")),
            summary=str(fields.get("summary", "")),
            age_days=age,
            days_since_update=days_since,
            status=status_name,
            assignee=assignee_name,
        ))
    aging_30plus = sum(1 for a in ages if a >= 30)
    aging_90plus = sum(1 for a in ages if a >= 90)
    aging_180plus = sum(1 for a in ages if a >= 180)
    aging_365plus = sum(1 for a in ages if a >= 365)
    avg_age = sum(ages) / len(ages) if ages else 0.0
    max_age = max(ages) if ages else 0
    return BacklogHealth(
        board_name=board_name,
        total_open=total,
        aging_30plus=aging_30plus,
        aging_90plus=aging_90plus,
        aging_180plus=aging_180plus,
        aging_365plus=aging_365plus,
        aging_avg_days=round(avg_age, 1),
        aging_max_days=max_age,
        stale_count=stale,
        items=backlog_items,
    )


def sprint_overlaps_period(start: date, end: date, period_start: date, period_end: date) -> bool:
    return start <= period_end and end >= period_start


def parse_date(value: object) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (TypeError, ValueError):
        return None


def parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo is not None else value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    try:
        result = datetime.fromisoformat(str(value))
        return result.astimezone(timezone.utc).replace(tzinfo=None) if result.tzinfo is not None else result
    except (TypeError, ValueError):
        return None


def normalized(value: object) -> str:
    return str(value).strip().casefold() if value is not None else ""


def is_forgotten(status: str, since_update_days: int, forgotten_age_days: int, active_statuses: list[str]) -> bool:
    if since_update_days < forgotten_age_days:
        return False
    active = {normalized(s) for s in active_statuses if normalized(s)}
    return normalized(status) in active


def is_done_status(status: dict[str, Any], done_statuses: list[str]) -> bool:
    done = {normalized(s) for s in done_statuses if normalized(s)}
    category = status.get("statusCategory", {}) if isinstance(status, dict) else {}
    values = [
        category.get("key"),
        category.get("name"),
        status.get("name") if isinstance(status, dict) else None,
    ]
    return any(normalized(value) in done for value in values)


def classify_status(status: dict[str, Any], statuses: BoardStatuses) -> StatusClassification:
    name = str(status.get("name", "")) if isinstance(status, dict) else ""
    key = normalized(name)
    matches: list[str] = []
    if is_done_status(status, statuses.done):
        matches.append("done")
    if key and key in {normalized(value) for value in statuses.active}:
        matches.append("active")
    if key and key in {normalized(value) for value in statuses.waiting}:
        matches.append("waiting")
    return StatusClassification(name, matches[0] if len(matches) == 1 else "unknown")


def is_done(issue: dict[str, Any], done_statuses: list[str]) -> bool:
    return is_done_status(issue.get("fields", {}).get("status", {}), done_statuses)


def status_name(issue: dict[str, Any]) -> str:
    status = issue.get("fields", {}).get("status", {})
    return str(status.get("name", "")) if isinstance(status, dict) else ""


def _issue_end(issue: dict[str, Any], changelog: list[dict[str, Any]] | None, done_statuses: list[str], cutoff: datetime | None = None) -> datetime | None:
    """Конец жизненного цикла задачи: resolutiondate, иначе последний переход в done-статус."""
    fields = issue.get("fields", {})
    start = parse_datetime(fields.get("created"))
    if not start:
        return None
    end = parse_datetime(fields.get("resolutiondate"))
    if end and end > start and (cutoff is None or end <= cutoff):
        return end
    done = {normalized(s) for s in done_statuses if normalized(s)}
    latest: datetime | None = None
    for history in changelog or []:
        created = parse_datetime(history.get("created"))
        if not created or created < start or (cutoff is not None and created > cutoff):
            continue
        for item in history.get("items", []):
            if isinstance(item, dict) and item.get("field") == "status" and normalized(str(item.get("toString") or "")) in done:
                if latest is None or created > latest:
                    latest = created
    return latest


def flow_efficiency_for_issue(
    issue: dict[str, Any],
    changelog: list[dict[str, Any]] | None,
    done_statuses: list[str],
    active_statuses: list[str],
    waiting_statuses: list[str],
    cutoff: datetime | None = None,
) -> tuple[float | None, set[str]]:
    fields = issue.get("fields", {})
    start = parse_datetime(fields.get("created"))
    if not start:
        return None, set()
    done = {normalized(s) for s in done_statuses if normalized(s)}
    active = {normalized(s) for s in active_statuses if normalized(s)}
    waiting = {normalized(s) for s in waiting_statuses if normalized(s)}
    transitions: list[tuple[datetime, str, str]] = []
    for history in changelog or []:
        created = parse_datetime(history.get("created"))
        if not created or created < start or (cutoff is not None and created > cutoff):
            continue
        for item in history.get("items", []):
            if isinstance(item, dict) and item.get("field") == "status":
                transitions.append((created, str(item.get("fromString") or ""), str(item.get("toString") or "")))
    transitions.sort(key=lambda item: item[0])
    end = _issue_end(issue, changelog, done_statuses, cutoff)
    if not end:
        return None, set()
    transitions = [t for t in transitions if t[0] <= end + timedelta(seconds=1)]
    current_status = transitions[0][1] if transitions else status_name(issue)
    current_time = start
    active_seconds = 0.0
    waiting_seconds = 0.0
    unknown: set[str] = set()
    for changed_at, _from_status, to_status in transitions + [(end, "", "")]:
        seconds = max(0.0, (changed_at - current_time).total_seconds())
        key = normalized(current_status)
        if key in done:
            pass
        elif key in active:
            active_seconds += seconds
        else:
            waiting_seconds += seconds
            if key and key not in waiting:
                unknown.add(current_status)
        current_time = changed_at
        if to_status:
            current_status = to_status
    total = active_seconds + waiting_seconds
    return (active_seconds / total * 100, unknown) if total > 0 else (None, unknown)


def resolve_historical_status(changelog: list[dict[str, Any]], current_status: dict[str, Any], cutoff: datetime, done_statuses: list[str]) -> bool:
    status = dict(current_status) if isinstance(current_status, dict) else {}
    if not isinstance(changelog, list):
        return is_done_status(status, done_statuses)
    changes: list[tuple[datetime, dict[str, Any]]] = []
    for history in changelog:
        if not isinstance(history, dict):
            continue
        created_val = history.get("created")
        if not created_val:
            continue
        created = parse_datetime(created_val)
        if not created or created <= cutoff:
            continue
        for item in history.get("items", []):
            if isinstance(item, dict) and item.get("field") == "status":
                changes.append((created, item))
    changes.sort(key=lambda c: c[0], reverse=True)
    for _created, item in changes:
        from_str = item.get("fromString") or ""
        status = {"name": from_str}
    result = is_done_status(status, done_statuses)
    log.debug("Historical resolve: cutoff=%s, changes_after_cutoff=%s, current=%s, resolved=%s, done=%s",
              cutoff, len(changes), current_status.get("name"), status.get("name"), result)
    return result


def assignee_name(issue: dict[str, Any]) -> str:
    assignee = issue.get("fields", {}).get("assignee")
    return assignee.get("displayName", "Unassigned") if isinstance(assignee, dict) else "Unassigned"


def resolve_historical_assignee(changelog: list[dict[str, Any]] | None, current_name: str, cutoff: datetime) -> str:
    name = current_name or "Unassigned"
    changes: list[tuple[datetime, dict[str, Any]]] = []
    for history in changelog or []:
        if not isinstance(history, dict):
            continue
        created = parse_datetime(history.get("created"))
        if not created or created <= cutoff:
            continue
        for item in history.get("items", []):
            if isinstance(item, dict) and item.get("field") == "assignee":
                changes.append((created, item))
    changes.sort(key=lambda c: c[0], reverse=True)
    for _created, item in changes:
        name = item.get("fromString") or "Unassigned"
    return name


def issue_points(issue: dict[str, Any], points_field: str | None) -> float | None:
    if not points_field:
        return None
    value = issue.get("fields", {}).get(points_field)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def completion_pct(done_points: float, total_points: float, done_issues: int, total_issues: int) -> float:
    if total_points > 0:
        return done_points / total_points * 100
    if total_issues > 0:
        return done_issues / total_issues * 100
    return 0


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * p / 100.0
    f = int(k)
    c = k - f
    if f + 1 < len(sorted_vals):
        return sorted_vals[f] + (sorted_vals[f + 1] - sorted_vals[f]) * c
    return sorted_vals[f]


def _is_done_in_sprint(issue: dict[str, Any], sprint_id: int, done_statuses: list[str], historical_done: dict[tuple[str, int], bool] | None) -> bool:
    if historical_done:
        key = issue.get("key")
        entry = historical_done.get((key, sprint_id))
        if entry is not None:
            return entry
    return is_done(issue, done_statuses)


def _future_route_keys(sprint: SprintIssues, all_sprints: list[SprintIssues]) -> tuple[set[str], set[str]]:
    same_board: set[str] = set()
    other_board: set[str] = set()
    for future in all_sprints:
        if future is sprint or future.start_date < sprint.end_date:
            continue
        keys = {issue["key"] for issue in future.issues if "key" in issue}
        if future.board_name == sprint.board_name:
            same_board |= keys
        else:
            other_board |= keys
    return same_board, other_board


def classify_not_done_route(key: str, same_board: set[str], other_board: set[str], backlog_keys: set[str], complete: bool, current_status: StatusClassification | None = None) -> str:
    if key in same_board:
        return "spillover"
    if key in other_board:
        return "observed_other_board"
    if not complete:
        return "unknown"
    if current_status is not None:
        if current_status.category == "active":
            return "active"
        if current_status.category == "waiting":
            return "backlog"
        if current_status.category == "unknown":
            return "unknown"
    if key in backlog_keys:
        return "backlog"
    return "not_observed" if complete else "unknown"


def completed_after_cutoff(changelog: list[dict[str, Any]] | None, cutoff: datetime | None, done_statuses: list[str]) -> bool:
    if cutoff is None:
        return False
    for history in changelog or []:
        changed_at = parse_datetime(history.get("created"))
        if not changed_at or changed_at <= cutoff:
            continue
        for item in history.get("items", []):
            if isinstance(item, dict) and item.get("field") == "status":
                if is_done_status({"name": item.get("toString") or ""}, done_statuses):
                    return True
    return False


def compute_spillover(
    sprints: list[SprintIssues],
    backlog_keys: set[str],
    done_statuses: list[str],
    historical_done: dict[tuple[str, int], bool] | None = None,
    all_sprints: list[SprintIssues] | None = None,
    classification_complete: bool = True,
    status_changelogs: dict[str, list[dict[str, Any]]] | None = None,
    current_statuses: dict[str, StatusClassification] | None = None,
) -> dict[int, dict[str, int]]:
    """Return per-sprint routing; reassigned is the legacy residual sum."""
    ordered = sorted(sprints, key=lambda s: s.start_date)
    sprint_keys = {s.sprint_id: {i["key"] for i in s.issues if "key" in i} for s in ordered}
    result: dict[int, dict[str, int]] = {}
    for i, sprint in enumerate(ordered):
        future_keys, other_board_keys = _future_route_keys(sprint, all_sprints or sprints)
        counts = {
            "spillover": 0, "spillover_completed": 0, "backlog": 0,
            "reassigned": 0, "completed_later": 0, "observed_other_board": 0,
            "not_observed": 0, "unknown": 0, "active": 0,
        }
        for issue in sprint.issues:
            key = issue.get("key")
            if not key or _is_done_in_sprint(issue, sprint.sprint_id, done_statuses, historical_done):
                continue
            route = "unknown" if sprint.complete_date is None else classify_not_done_route(
                key, future_keys, other_board_keys, backlog_keys, classification_complete,
                (current_statuses or {}).get(str(key)),
            )
            if route == "spillover":
                counts["spillover"] += 1
                for j in range(len(ordered) - 1, i, -1):
                    if key not in sprint_keys[ordered[j].sprint_id]:
                        continue
                    if any(
                        future_issue.get("key") == key
                        and _is_done_in_sprint(future_issue, ordered[j].sprint_id, done_statuses, historical_done)
                        for future_issue in ordered[j].issues
                    ):
                        counts["spillover_completed"] += 1
                    break
            elif route in ("active", "backlog"):
                counts[route] += 1
            else:
                counts["reassigned"] += 1
                counts[route] += 1
            if completed_after_cutoff((status_changelogs or {}).get(str(key)), sprint.complete_date, done_statuses):
                counts["completed_later"] += 1
        result[sprint.sprint_id] = counts
    return result


def summarize_sprint(sprint: SprintIssues, points_field: str | None, done_statuses: list[str], spillover_data: dict[str, int] | None = None, historical_done: dict[tuple[str, int], bool] | None = None, flow_efficiency_pct: float = 0.0, status_changelogs: dict[str, list[dict[str, Any]]] | None = None) -> SprintSummary:
    total_points = 0.0
    done_points = 0.0
    done_issues = 0
    unestimated = 0
    statuses: dict[str, int] = {}
    assignees: dict[str, int] = {}
    lead_times: list[float] = []
    ages: list[float] = []
    today = date.today()
    for issue in sprint.issues:
        fields = issue.get("fields", {})
        done = _is_done_in_sprint(issue, sprint.sprint_id, done_statuses, historical_done)
        points = issue_points(issue, points_field)
        status = fields.get("status", {}).get("name", "unknown")
        assignee = fields.get("assignee", {}).get("displayName", "Unassigned") if fields.get("assignee") else "Unassigned"
        statuses[status] = statuses.get(status, 0) + 1
        assignees[assignee] = assignees.get(assignee, 0) + 1
        if done:
            done_issues += 1
            created = parse_datetime(fields.get("created"))
            end = _issue_end(issue, (status_changelogs or {}).get(issue.get("key")), done_statuses, sprint.complete_date)
            if created and end:
                lead_times.append((end - created).total_seconds() / 86400)
        else:
            created = parse_datetime(fields.get("created"))
            if created:
                ages.append((today - created.date()).total_seconds() / 86400)
        if points is None:
            unestimated += 1
            continue
        total_points += points
        if done:
            done_points += points
    total_issues = len(sprint.issues)
    not_done = total_issues - done_issues
    sp = spillover_data or {}
    return SprintSummary(
        sprint.board_name, sprint.sprint_id, sprint.sprint_name, sprint.start_date, sprint.end_date,
        total_issues, done_issues, total_points, done_points,
        completion_pct(done_points, total_points, done_issues, total_issues),
        not_done,
        sp.get("spillover", 0),
        sp.get("spillover_completed", 0),
        sp.get("backlog", 0),
        sp.get("reassigned", 0),
        unestimated, len(sprint.removed_issues), sum(issue_points(i, points_field) or 0.0 for i in sprint.removed_issues), flow_efficiency_pct,
        _percentile(lead_times, 50),
        _percentile(lead_times, 85),
        _percentile(lead_times, 95),
        sum(ages) / len(ages) if ages else 0.0,
        max(ages) if ages else 0.0,
        statuses, assignees,
        completed_later_count=sp.get("completed_later", 0),
        observed_other_board_count=sp.get("observed_other_board", 0),
        not_observed_count=sp.get("not_observed", 0),
        unknown_count=sp.get("unknown", 0),
        active_count=sp.get("active", 0),
    )


def build_period_report(sprints: list[SprintIssues], points_field: str | None, done_statuses: list[str], backlog_keys: dict[str, set[str]] | None = None, historical_done: dict[tuple[str, int], bool] | None = None, errors: list[str] | None = None, status_changelogs: dict[str, list[dict[str, Any]]] | None = None, active_statuses: list[str] | None = None, waiting_statuses: list[str] | None = None, backlog_health: list | None = None, board_statuses: dict[str, BoardStatuses] | None = None, classification_complete_boards: set[str] | None = None, team_scope: list[TeamScopeSummary] | None = None, scope_statuses: dict[str, dict[str, StatusClassification]] | None = None, routing_sprints: list[SprintIssues] | None = None) -> PeriodReport:
    bl_keys = backlog_keys or {}
    active_default = active_statuses or []
    waiting_default = waiting_statuses or []
    board_sprints: dict[str, list[SprintIssues]] = {}
    for sprint in sprints:
        board_sprints.setdefault(sprint.board_name, []).append(sprint)
    log.info("Building report from %s boards, %s sprints, %s historical overrides",
             len(board_sprints), len(sprints), len(historical_done or {}))
    spillover_maps: dict[str, dict[int, dict[str, int]]] = {}
    for board_name, bsprints in board_sprints.items():
        bk = bl_keys.get(board_name, set())
        bs = _statuses_for(board_name, board_statuses, done_statuses, active_default, waiting_default)
        complete = classification_complete_boards is None or board_name in classification_complete_boards
        spillover_maps[board_name] = compute_spillover(
            bsprints, bk, bs.done, historical_done, routing_sprints or sprints, complete, status_changelogs,
            (scope_statuses or {}).get(board_name),
        )
        log.debug("Spillover for %s: %s sprints, backlog_keys=%s, map=%s",
                  board_name, len(bsprints), len(bk), {k: v for k, v in spillover_maps[board_name].items()})
    flow_by_sprint, unknown_statuses = compute_flow_efficiency_by_sprint(sprints, done_statuses, status_changelogs or {}, active_default, waiting_default, historical_done, board_statuses)
    report_errors = list(errors or [])
    if unknown_statuses:
        report_errors.append("Unknown flow statuses counted as waiting: " + ", ".join(sorted(unknown_statuses)))
    summaries = [summarize_sprint(sprint, points_field, _statuses_for(sprint.board_name, board_statuses, done_statuses, active_default, waiting_default).done, spillover_maps.get(sprint.board_name, {}).get(sprint.sprint_id), historical_done, flow_by_sprint.get(sprint.sprint_id, 0.0), status_changelogs) for sprint in sprints]
    for s in summaries:
        log.debug("Sprint %s/%s: total=%s done=%s pts=%s/%s not_done=%s spill=%s→%s backlog=%s reassign=%s unest=%s",
                  s.board_name, s.sprint_name, s.total_issues, s.done_issues,
                  s.done_points, s.total_points, s.not_done_count,
                  s.spillover_count, s.spillover_completed,
                  s.backlog_count, s.reassigned_count, s.unestimated_count)
    board_names = sorted(board_sprints)
    boards = [_aggregate_board(name, board_sprints[name], points_field, _statuses_for(name, board_statuses, done_statuses, active_default, waiting_default).done, summaries, historical_done) for name in board_names]
    portfolio = _aggregate_portfolio(sprints, boards, points_field, done_statuses, summaries, historical_done, board_statuses)
    assignees = compute_assignee_stats(sprints, done_statuses, points_field, historical_done, bl_keys, status_changelogs or {}, backlog_health=backlog_health, board_statuses=board_statuses, classification_complete_boards=classification_complete_boards, scope_statuses=scope_statuses, routing_sprints=routing_sprints)
    log.info("Assignees: %s people across %s boards", len(assignees), len(board_sprints))
    return PeriodReport(portfolio, boards, summaries, assignees, report_errors, list(backlog_health or []), list(team_scope or []))


def compute_flow_efficiency_by_sprint(
    sprints: list[SprintIssues],
    done_statuses: list[str],
    status_changelogs: dict[str, list[dict[str, Any]]],
    active_statuses: list[str],
    waiting_statuses: list[str],
    historical_done: dict[tuple[str, int], bool] | None = None,
    board_statuses: dict[str, BoardStatuses] | None = None,
) -> tuple[dict[int, float], set[str]]:
    result: dict[int, float] = {}
    unknown: set[str] = set()
    for sprint in sprints:
        bs = _statuses_for(sprint.board_name, board_statuses, done_statuses, active_statuses, waiting_statuses)
        values: list[float] = []
        for issue in sprint.issues:
            if not _is_done_in_sprint(issue, sprint.sprint_id, bs.done, historical_done):
                continue
            value, issue_unknown = flow_efficiency_for_issue(
                issue,
                status_changelogs.get(str(issue.get("key"))),
                bs.done,
                bs.active,
                bs.waiting,
                sprint.complete_date,
            )
            unknown |= issue_unknown
            if value is not None:
                values.append(value)
        result[sprint.sprint_id] = sum(values) / len(values) if values else 0.0
    return result, unknown


def collect_team_members(sprints: list[SprintIssues], backlog_health: list | None = None) -> dict[str, set[str]]:
    """Собирает текущих assignee per board из sprint issues и backlog health."""
    team_members: dict[str, set[str]] = {}
    for s in sprints:
        tm = team_members.setdefault(s.board_name, set())
        for issue in s.issues:
            name = assignee_name(issue)
            if name != "Unassigned":
                tm.add(name)
    if backlog_health:
        for bh in backlog_health:
            if not hasattr(bh, "board_name"):
                continue
            tm = team_members.setdefault(bh.board_name, set())
            if hasattr(bh, "items"):
                for item in bh.items:
                    if item.assignee != "Unassigned":
                        tm.add(item.assignee)
    return team_members


def compute_assignee_stats(sprints: list[SprintIssues], done_statuses: list[str], points_field: str | None, historical_done: dict[tuple[str, int], bool] | None, backlog_keys: dict[str, set[str]], status_changelogs: dict[str, list[dict[str, Any]]] | None = None, backlog_health: list | None = None, board_statuses: dict[str, BoardStatuses] | None = None, classification_complete_boards: set[str] | None = None, scope_statuses: dict[str, dict[str, StatusClassification]] | None = None, routing_sprints: list[SprintIssues] | None = None) -> list[AssigneeSummary]:
    board_sprints_map: dict[str, list[SprintIssues]] = {}
    for sprint in sprints:
        board_sprints_map.setdefault(sprint.board_name, []).append(sprint)

    team_members = collect_team_members(sprints, backlog_health)
    groups: dict[tuple[str, str], dict[str, object]] = {}
    for sprint in sprints:
        future_keys, other_board_keys = _future_route_keys(sprint, routing_sprints or sprints)
        ordered = sorted(board_sprints_map.get(sprint.board_name, []), key=lambda s: s.start_date)
        bk = backlog_keys.get(sprint.board_name, set())
        bs_done = _statuses_for(sprint.board_name, board_statuses, done_statuses, [], []).done
        complete = classification_complete_boards is None or sprint.board_name in classification_complete_boards
        for issue in sprint.issues:
            key = issue.get("key") or ""
            fields = issue.get("fields", {})
            name = assignee_name(issue)
            if sprint.complete_date:
                name = resolve_historical_assignee((status_changelogs or {}).get(str(key)), name, sprint.complete_date)
            external = name != "Unassigned" and name not in team_members.get(sprint.board_name, set())
            original_name = None
            if external:
                original_name = name
                name = "External / Other"
            ak = (name, sprint.board_name)
            if ak not in groups:
                groups[ak] = {"total": 0, "done": 0, "done_sp": 0.0, "total_sp": 0.0,
                              "spillover": 0, "spillover_completed": 0, "backlog": 0, "reassigned": 0,
                              "completed_later": 0, "observed_other_board": 0, "not_observed": 0, "unknown": 0,
                              "active": 0,
                              "items": []}
            g = groups[ak]
            g["total"] += 1
            done = _is_done_in_sprint(issue, sprint.sprint_id, bs_done, historical_done)
            pts = issue_points(issue, points_field)
            summary_text = fields.get("summary", "")
            if pts is not None:
                g["total_sp"] += pts
            route = "done"
            current_status = (scope_statuses or {}).get(sprint.board_name, {}).get(key)
            current_status = current_status or classify_status(fields.get("status", {}), _statuses_for(sprint.board_name, board_statuses, done_statuses, [], []))
            if done:
                g["done"] += 1
                if pts is not None:
                    g["done_sp"] += pts
                category = "done"
            else:
                route = "unknown" if sprint.complete_date is None else classify_not_done_route(
                    key, future_keys, other_board_keys, bk, complete,
                    (scope_statuses or {}).get(sprint.board_name, {}).get(key),
                )
                if route == "spillover":
                    g["spillover"] += 1
                    for j in range(len(ordered) - 1, -1, -1):
                        if ordered[j].sprint_id == sprint.sprint_id:
                            break
                        if key in {i["key"] for i in ordered[j].issues if "key" in i}:
                            for fi in ordered[j].issues:
                                if fi.get("key") == key and _is_done_in_sprint(fi, ordered[j].sprint_id, bs_done, historical_done):
                                    g["spillover_completed"] += 1
                                    break
                            break
                    category = "spillover"
                elif route in ("active", "backlog"):
                    g[route] += 1
                    category = route
                else:
                    g["reassigned"] += 1
                    g[route] += 1
                    category = "reassigned"
            completed_later = not done and completed_after_cutoff((status_changelogs or {}).get(str(key)), sprint.complete_date, bs_done)
            if completed_later:
                g["completed_later"] += 1
            items = g["items"]
            if isinstance(items, list):
                items.append(AssigneeIssue(key=key, summary=summary_text, sprint_name=sprint.sprint_name, points=pts, category=category, original_name=original_name, route_category=route, completed_later=completed_later, current_status=current_status.name, current_status_category=current_status.category))
    result: list[AssigneeSummary] = []
    for (name, board), g in groups.items():
        items: list[AssigneeIssue] = g.get("items", []) if isinstance(g.get("items"), list) else []
        result.append(AssigneeSummary(
            display_name=name, board_name=board,
            total_assigned=int(g["total"]), done=int(g["done"]),
            done_sp=g["done_sp"], total_sp=g["total_sp"],
            completion_pct=completion_pct(g["done_sp"], g["total_sp"], int(g["done"]), int(g["total"])),
            spillover=int(g["spillover"]), spillover_completed=int(g["spillover_completed"]),
            backlog=int(g["backlog"]), reassigned=int(g["reassigned"]),
            items=items,
            completed_later=int(g["completed_later"]),
            observed_other_board=int(g["observed_other_board"]),
            not_observed=int(g["not_observed"]),
            unknown=int(g["unknown"]),
            active=int(g["active"]),
        ))
    result.sort(key=lambda a: a.done_sp, reverse=True)
    return result


def _unique_issues(sprints: list[SprintIssues]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for sprint in sprints:
        for issue in sprint.issues:
            key = issue.get("key")
            if key and key not in seen:
                seen.add(key)
                result.append(issue)
    return result


def _aggregate_board(name: str, sprints: list[SprintIssues], points_field: str | None, done_statuses: list[str], summaries: list[SprintSummary], historical_done: dict[tuple[str, int], bool] | None = None) -> BoardAggregate:
    issues = _unique_issues(sprints)
    total_issues, _cd, _ctp, _cdp, unestimated = _issue_totals(issues, points_field, done_statuses)
    done_issues, done_points, total_points = _aggregate_done_from_sprints(sprints, points_field, done_statuses, historical_done)
    board_summaries = [s for s in summaries if s.board_name == name]
    not_done = sum(s.not_done_count for s in board_summaries)
    spillover = sum(s.spillover_count for s in board_summaries)
    spillover_completed = sum(s.spillover_completed for s in board_summaries)
    backlog = sum(s.backlog_count for s in board_summaries)
    reassigned = sum(s.reassigned_count for s in board_summaries)
    removed = sum(s.removed_count for s in board_summaries)
    removed_points = sum(s.removed_points for s in board_summaries)
    completed_later = sum(s.completed_later_count for s in board_summaries)
    observed_other_board = sum(s.observed_other_board_count for s in board_summaries)
    not_observed = sum(s.not_observed_count for s in board_summaries)
    unknown = sum(s.unknown_count for s in board_summaries)
    active = sum(s.active_count for s in board_summaries)
    velocities = [s.done_points for s in board_summaries]
    flow = _weighted_flow_efficiency(board_summaries)
    return BoardAggregate(
        name, len(sprints), total_issues, done_issues, total_points, done_points,
        sum(velocities) / len(velocities) if velocities else 0,
        completion_pct(done_points, total_points, done_issues, total_issues),
        not_done, spillover, spillover_completed,
        spillover_completed / spillover * 100 if spillover else 0,
        backlog, reassigned, unestimated, removed, removed_points, flow,
        completed_later_count=completed_later,
        observed_other_board_count=observed_other_board,
        not_observed_count=not_observed,
        unknown_count=unknown,
        active_count=active,
    )


def _aggregate_portfolio(sprints: list[SprintIssues], boards: list[BoardAggregate], points_field: str | None, done_statuses: list[str], summaries: list[SprintSummary], historical_done: dict[tuple[str, int], bool] | None = None, board_statuses: dict[str, BoardStatuses] | None = None) -> PortfolioAggregate:
    issues = _unique_issues(sprints)
    total_issues, _cd, _ctp, _cdp, unestimated = _issue_totals(issues, points_field, done_statuses)
    done_issues, done_points, total_points = _aggregate_done_from_sprints(sprints, points_field, done_statuses, historical_done, board_statuses)
    not_done = sum(s.not_done_count for s in summaries)
    spillover = sum(s.spillover_count for s in summaries)
    spillover_completed = sum(s.spillover_completed for s in summaries)
    backlog = sum(s.backlog_count for s in summaries)
    reassigned = sum(s.reassigned_count for s in summaries)
    removed = sum(s.removed_count for s in summaries)
    removed_points = sum(s.removed_points for s in summaries)
    completed_later = sum(s.completed_later_count for s in summaries)
    observed_other_board = sum(s.observed_other_board_count for s in summaries)
    not_observed = sum(s.not_observed_count for s in summaries)
    unknown = sum(s.unknown_count for s in summaries)
    active = sum(s.active_count for s in summaries)
    flow = _weighted_flow_efficiency(summaries)
    return PortfolioAggregate(
        len(boards), len(sprints), total_issues, done_issues, total_points, done_points,
        sum(board.avg_velocity for board in boards) / len(boards) if boards else 0,
        completion_pct(done_points, total_points, done_issues, total_issues),
        not_done, spillover, spillover_completed, backlog, reassigned, unestimated, removed, removed_points, flow,
        completed_later_count=completed_later,
        observed_other_board_count=observed_other_board,
        not_observed_count=not_observed,
        unknown_count=unknown,
        active_count=active,
    )


def _weighted_flow_efficiency(summaries: list[SprintSummary]) -> float:
    done = sum(s.done_issues for s in summaries)
    if done <= 0:
        return 0.0
    return sum(s.flow_efficiency_pct * s.done_issues for s in summaries) / done


def _issue_totals(issues: list[dict[str, Any]], points_field: str | None, done_statuses: list[str]) -> tuple[int, int, float, float, int]:
    done_issues = 0
    total_points = 0.0
    done_points = 0.0
    unestimated = 0
    for issue in issues:
        done = is_done(issue, done_statuses)
        points = issue_points(issue, points_field)
        if done:
            done_issues += 1
        if points is None:
            unestimated += 1
        else:
            total_points += points
            if done:
                done_points += points
    return len(issues), done_issues, total_points, done_points, unestimated


def _aggregate_done_from_sprints(sprints: list[SprintIssues], points_field: str | None, done_statuses: list[str], historical_done: dict[tuple[str, int], bool] | None, board_statuses: dict[str, BoardStatuses] | None = None) -> tuple[int, float, float]:
    ordered = sorted(sprints, key=lambda s: s.start_date)
    key_done: dict[str, bool] = {}
    key_points: dict[str, float] = {}
    for sprint in ordered:
        bs_done = _statuses_for(sprint.board_name, board_statuses, done_statuses, [], []).done
        for issue in sprint.issues:
            key = issue.get("key")
            if not key:
                continue
            pts = issue_points(issue, points_field)
            if pts is not None:
                key_points[key] = pts
            key_done[key] = _is_done_in_sprint(issue, sprint.sprint_id, bs_done, historical_done)
    done_issues = sum(1 for v in key_done.values() if v)
    done_points = sum(pts for k, pts in key_points.items() if key_done.get(k))
    total_points = sum(key_points.values())
    return done_issues, done_points, total_points


def build_chart_slots(sprints: list[SprintSummary], period_fn: Callable[[date, date], str]) -> list[dict[str, object]]:
    """Агрегирует спринты по (team, period, startDate), нумерует глобально."""
    aggregated: dict[tuple[str, str, str], dict[str, object]] = {}
    for sprint in sprints:
        start = sprint.start_date.isoformat()
        end = sprint.end_date.isoformat()
        slot_key = (sprint.board_name, period_fn(sprint.start_date, sprint.end_date), start)
        if slot_key not in aggregated:
            aggregated[slot_key] = dict(team=sprint.board_name, period=slot_key[1], startDate=start, endDate=end,
                chartSlotLabel="", done=0, total=0, totalPoints=0.0, donePoints=0.0, points=0.0,
                spillover=0, spilloverCompleted=0, backlog=0, reassigned=0,
                completedLater=0, observedOtherBoard=0, notObserved=0, unknown=0, active=0,
                notDone=0, removed=0, removedPoints=0.0,
                completion=0.0, flowEfficiency=0.0,
                leadTimeP50=0.0, leadTimeP85=0.0, leadTimeP95=0.0,
                agingAvg=0.0, agingMax=0, statusDistribution="{}")
        s = aggregated[slot_key]
        s["done"] = int(s["done"]) + sprint.done_issues
        s["total"] = int(s["total"]) + sprint.total_issues
        s["totalPoints"] = float(s["totalPoints"]) + sprint.total_points
        s["donePoints"] = float(s["donePoints"]) + sprint.done_points
        s["points"] = s["donePoints"]
        s["spillover"] = int(s["spillover"]) + sprint.spillover_count
        s["spilloverCompleted"] = int(s["spilloverCompleted"]) + sprint.spillover_completed
        s["backlog"] = int(s["backlog"]) + sprint.backlog_count
        s["reassigned"] = int(s["reassigned"]) + sprint.reassigned_count
        s["completedLater"] = int(s["completedLater"]) + getattr(sprint, "completed_later_count", 0)
        s["observedOtherBoard"] = int(s["observedOtherBoard"]) + getattr(sprint, "observed_other_board_count", 0)
        s["notObserved"] = int(s["notObserved"]) + getattr(sprint, "not_observed_count", 0)
        s["unknown"] = int(s["unknown"]) + getattr(sprint, "unknown_count", 0)
        s["active"] = int(s["active"]) + getattr(sprint, "active_count", 0)
        s["notDone"] = int(s["notDone"]) + sprint.not_done_count
        s["removed"] = int(s["removed"]) + sprint.removed_count
        s["removedPoints"] = float(s["removedPoints"]) + sprint.removed_points

    slots = list(aggregated.values())
    for s in slots:
        if float(s["totalPoints"]) > 0:
            s["completion"] = float(s["donePoints"]) / float(s["totalPoints"]) * 100
        elif int(s["total"]) > 0:
            s["completion"] = int(s["done"]) / int(s["total"]) * 100
        slot_sprints = [spr for spr in sprints
            if spr.board_name == s["team"]
            and period_fn(spr.start_date, spr.end_date) == s["period"]
            and spr.start_date.isoformat() == s["startDate"]]
        fe_sprints = slot_sprints
        s["flowEfficiency"] = sum(spr.flow_efficiency_pct for spr in fe_sprints) / max(1, len(fe_sprints))
        if slot_sprints:
            s["leadTimeP50"] = _percentile([spr.lead_time_p50 for spr in slot_sprints], 50)
            s["leadTimeP85"] = _percentile([spr.lead_time_p85 for spr in slot_sprints], 85)
            s["leadTimeP95"] = _percentile([spr.lead_time_p95 for spr in slot_sprints], 95)
            nd_total = sum(spr.not_done_count for spr in slot_sprints)
            s["agingAvg"] = sum(spr.aging_avg_days * spr.not_done_count for spr in slot_sprints) / max(1, nd_total) if nd_total > 0 else 0.0
            s["agingMax"] = max(spr.aging_max_days for spr in slot_sprints)
            merged_dist: dict[str, int] = {}
            for spr in slot_sprints:
                d = getattr(spr, "status_distribution", {}) or {}
                for k, v in d.items():
                    merged_dist[k] = merged_dist.get(k, 0) + v
            s["statusDistribution"] = json.dumps(merged_dist, ensure_ascii=False)

    slots.sort(key=lambda s: (str(s["period"]), str(s["startDate"]), str(s["team"])))
    label_map: dict[tuple[str, str], str] = {}
    counters: dict[str, int] = {}
    for s in slots:
        p = str(s["period"])
        date_key = (p, str(s["startDate"]))
        if date_key not in label_map:
            counters[p] = counters.get(p, 0) + 1
            label_map[date_key] = f"{p}S{counters[p]}"
        s["chartSlotLabel"] = label_map[date_key]
    return slots
