import json
import os
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PyQt5.QtCore import QObject, pyqtSignal

from jira_analytics import (
    alert_kind,
    alert_issue,
    build_notify_command,
    collect_alert_result,
    collect_period_result,
    Config,
    DEFAULT_DONE_STATUSES,
    DEFAULT_ITEMS,
    ensure_user_files,
    env_values,
    issue_key_from_text,
    items_from_text,
    load_config_text,
    load_period_source_cache,
    normalize_jira_url,
    notify,
    parse_date,
    save_config,
    save_env,
    save_period_source_cache,
    source_kind,
    sprint_report_removed_issues,
)
from jira_period_analytics import AssigneeIssue, BacklogHealth, SprintIssues, build_chart_slots, build_period_report, compute_backlog_health, compute_spillover, flow_efficiency_for_issue, is_done, is_done_status, is_forgotten, resolve_historical_status, sprint_overlaps_period, summarize_sprint
from jira_analytics_gui import Backend, ModuleApi, period_label_for_sprint
from jira_modules import ModuleDescriptor, discover_modules

TEST_ENV = {"JIRA_URL": "https://jira.example.org"}


def write_module(root: Path, name: str, manifest: dict, qml: str = "import QtQuick 2.12\nItem {}\n") -> Path:
    module_dir = root / name
    module_dir.mkdir(parents=True)
    qml_name = manifest.get("qml", "Module.qml")
    if isinstance(qml_name, str) and not qml_name.startswith("/") and ".." not in qml_name:
        resolved = (module_dir / qml_name).resolve()
        try:
            resolved.relative_to(module_dir.resolve())
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(qml, encoding="utf-8")
        except (ValueError, OSError):
            pass
    (module_dir / "module.json").write_text(json.dumps(manifest), encoding="utf-8")
    return module_dir


def _valid_manifest(**overrides) -> dict:
    base = {"id": "test_mod", "title": "Test", "qml": "Module.qml", "order": 0, "apiVersion": 1}
    base.update(overrides)
    return base


def issue(key, status_key="done", points=1, assignee="Ivan", status_name=None, category_name=None):
    return {
        "key": key,
        "fields": {
            "status": {
                "name": status_name or status_key.title(),
                "statusCategory": {"key": status_key, "name": category_name or status_key.title()},
            },
            "customfield_1": points,
            "assignee": {"displayName": assignee} if assignee else None,
        },
    }


def test_issue_key_from_text():
    assert issue_key_from_text("AL-5948") == "AL-5948"
    assert issue_key_from_text("https://jira.example.org/browse/AL-5948") == "AL-5948"
    assert issue_key_from_text("project = AL") is None
    assert issue_key_from_text("key in (AL-5948, AL-1234)") is None


def test_source_kind():
    assert source_kind("AL-5948") == "issue"
    assert source_kind("https://jira.example.org/browse/AL-5948") == "issue"
    assert source_kind("project = AL AND statusCategory != Done") == "jql"
    assert source_kind("key in (AL-5948, AL-1234)") == "jql"


def test_normalize_jira_url():
    assert normalize_jira_url("jira.example.org") == "https://jira.example.org"
    assert normalize_jira_url("https://jira.example.org/") == "https://jira.example.org"
    assert normalize_jira_url("http://jira.example.org") == "http://jira.example.org"


def test_items_from_text():
    assert items_from_text("") == DEFAULT_ITEMS
    assert items_from_text(" AL-1 \n\n project = AL ") == ["AL-1", "project = AL"]


def test_sprint_overlaps_period():
    period_start = date(2026, 4, 1)
    period_end = date(2026, 6, 30)
    assert sprint_overlaps_period(date(2026, 4, 2), date(2026, 4, 15), period_start, period_end)
    assert sprint_overlaps_period(date(2026, 3, 20), date(2026, 4, 3), period_start, period_end)
    assert sprint_overlaps_period(date(2026, 6, 25), date(2026, 7, 5), period_start, period_end)
    assert not sprint_overlaps_period(date(2026, 7, 1), date(2026, 7, 15), period_start, period_end)


def test_period_label_for_sprint_uses_two_thirds_rule():
    assert period_label_for_sprint(date(2026, 3, 22), date(2026, 3, 31)) == "Q1"
    assert period_label_for_sprint(date(2026, 3, 29), date(2026, 4, 7)) == "Q2"
    assert period_label_for_sprint(date(2026, 3, 27), date(2026, 4, 5)) == "Q1"


def test_add_period_sprint_labels_numbers_by_team_and_period():
    sprints = [
        {"team": "A", "startDate": "2026-04-15", "endDate": "2026-04-28", "period": "Q2"},
        {"team": "A", "startDate": "2026-04-01", "endDate": "2026-04-14", "period": "Q2"},
        {"team": "B", "startDate": "2026-04-01", "endDate": "2026-04-14", "period": "Q2"},
    ]
    Backend.add_period_sprint_labels(sprints)
    assert [item["periodSprintLabel"] for item in sprints] == ["Q2S2", "Q2S1", "Q2S1"]


def test_summarize_sprint_counts_not_done_and_unestimated():
    sprint = SprintIssues(
        "Team A", 1, "Sprint 1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14),
        [issue("A-1", "done", 3), issue("A-2", "indeterminate", None), issue("A-3", "new", "bad")],
    )

    summary = summarize_sprint(sprint, "customfield_1", ["done"])

    assert summary.total_issues == 3
    assert summary.done_issues == 1
    assert summary.done_points == 3
    assert summary.not_done_count == 2
    assert summary.spillover_count == 0
    assert summary.unestimated_count == 2


def test_summarize_sprint_lead_time_uses_changelog_fallback():
    it = issue("A-1", "done", 3)
    it["fields"]["created"] = "2026-04-01T00:00:00"
    it["fields"]["resolutiondate"] = None
    sprint = SprintIssues(
        "Team A", 1, "Sprint 1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14),
        [it],
    )
    changelog = {"A-1": [
        {"created": "2026-04-05T00:00:00", "items": [
            {"field": "status", "fromString": "In Progress", "toString": "Done"},
        ]},
    ]}

    summary = summarize_sprint(sprint, "customfield_1", ["done"], status_changelogs=changelog)

    assert summary.lead_time_p50 == 4.0
    assert summary.lead_time_p85 == 4.0
    assert summary.lead_time_p95 == 4.0


def test_summarize_sprint_lead_time_prefers_resolutiondate():
    it = issue("A-1", "done", 3)
    it["fields"]["created"] = "2026-04-01T00:00:00"
    it["fields"]["resolutiondate"] = "2026-04-10T00:00:00"
    sprint = SprintIssues(
        "Team A", 1, "Sprint 1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14),
        [it],
    )
    changelog = {"A-1": [
        {"created": "2026-04-05T00:00:00", "items": [
            {"field": "status", "fromString": "In Progress", "toString": "Done"},
        ]},
    ]}

    summary = summarize_sprint(sprint, "customfield_1", ["done"], status_changelogs=changelog)

    assert summary.lead_time_p50 == 9.0


def test_summarize_sprint_counts_removed_issues_separately():
    sprint = SprintIssues(
        "Team A", 1, "Sprint 1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14),
        [issue("A-1", "done", 3)],
        [issue("A-2", "new", 5), issue("A-3", "new", None)],
    )

    report = build_period_report([sprint], "customfield_1", ["done"])

    assert report.sprints[0].done_issues == 1
    assert report.sprints[0].done_points == 3
    assert report.sprints[0].removed_count == 2
    assert report.sprints[0].removed_points == 5
    assert report.boards[0].removed_count == 2
    assert report.portfolio.removed_points == 5


def test_sprint_report_removed_issues_reads_punted_issues():
    class FakeJira:
        def get(self, path):
            assert "sprintreport" in path
            return {"contents": {"puntedIssues": [
                {"key": "A-2", "summary": "Removed", "estimateStatistic": {"statFieldValue": {"value": 5}}},
                {"key": "A-3", "summary": "No estimate", "estimateStatistic": {}},
            ]}}

    removed = sprint_report_removed_issues(FakeJira(), 101, 1, "customfield_1")

    assert [item["key"] for item in removed] == ["A-2", "A-3"]
    assert removed[0]["fields"]["customfield_1"] == 5


def test_period_report_deduplicates_portfolio_totals():
    sprints = [
        SprintIssues("Team A", 1, "Sprint 1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "done", 5)]),
        SprintIssues("Team B", 2, "Sprint 2", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "done", 5)]),
    ]

    report = build_period_report(sprints, "customfield_1", ["done"])

    assert report.portfolio.boards_count == 2
    assert report.portfolio.total_issues == 1
    assert report.portfolio.total_points == 5


def test_is_done_uses_configured_statuses():
    assert is_done(issue("A-1", "Done"), ["done"])
    assert is_done(issue("A-2", "new", status_name="Готово"), ["Готово"])
    assert is_done(issue("A-3", "new", category_name="Сделано"), ["сделано"])
    assert not is_done(issue("A-4", "new", status_name="Готово"), ["done"])


def test_is_done_uses_default_russian_statuses():
    assert is_done(issue("A-1", "new", status_name="На ревизии"), DEFAULT_DONE_STATUSES)
    assert is_done(issue("A-2", "new", status_name="Ожидает релиз"), DEFAULT_DONE_STATUSES)
    assert is_done(issue("A-3", "QA-RC"), DEFAULT_DONE_STATUSES)
    assert is_done(issue("A-4", "new", status_name="Ожидает закрытия подзадач"), DEFAULT_DONE_STATUSES)
    assert is_done(issue("A-5", "new", status_name="Код написан"), DEFAULT_DONE_STATUSES)


def test_is_forgotten_threshold_boundary():
    assert not is_forgotten("In Progress", 29, 30, ["In Progress"])
    assert is_forgotten("In Progress", 30, 30, ["In Progress"])
    assert is_forgotten("In Progress", 31, 30, ["In Progress"])


def test_is_forgotten_status_case_insensitive():
    assert is_forgotten("in progress", 30, 30, ["In Progress"])
    assert is_forgotten("IN PROGRESS", 30, 30, ["In Progress"])


def test_is_forgotten_requires_active_status():
    assert not is_forgotten("To Do", 30, 30, ["In Progress"])
    assert not is_forgotten("", 30, 30, ["In Progress"])


def test_is_forgotten_active_statuses_case_insensitive():
    assert is_forgotten("In Progress", 30, 30, ["in progress", "code review"])


def test_flow_efficiency_splits_active_waiting_and_done_time():
    item = issue("A-1", "done", status_name="Done")
    item["fields"]["created"] = "2026-04-01T00:00:00"
    item["fields"]["resolutiondate"] = "2026-04-04T00:00:00"
    changelog = [
        {"created": "2026-04-02T00:00:00", "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"}]},
        {"created": "2026-04-03T00:00:00", "items": [{"field": "status", "fromString": "In Progress", "toString": "Done"}]},
    ]
    value, unknown = flow_efficiency_for_issue(item, changelog, ["Done"], ["In Progress"], ["To Do"])
    assert round(value or 0) == 50
    assert unknown == set()


def test_flow_efficiency_counts_unknown_as_waiting():
    item = issue("A-1", "done", status_name="Done")
    item["fields"]["created"] = "2026-04-01T00:00:00"
    item["fields"]["resolutiondate"] = "2026-04-03T00:00:00"
    changelog = [
        {"created": "2026-04-02T00:00:00", "items": [{"field": "status", "fromString": "Analysis", "toString": "In Progress"}]},
    ]
    value, unknown = flow_efficiency_for_issue(item, changelog, ["Done"], ["In Progress"], [])
    assert round(value or 0) == 50
    assert unknown == {"Analysis"}


def test_flow_efficiency_ignores_transitions_after_closed_sprint_cutoff():
    item = issue("A-1", "done", status_name="Done")
    item["fields"]["created"] = "2026-04-01T00:00:00"
    item["fields"]["resolutiondate"] = "2026-04-06T00:00:00"
    changelog = [
        {"created": "2026-04-02T00:00:00", "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"}]},
        {"created": "2026-04-03T00:00:00", "items": [{"field": "status", "fromString": "In Progress", "toString": "Done"}]},
        {"created": "2026-04-05T00:00:00", "items": [{"field": "status", "fromString": "Done", "toString": "In Progress"}]},
        {"created": "2026-04-06T00:00:00", "items": [{"field": "status", "fromString": "In Progress", "toString": "Done"}]},
    ]

    value, unknown = flow_efficiency_for_issue(
        item, changelog, ["Done"], ["In Progress"], ["To Do"], datetime(2026, 4, 4)
    )

    assert round(value or 0) == 50
    assert unknown == set()


def test_period_report_exposes_flow_efficiency():
    item = issue("A-1", "done", status_name="Done")
    item["fields"]["created"] = "2026-04-01T00:00:00"
    item["fields"]["resolutiondate"] = "2026-04-03T00:00:00"
    sprint = SprintIssues("Team A", 1, "Sprint 1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [item])
    changelog = {
        "A-1": [
            {"created": "2026-04-02T00:00:00", "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"}]},
        ]
    }
    report = build_period_report([sprint], None, ["Done"], status_changelogs=changelog, active_statuses=["In Progress"], waiting_statuses=["To Do"])
    assert round(report.sprints[0].flow_efficiency_pct) == 50
    assert round(report.portfolio.flow_efficiency_pct) == 50


def test_period_report_flow_efficiency_uses_historical_done_filter():
    item1 = issue("A-1", "done", status_name="Done")
    item1["fields"]["created"] = "2026-04-01T00:00:00"
    item1["fields"]["resolutiondate"] = "2026-04-03T00:00:00"
    item2 = issue("A-2", "done", status_name="Done")
    item2["fields"]["created"] = "2026-04-01T00:00:00"
    item2["fields"]["resolutiondate"] = "2026-04-03T00:00:00"
    sprint = SprintIssues("Team A", 1, "Sprint 1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [item1, item2])
    changelog = {
        "A-1": [{"created": "2026-04-02T00:00:00", "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"}]}],
        "A-2": [{"created": "2026-04-01T00:00:00", "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"}]}],
    }

    report = build_period_report([sprint], None, ["Done"], historical_done={("A-1", 1): True, ("A-2", 1): False}, status_changelogs=changelog, active_statuses=["In Progress"], waiting_statuses=["To Do"])

    assert report.sprints[0].done_issues == 1
    assert round(report.sprints[0].flow_efficiency_pct) == 50


def test_load_config_mapping():
    cfg = load_config_text(
        """
warn_days: 3
target_end_field: Target end
items:
  - AL-5948
  - project = AL
"""
    )
    assert cfg.warn_days == 3
    assert cfg.target_end_field == "Target end"
    assert cfg.first_check_delay_minutes == 2
    assert cfg.check_interval_minutes == 60
    assert cfg.connect_timeout_seconds == 15
    assert cfg.notifications_enabled is True
    assert cfg.items == ["AL-5948", "project = AL"]


def test_load_config_gui_fields():
    cfg = load_config_text(
        """
warn_days: 3
first_check_delay_minutes: 5
check_interval_minutes: 30
connect_timeout_seconds: 9
notifications_enabled: false
items:
  - AL-5948
"""
    )
    assert cfg.first_check_delay_minutes == 5
    assert cfg.check_interval_minutes == 30
    assert cfg.connect_timeout_seconds == 9
    assert cfg.notifications_enabled is False


def test_save_config_preserves_items_and_toggles_notifications(tmp_path):
    path = tmp_path / "config.yaml"
    cfg = load_config_text("items:\n  - AL-5948\n")
    cfg.notifications_enabled = False

    save_config(path, cfg)

    saved = load_config_text(path.read_text(encoding="utf-8"))
    assert saved.notifications_enabled is False
    assert saved.items == ["AL-5948"]


def test_load_period_config():
    cfg = load_config_text(
        """
analytics_enabled: true
period_start: 2026-04-01
period_end: 2026-06-30
boards:
  - id: 101
    name: Team A
story_points_field: Story points
done_statuses:
  - done
  - Готово
active_statuses:
  - In Progress
waiting_statuses:
  - To Do
"""
    )
    assert cfg.analytics_enabled is True
    assert cfg.period_start == date(2026, 4, 1)
    assert cfg.period_end == date(2026, 6, 30)
    assert cfg.boards[0].id == 101
    assert cfg.boards[0].name == "Team A"
    assert cfg.story_points_field == "Story points"
    assert cfg.done_statuses == ["done", "Готово"]
    assert cfg.active_statuses == ["In Progress"]
    assert cfg.waiting_statuses == ["To Do"]


def test_analytics_enabled_by_default():
    assert Config().analytics_enabled is True
    assert load_config_text("items: []\n").analytics_enabled is True


def test_analytics_enabled_rejects_string():
    for value in ('"false"', "null", '""'):
        try:
            load_config_text(f"analytics_enabled: {value}\n")
        except SystemExit as exc:
            assert "analytics_enabled" in str(exc)
            assert "boolean" in str(exc)
        else:
            raise AssertionError(f"non-boolean analytics_enabled={value} must fail")


def test_config_period_rejects_non_iso_suffix():
    for value in ("2026-07-01junk", "20260701", "2026-W27-3"):
        try:
            load_config_text(f"period_start: {value}\nperiod_end: 2026-09-30\n")
        except SystemExit as exc:
            assert "period_start" in str(exc)
        else:
            raise AssertionError(f"non-ISO period_start={value} must fail")


def test_default_period_is_current_quarter():
    import jira_analytics
    assert jira_analytics.default_period(date(2026, 8, 22)) == (date(2026, 7, 1), date(2026, 9, 30))
    assert jira_analytics.default_period(date(2026, 12, 1)) == (date(2026, 10, 1), date(2026, 12, 31))


def test_enabled_analytics_allows_initial_empty_period_and_boards():
    cfg = load_config_text(
        """
analytics_enabled: true
period_start: null
period_end: null
boards: []
"""
    )
    assert cfg.analytics_enabled is True
    assert cfg.period_start is None
    assert cfg.period_end is None
    assert cfg.boards == []


def test_period_config_rejects_incomplete_pair():
    try:
        load_config_text("analytics_enabled: false\nperiod_start: 2026-04-01\nperiod_end: null\n")
    except SystemExit as exc:
        assert "period_start" in str(exc)
        assert "period_end" in str(exc)
    else:
        raise AssertionError("one period boundary must fail")


def test_yaml_example_contains_all_config_fields():
    example = Path("jira-analytics.yaml.example")
    cfg = load_config_text(example.read_text(encoding="utf-8"))
    assert cfg.analytics_enabled is True
    assert cfg.include_active_sprints is True
    assert cfg.changelog_cache_ttl_hours == 24
    assert cfg.forgotten_age_days == 30
    assert cfg.show_external_assignees is False
    assert cfg.parallel_workers == 3
    assert cfg.story_points_field == "Story points"


def test_analytics_disabled_config():
    cfg = load_config_text("analytics_enabled: false\n")
    assert cfg.analytics_enabled is False
    assert cfg.period_start is None
    assert cfg.boards == []
    assert cfg.story_points_field == "Story points"
    assert cfg.done_statuses == DEFAULT_DONE_STATUSES
    assert "в работе" in cfg.active_statuses
    assert "Нужна информация" in cfg.waiting_statuses


def test_period_config_validation():
    try:
        load_config_text(
            """
analytics_enabled: true
period_start: 2026-07-01
period_end: 2026-06-30
boards:
  - id: 101
    name: Team A
"""
        )
    except SystemExit as exc:
        assert "period_end" in str(exc)
    else:
        raise AssertionError("period_end < period_start must fail")


def test_period_config_allows_empty_boards_when_enabled():
    cfg = load_config_text(
        """
analytics_enabled: true
period_start: 2026-04-01
period_end: 2026-06-30
boards: []
"""
    )
    assert cfg.analytics_enabled is True
    assert cfg.boards == []


def test_save_config_preserves_period_fields(tmp_path):
    path = tmp_path / "config.yaml"
    cfg = load_config_text(
        """
analytics_enabled: true
period_start: 2026-04-01
period_end: 2026-06-30
boards:
  - id: 101
    name: Team A
story_points_field: Points
done_statuses:
  - done
  - Closed
"""
    )

    save_config(path, cfg)

    saved = load_config_text(path.read_text(encoding="utf-8"))
    assert saved.analytics_enabled is True
    assert saved.boards[0].name == "Team A"
    assert saved.story_points_field == "Points"
    assert saved.done_statuses == ["done", "Closed"]


def test_load_config_per_board_statuses(tmp_path):
    cfg = load_config_text(
        """
analytics_enabled: true
period_start: 2026-04-01
period_end: 2026-06-30
boards:
  - id: 101
    name: Team A
    done_statuses:
      - "Код написан"
    active_statuses:
      - "In Progress"
  - id: 202
    name: Team B
done_statuses:
  - done
"""
    )
    assert cfg.boards[0].done_statuses == ["Код написан"]
    assert cfg.boards[0].active_statuses == ["In Progress"]
    assert cfg.boards[0].waiting_statuses is None
    assert cfg.boards[1].done_statuses is None
    assert cfg.boards[1].active_statuses is None
    assert cfg.boards[1].waiting_statuses is None

    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    saved = load_config_text(path.read_text(encoding="utf-8"))
    assert saved.boards[0].done_statuses == ["Код написан"]
    assert saved.boards[0].active_statuses == ["In Progress"]
    assert saved.boards[0].waiting_statuses is None
    assert saved.boards[1].done_statuses is None
    assert saved.done_statuses == ["done"]


def test_board_statuses_empty_list_preserved(tmp_path):
    cfg = load_config_text(
        """
analytics_enabled: true
period_start: 2026-04-01
period_end: 2026-06-30
boards:
  - id: 101
    name: Team A
    done_statuses: []
"""
    )
    assert cfg.boards[0].done_statuses == []

    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    saved = load_config_text(path.read_text(encoding="utf-8"))
    assert saved.boards[0].done_statuses == []


def test_resolve_board_statuses_override_and_fallback():
    import jira_period_analytics as jpa
    boards = [
        jpa.BoardConfig(101, "Team A", done_statuses=["Готово"]),
        jpa.BoardConfig(202, "Team B"),
        jpa.BoardConfig(303, "Team C", done_statuses=[]),
    ]
    resolved = jpa.resolve_board_statuses(boards, ["done"], ["active"], ["waiting"])
    assert resolved["Team A"].done == ["Готово"]
    assert resolved["Team A"].active == ["active"]
    assert resolved["Team A"].waiting == ["waiting"]
    assert resolved["Team B"].done == ["done"]
    assert resolved["Team B"].active == ["active"]
    assert resolved["Team C"].done == []


def test_classify_status_groups_and_exact_name():
    import jira_period_analytics as jpa

    statuses = jpa.BoardStatuses(["done"], ["в работе"], ["Новая"])
    assert jpa.classify_status(
        {"name": "Закрыта", "statusCategory": {"key": "done"}}, statuses
    ) == jpa.StatusClassification("Закрыта", "done")
    assert jpa.classify_status({"name": "В работе"}, statuses) == jpa.StatusClassification("В работе", "active")
    assert jpa.classify_status({"name": "новая"}, statuses) == jpa.StatusClassification("новая", "waiting")
    assert jpa.classify_status({"name": "Review"}, statuses) == jpa.StatusClassification("Review", "unknown")
    assert jpa.classify_status({}, statuses) == jpa.StatusClassification("", "unknown")

    ambiguous = jpa.BoardStatuses([], ["Review"], ["review"])
    assert jpa.classify_status({"name": "Review"}, ambiguous).category == "unknown"


def test_build_period_report_uses_per_board_done_statuses():
    import jira_period_analytics as jpa
    s_a = SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "new", status_name="Код написан")])
    s_b = SprintIssues("Team B", 2, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("B-1", "new", status_name="Код написан")])
    board_statuses = {"Team A": jpa.BoardStatuses(["Код написан"], ["In Progress"], ["To Do"])}

    report = build_period_report([s_a, s_b], "customfield_1", ["done"], board_statuses=board_statuses)

    by_name = {s.board_name: s for s in report.sprints}
    assert by_name["Team A"].done_issues == 1
    assert by_name["Team B"].done_issues == 0


def test_config_signature_includes_board_statuses():
    import jira_analytics
    env = {"JIRA_URL": "https://jira.example.org"}
    cfg_a = Config(analytics_enabled=True, period_start=date(2026, 4, 1), period_end=date(2026, 6, 30),
                   boards=[jira_analytics.BoardConfig(101, "A")])
    cfg_b = Config(analytics_enabled=True, period_start=date(2026, 4, 1), period_end=date(2026, 6, 30),
                   boards=[jira_analytics.BoardConfig(101, "A", done_statuses=["Код написан"])])
    assert jira_analytics._config_signature(env, cfg_a) != jira_analytics._config_signature(env, cfg_b)
    assert jira_analytics._config_signature({**env, "JIRA_USERNAME": "alice"}, cfg_a) != jira_analytics._config_signature({**env, "JIRA_USERNAME": "bob"}, cfg_a)


def test_read_write_include_active_sprints(tmp_path):
    cfg = load_config_text("include_active_sprints: true\n")
    assert cfg.include_active_sprints is True

    cfg.include_active_sprints = False
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    saved = load_config_text(path.read_text(encoding="utf-8"))
    assert saved.include_active_sprints is False


def test_include_active_sprints_rejects_string():
    try:
        load_config_text('include_active_sprints: "false"\n')
    except SystemExit as exc:
        assert "include_active_sprints" in str(exc)
        assert "boolean" in str(exc)
    else:
        raise AssertionError("string value must fail")


def test_save_env_and_env_values(tmp_path):
    path = tmp_path / "jira.env"

    save_env(path, {"JIRA_USERNAME": "user@example.org", "JIRA_TOKEN": "token"})

    assert env_values(path)["JIRA_USERNAME"] == "user@example.org"
    assert env_values(path)["JIRA_TOKEN"] == "token"
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_env_values_keeps_empty_values(tmp_path):
    path = tmp_path / "jira.env"
    path.write_text("JIRA_USERNAME=\nJIRA_TOKEN=\n", encoding="utf-8")

    values = env_values(path)

    assert values["JIRA_USERNAME"] == ""
    assert values["JIRA_TOKEN"] == ""


def test_ensure_user_files_copies_config_without_overwriting(tmp_path):
    import jira_analytics

    old_default_config = jira_analytics.DEFAULT_CONFIG
    old_default_env = jira_analytics.DEFAULT_ENV
    old_system_config = jira_analytics.SYSTEM_CONFIG
    old_old_default_config = jira_analytics.OLD_DEFAULT_CONFIG
    old_old_default_env = jira_analytics.OLD_DEFAULT_ENV
    old_old_system_config = jira_analytics.OLD_SYSTEM_CONFIG
    root = tmp_path / "fallback"
    jira_analytics.DEFAULT_CONFIG = root / "user" / "jira-analytics.yaml"
    jira_analytics.DEFAULT_ENV = root / "user" / "jira-analytics.env"
    jira_analytics.SYSTEM_CONFIG = tmp_path / "etc" / "jira-analytics.yaml"
    jira_analytics.OLD_DEFAULT_CONFIG = tmp_path / "missing-old.yaml"
    jira_analytics.OLD_DEFAULT_ENV = tmp_path / "missing-old.env"
    jira_analytics.OLD_SYSTEM_CONFIG = tmp_path / "missing-system-old.yaml"
    jira_analytics.SYSTEM_CONFIG.parent.mkdir(parents=True)
    jira_analytics.SYSTEM_CONFIG.write_text("items:\n  - AL-1\n", encoding="utf-8")
    try:
        ensure_user_files()
        jira_analytics.DEFAULT_CONFIG.write_text("items:\n  - AL-2\n", encoding="utf-8")
        ensure_user_files()
        assert jira_analytics.DEFAULT_CONFIG.read_text(encoding="utf-8") == "items:\n  - AL-2\n"
        assert env_values(jira_analytics.DEFAULT_ENV)["JIRA_URL"] == ""
        assert oct(jira_analytics.DEFAULT_ENV.stat().st_mode & 0o777) == "0o600"
    finally:
        jira_analytics.DEFAULT_CONFIG = old_default_config
        jira_analytics.DEFAULT_ENV = old_default_env
        jira_analytics.SYSTEM_CONFIG = old_system_config
        jira_analytics.OLD_DEFAULT_CONFIG = old_old_default_config
        jira_analytics.OLD_DEFAULT_ENV = old_old_default_env
        jira_analytics.OLD_SYSTEM_CONFIG = old_old_system_config


def test_ensure_user_files_falls_back_when_system_env_unreadable(tmp_path):
    import jira_analytics

    old_default_config = jira_analytics.DEFAULT_CONFIG
    old_default_env = jira_analytics.DEFAULT_ENV
    old_system_config = jira_analytics.SYSTEM_CONFIG
    old_old_default_config = jira_analytics.OLD_DEFAULT_CONFIG
    old_old_default_env = jira_analytics.OLD_DEFAULT_ENV
    old_old_system_config = jira_analytics.OLD_SYSTEM_CONFIG
    jira_analytics.DEFAULT_CONFIG = tmp_path / "user" / "jira-analytics.yaml"
    jira_analytics.DEFAULT_ENV = tmp_path / "user" / "jira-analytics.env"
    jira_analytics.SYSTEM_CONFIG = tmp_path / "missing.yaml"
    jira_analytics.OLD_DEFAULT_CONFIG = tmp_path / "missing-old.yaml"
    jira_analytics.OLD_DEFAULT_ENV = tmp_path / "missing-old.env"
    jira_analytics.OLD_SYSTEM_CONFIG = tmp_path / "missing-system-old.yaml"
    try:
        ensure_user_files()
        values = env_values(jira_analytics.DEFAULT_ENV)
        assert values["JIRA_USERNAME"] == ""
        assert values["JIRA_TOKEN"] == ""
        assert oct(jira_analytics.DEFAULT_ENV.stat().st_mode & 0o777) == "0o600"
    finally:
        jira_analytics.DEFAULT_CONFIG = old_default_config
        jira_analytics.DEFAULT_ENV = old_default_env
        jira_analytics.SYSTEM_CONFIG = old_system_config
        jira_analytics.OLD_DEFAULT_CONFIG = old_old_default_config
        jira_analytics.OLD_DEFAULT_ENV = old_old_default_env
        jira_analytics.OLD_SYSTEM_CONFIG = old_old_system_config


def test_collect_alert_result_deduplicates_by_key(monkeypatch=None):
    import jira_analytics

    issue = {
        "key": "AL-5948",
        "fields": {
            "summary": "Task",
            "status": {"name": "In Progress"},
            "customfield_1": "2026-07-01",
        },
    }
    old_jira_client = jira_analytics.jira_client
    old_field_id = jira_analytics.field_id
    old_get_issue = jira_analytics.get_issue
    old_search_issues = jira_analytics.search_issues
    old_date = jira_analytics.date

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 1)

    jira_analytics.jira_client = lambda env, timeout=None: object()
    jira_analytics.field_id = lambda jira, name: "customfield_1"
    jira_analytics.get_issue = lambda jira, key, fields: issue
    jira_analytics.search_issues = lambda jira, jql, fields: [issue]
    jira_analytics.date = FixedDate
    try:
        alerts = collect_alert_result(TEST_ENV, Config(items=["AL-5948", "project = AL"])).alerts
    finally:
        jira_analytics.jira_client = old_jira_client
        jira_analytics.field_id = old_field_id
        jira_analytics.get_issue = old_get_issue
        jira_analytics.search_issues = old_search_issues
        jira_analytics.date = old_date

    assert [alert.key for alert in alerts] == ["AL-5948"]


def test_collect_alert_result_sorts_by_days():
    import jira_analytics

    issues = {
        "AL-1": {
            "key": "AL-1",
            "fields": {"summary": "Soon", "status": {"name": "Open"}, "customfield_1": "2026-07-03"},
        },
        "AL-2": {
            "key": "AL-2",
            "fields": {"summary": "Overdue", "status": {"name": "Open"}, "customfield_1": "2026-06-30"},
        },
    }
    old_jira_client = jira_analytics.jira_client
    old_field_id = jira_analytics.field_id
    old_get_issue = jira_analytics.get_issue
    old_date = jira_analytics.date

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 1)

    jira_analytics.jira_client = lambda env, timeout=None: object()
    jira_analytics.field_id = lambda jira, name: "customfield_1"
    jira_analytics.get_issue = lambda jira, key, fields: issues[key]
    jira_analytics.date = FixedDate
    try:
        alerts = collect_alert_result(TEST_ENV, Config(items=["AL-1", "AL-2"])).alerts
    finally:
        jira_analytics.jira_client = old_jira_client
        jira_analytics.field_id = old_field_id
        jira_analytics.get_issue = old_get_issue
        jira_analytics.date = old_date

    assert [alert.key for alert in alerts] == ["AL-2", "AL-1"]


def test_collect_alert_result_keeps_source_errors():
    import jira_analytics

    old_jira_client = jira_analytics.jira_client
    old_field_id = jira_analytics.field_id
    old_get_issue = jira_analytics.get_issue
    jira_analytics.jira_client = lambda env, timeout=None: object()
    jira_analytics.field_id = lambda jira, name: "customfield_1"
    jira_analytics.get_issue = lambda jira, key, fields: (_ for _ in ()).throw(RuntimeError("not found"))
    try:
        result = collect_alert_result(TEST_ENV, Config(items=["AL-404"]))
    finally:
        jira_analytics.jira_client = old_jira_client
        jira_analytics.field_id = old_field_id
        jira_analytics.get_issue = old_get_issue

    assert result.alerts == []
    assert "AL-404" in result.errors[0]
    assert "not found" in result.errors[0]


def test_collect_alert_result_uses_default_items_when_empty():
    import jira_analytics

    seen = []
    old_jira_client = jira_analytics.jira_client
    old_field_id = jira_analytics.field_id
    old_search_issues = jira_analytics.search_issues
    jira_analytics.jira_client = lambda env, timeout=None: object()
    jira_analytics.field_id = lambda jira, name: "customfield_1"
    jira_analytics.search_issues = lambda jira, jql, fields: seen.append(jql) or []
    try:
        collect_alert_result(TEST_ENV, Config(items=[]))
    finally:
        jira_analytics.jira_client = old_jira_client
        jira_analytics.field_id = old_field_id
        jira_analytics.search_issues = old_search_issues

    assert seen == DEFAULT_ITEMS


def test_collect_period_result_uses_agile_api_with_partial_failure():
    import jira_analytics

    class FakeJira:
        def get_all_fields(self):
            return [{"name": "Story points", "id": "customfield_1"}]

        def get_all_sprints_from_board(self, board_id, state, start=0, limit=50):
            if board_id == 202:
                raise RuntimeError("board denied")
            return {
                "values": [
                    {"id": 1, "name": "Sprint 1", "startDate": "2026-04-01T00:00:00.000+0000", "endDate": "2026-04-14T00:00:00.000+0000"},
                    {"id": 2, "name": "Sprint old", "startDate": "2026-01-01", "endDate": "2026-01-14"},
                ],
                "isLast": True,
            }

        def get_all_issues_for_sprint_in_board(self, board_id, sprint_id, fields, start=0, limit=100):
            assert isinstance(fields, str)
            assert "status" in fields.split(",")
            assert "customfield_1" in fields.split(",")
            return {"issues": [issue("A-1", "done", 3), issue("A-2", "new", None)], "isLast": True}

    old_jira_client = jira_analytics.jira_client
    jira_analytics.jira_client = lambda env, timeout=None: FakeJira()
    try:
        report = collect_period_result(
            TEST_ENV,
            Config(
                analytics_enabled=True,
                period_start=date(2026, 4, 1),
                period_end=date(2026, 6, 30),
                boards=[jira_analytics.BoardConfig(101, "Team A"), jira_analytics.BoardConfig(202, "Team B")],
            ),
        )
    finally:
        jira_analytics.jira_client = old_jira_client

    assert report.portfolio.sprints_count == 1
    assert report.portfolio.done_points == 3
    assert report.portfolio.not_done_count == 1
    assert report.portfolio.spillover_count == 0
    assert "Team B" in report.errors[0]


def test_partial_board_failure_makes_cross_board_routing_unknown():
    import jira_analytics

    class FakeJira:
        def get_all_fields(self):
            return [{"name": "Story points", "id": "customfield_1"}]

        def get_all_sprints_from_board(self, board_id, state, start=0, limit=50):
            if board_id == 202:
                raise RuntimeError("board denied")
            return {"values": [{
                "id": 1, "name": "S1", "state": "closed",
                "startDate": "2026-04-01", "endDate": "2026-04-14", "completeDate": "2026-04-14",
            }], "isLast": True}

        def get_all_issues_for_sprint_in_board(self, board_id, sprint_id, fields, start=0, limit=100):
            return {"issues": [issue("A-1", "new", status_name="Новая")], "isLast": True}

        def jql(self, jql, fields, start=0, limit=100):
            return {"issues": [], "total": 0}

    config = Config(
        analytics_enabled=True, period_start=date(2026, 4, 1), period_end=date(2026, 6, 30),
        boards=[
            jira_analytics.BoardConfig(101, "Team A", backlog_jql="project = A"),
            jira_analytics.BoardConfig(202, "Team B", backlog_jql="project = B"),
        ],
    )
    with patch("jira_analytics.jira_client", return_value=FakeJira()), \
         patch("jira_analytics.load_period_source_cache", return_value=None), \
         patch("jira_analytics.save_period_source_cache"), \
         patch("jira_analytics.fetch_issue_changelog", return_value=[]):
        report = collect_period_result(TEST_ENV, config)

    assert report.sprints[0].board_name == "Team A"
    assert report.sprints[0].not_observed_count == 0
    assert report.sprints[0].unknown_count == 1


def test_collect_period_result_respects_include_active_sprints():
    import jira_analytics

    state_seen: list[str] = []

    class FakeJira:
        def get_all_fields(self):
            return [{"name": "Story points", "id": "customfield_1"}]

        def get_all_sprints_from_board(self, board_id, state, start=0, limit=50):
            state_seen.append(state)
            return {"values": [], "isLast": True}

        def get_all_issues_for_sprint_in_board(self, board_id, sprint_id, fields, start=0, limit=100):
            return {"issues": [], "isLast": True}

    old_jira_client = jira_analytics.jira_client
    jira_analytics.jira_client = lambda env, timeout=None: FakeJira()
    try:
        collect_period_result(
            TEST_ENV,
            Config(
                analytics_enabled=True,
                period_start=date(2026, 4, 1),
                period_end=date(2026, 6, 30),
                boards=[jira_analytics.BoardConfig(101, "Team A")],
                include_active_sprints=True,
            ),
        )
    finally:
        jira_analytics.jira_client = old_jira_client

    assert state_seen[0] == "active,closed,future"
    state_seen.clear()

    jira_analytics.jira_client = lambda env, timeout=None: FakeJira()
    try:
        collect_period_result(
            TEST_ENV,
            Config(
                analytics_enabled=True,
                period_start=date(2026, 4, 1),
                period_end=date(2026, 6, 30),
                boards=[jira_analytics.BoardConfig(101, "Team A")],
                include_active_sprints=False,
            ),
        )
    finally:
        jira_analytics.jira_client = old_jira_client

    assert state_seen[0] == "closed"


def test_future_sprint_is_routing_only_and_not_reported():
    import jira_analytics

    class FakeJira:
        def get_all_fields(self):
            return [{"name": "Story points", "id": "customfield_1"}]

        def get_all_sprints_from_board(self, board_id, state, start=0, limit=50):
            return {"values": [
                {"id": 1, "name": "Closed", "state": "closed", "startDate": "2026-04-01", "endDate": "2026-04-14", "completeDate": "2026-04-14"},
                {"id": 2, "name": "Future", "state": "future", "startDate": "2026-07-01", "endDate": "2026-07-14"},
            ], "isLast": True}

        def get_all_issues_for_sprint_in_board(self, board_id, sprint_id, fields, start=0, limit=100):
            return {"issues": [issue("A-1", "new", status_name="Новая")], "isLast": True}

        def jql(self, jql, fields, start=0, limit=100):
            return {"issues": [], "total": 0}

    config = Config(
        analytics_enabled=True, period_start=date(2026, 4, 1), period_end=date(2026, 6, 30),
        boards=[jira_analytics.BoardConfig(101, "Team A", backlog_jql="project = A")],
    )
    with patch("jira_analytics.jira_client", return_value=FakeJira()), \
         patch("jira_analytics.load_period_source_cache", return_value=None), \
         patch("jira_analytics.save_period_source_cache"), \
         patch("jira_analytics.fetch_issue_changelog", return_value=[]):
        report = collect_period_result(TEST_ENV, config)

    assert report.portfolio.sprints_count == 1
    assert [s.sprint_name for s in report.sprints] == ["Closed"]
    assert report.sprints[0].spillover_count == 1


def test_dump_sanitizes_by_default_and_raw_flag_keeps_raw(tmp_path):
    import jira_analytics

    def dump_issue(key):
        return {
            "key": key,
            "self": "https://jira.example.org/rest/api/2/issue/1",
            "fields": {
                "summary": "Секретный заголовок",
                "description": "Секретное описание",
                "status": {"name": "Done", "statusCategory": {"key": "done"}},
                "customfield_1": 3,
                "assignee": {"displayName": "Ivan", "name": "ivan", "self": "https://jira.example.org/rest/api/2/user?username=ivan"},
            },
        }

    class FakeJira:
        def get_all_fields(self):
            return [{"name": "Story points", "id": "customfield_1"}]

        def get_all_sprints_from_board(self, board_id, state, start=0, limit=50):
            return {"values": [{
                "id": 1, "name": "S1", "state": "closed",
                "startDate": "2026-04-01", "endDate": "2026-04-14", "completeDate": "2026-04-14",
            }], "isLast": True}

        def get_all_issues_for_sprint_in_board(self, board_id, sprint_id, fields, start=0, limit=100):
            return {"issues": [dump_issue("A-1")], "isLast": True}

    config = Config(
        analytics_enabled=True, period_start=date(2026, 4, 1), period_end=date(2026, 6, 30),
        boards=[jira_analytics.BoardConfig(101, "Team A")],
    )
    clean = tmp_path / "clean.json"
    raw = tmp_path / "raw.json"
    with patch("jira_analytics.jira_client", return_value=FakeJira()), \
         patch("jira_analytics.load_period_source_cache", return_value=None), \
         patch("jira_analytics.save_period_source_cache"), \
         patch("jira_analytics.fetch_issue_changelog", return_value=[]):
        collect_period_result(TEST_ENV, config, dump_path=str(clean))
        collect_period_result(TEST_ENV, config, dump_path=str(raw), raw=True)

    clean_data = json.loads(clean.read_text(encoding="utf-8"))
    raw_data = json.loads(raw.read_text(encoding="utf-8"))
    ci = clean_data["sprints"][0]["issues"][0]
    ri = raw_data["sprints"][0]["issues"][0]
    assert ci["key"] == "A-1"
    assert ci["fields"]["summary"] == "[REDACTED]"
    assert ci["fields"]["description"] == "[REDACTED]"
    assert ci["self"] == "[URL-REDACTED]"
    assert ci["fields"]["assignee"]["self"] == "[URL-REDACTED]"
    assert ci["fields"]["assignee"]["displayName"] == "Ivan"
    assert ri["fields"]["summary"] == "Секретный заголовок"
    assert ri["self"] == "https://jira.example.org/rest/api/2/issue/1"


def test_compute_spillover_detects_carryover():
    s1 = SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "new"), issue("A-2", "done", 3)])
    s2 = SprintIssues("Team A", 2, "S2", date(2026, 4, 15), date(2026, 4, 28), datetime(2026, 4, 28), [issue("A-1", "new"), issue("A-3", "done", 5)])
    result = compute_spillover([s1, s2], set(), ["done"])

    assert result[1]["spillover"] == 1
    assert result[1]["spillover_completed"] == 0
    assert result[1]["backlog"] == 0
    assert result[1]["reassigned"] == 0
    assert result[2]["spillover"] == 0


def test_compute_spillover_completed():
    s1 = SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "new")])
    s2 = SprintIssues("Team A", 2, "S2", date(2026, 4, 15), date(2026, 4, 28), datetime(2026, 4, 28), [issue("A-1", "done", 3)])
    result = compute_spillover([s1, s2], set(), ["done"])

    assert result[1]["spillover"] == 1
    assert result[1]["spillover_completed"] == 1


def test_compute_spillover_reassigned():
    s1 = SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "new")])
    s2 = SprintIssues("Team A", 2, "S2", date(2026, 4, 15), date(2026, 4, 28), datetime(2026, 4, 28), [issue("A-2", "done", 3)])
    result = compute_spillover([s1, s2], set(), ["done"])

    assert result[1]["spillover"] == 0
    assert result[1]["reassigned"] == 1


def test_compute_spillover_backlog():
    s1 = SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "new")])
    s2 = SprintIssues("Team A", 2, "S2", date(2026, 4, 15), date(2026, 4, 28), datetime(2026, 4, 28), [issue("A-2", "done", 3)])
    result = compute_spillover([s1, s2], {"A-1"}, ["done"])

    assert result[1]["spillover"] == 0
    assert result[1]["backlog"] == 1
    assert result[1]["reassigned"] == 0


def test_build_period_report_routes_current_active_with_exact_status():
    import jira_period_analytics as jpa

    sprint = SprintIssues(
        "Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14),
        [issue("A-1", "new", status_name="Новая")],
    )
    current = {"Team A": {"A-1": jpa.StatusClassification("В работе", "active")}}
    report = build_period_report(
        [sprint], "customfield_1", ["done"], scope_statuses=current,
        classification_complete_boards={"Team A"},
    )

    assert report.sprints[0].active_count == 1
    assert report.sprints[0].backlog_count == 0
    assert report.boards[0].active_count == 1
    assert report.portfolio.active_count == 1
    assert report.assignees[0].active == 1
    assert report.assignees[0].reassigned == 0
    item = report.assignees[0].items[0]
    assert item.route_category == "active"
    assert item.current_status == "В работе"
    assert item.current_status_category == "active"
    serializer = type("Serializer", (), {
        "_board_id_by_name": {},
        "format_number": staticmethod(Backend.format_number),
        "format_percent": staticmethod(Backend.format_percent),
    })()
    assert Backend.sprint_item(serializer, report.sprints[0])["active"] == 1
    assert Backend.board_item(serializer, report.boards[0])["active"] == 1
    assignee_item = Backend.assignee_item(serializer, report.assignees[0])
    assert assignee_item["active"] == 1
    assert assignee_item["items"][0]["currentStatus"] == "В работе"
    assert assignee_item["items"][0]["currentStatusCategory"] == "active"
    assert build_chart_slots(report.sprints, period_fn=period_label_for_sprint)[0]["active"] == 1


def test_compute_spillover_observes_other_board_and_keeps_legacy_reassigned():
    s1 = SprintIssues("Team A", 1, "A1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "new")])
    s2 = SprintIssues("Team B", 2, "B1", date(2026, 4, 15), date(2026, 4, 28), datetime(2026, 4, 28), [issue("A-1", "new")])
    result = compute_spillover(
        [s1], set(), ["done"], all_sprints=[s1, s2], classification_complete=True
    )

    assert result[1]["observed_other_board"] == 1
    assert result[1]["not_observed"] == 0
    assert result[1]["unknown"] == 0
    assert result[1]["reassigned"] == 1


def test_compute_spillover_distinguishes_not_observed_from_unknown():
    import jira_period_analytics as jpa

    sprint = SprintIssues("Team A", 1, "A1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "new")])
    complete = compute_spillover([sprint], set(), ["done"], classification_complete=True)
    incomplete = compute_spillover([sprint], set(), ["done"], classification_complete=False)
    incomplete_with_backlog = compute_spillover([sprint], {"A-1"}, ["done"], classification_complete=False)
    incomplete_with_active = compute_spillover(
        [sprint], set(), ["done"], classification_complete=False,
        current_statuses={"A-1": jpa.StatusClassification("В работе", "active")},
    )
    unknown_status = compute_spillover(
        [sprint], set(), ["done"], classification_complete=True,
        current_statuses={"A-1": jpa.StatusClassification("Review", "unknown")},
    )

    assert complete[1]["not_observed"] == 1
    assert complete[1]["unknown"] == 0
    assert incomplete[1]["not_observed"] == 0
    assert incomplete[1]["unknown"] == 1
    assert incomplete_with_backlog[1]["backlog"] == 0
    assert incomplete_with_backlog[1]["unknown"] == 1
    assert incomplete_with_active[1]["active"] == 0
    assert incomplete_with_active[1]["unknown"] == 1
    assert unknown_status[1]["unknown"] == 1
    assert complete[1]["reassigned"] == incomplete[1]["reassigned"] == 1


def test_active_sprint_routing_is_unknown_even_when_task_is_observed_later():
    active = SprintIssues("Team A", 1, "Active", date(2026, 4, 1), date(2026, 4, 14), None, [issue("A-1", "new")])
    future = SprintIssues("Team A", 2, "Future", date(2026, 4, 15), date(2026, 4, 28), None, [issue("A-1", "new")])
    result = compute_spillover([active], set(), ["done"], all_sprints=[active, future])

    assert result[1]["spillover"] == 0
    assert result[1]["unknown"] == 1
    assert result[1]["reassigned"] == 1


def test_future_sprint_can_start_on_origin_end_date():
    import jira_period_analytics as jpa

    source = SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "new")])
    future = SprintIssues("Team A", 2, "S2", date(2026, 4, 14), date(2026, 4, 28), datetime(2026, 4, 28), [issue("A-1", "new")])
    result = compute_spillover(
        [source, future], set(), ["done"],
        current_statuses={"A-1": jpa.StatusClassification("В работе", "active")},
    )

    assert result[1]["spillover"] == 1
    assert result[1]["active"] == 0


def test_completed_later_is_outcome_and_survives_reopening():
    sprint = SprintIssues("Team A", 1, "A1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "new", assignee="Ivan")])
    changelog = {
        "A-1": [
            {"created": "2026-04-20T10:00:00", "items": [{"field": "status", "fromString": "In Progress", "toString": "Done"}]},
            {"created": "2026-04-22T10:00:00", "items": [{"field": "status", "fromString": "Done", "toString": "In Progress"}]},
        ]
    }
    report = build_period_report(
        [sprint], "customfield_1", ["done"], status_changelogs=changelog,
        classification_complete_boards={"Team A"},
    )

    assert report.sprints[0].not_observed_count == 1
    assert report.sprints[0].completed_later_count == 1
    assert report.sprints[0].reassigned_count == 1
    assert report.boards[0].completed_later_count == 1
    assert report.portfolio.completed_later_count == 1
    item = report.assignees[0].items[0]
    assert item.category == "reassigned"
    assert item.route_category == "not_observed"
    assert item.completed_later is True
    serializer = type("Serializer", (), {
        "_board_id_by_name": {},
        "format_number": staticmethod(Backend.format_number),
        "format_percent": staticmethod(Backend.format_percent),
    })()
    sprint_item = Backend.sprint_item(serializer, report.sprints[0])
    board_item = Backend.board_item(serializer, report.boards[0])
    assignee_item = Backend.assignee_item(serializer, report.assignees[0])
    slot = build_chart_slots(report.sprints, period_fn=period_label_for_sprint)[0]
    assert sprint_item["completedLater"] == board_item["completedLater"] == 1
    assert sprint_item["notObserved"] == board_item["notObserved"] == 1
    assert assignee_item["completedLater"] == 1
    assert assignee_item["items"][0]["routeCategory"] == "not_observed"
    assert slot["completedLater"] == 1
    assert slot["notObserved"] == 1


def test_collect_period_filters_current_done_from_broad_backlog_jql():
    import jira_analytics

    done_issue = issue("A-1", "done", 3, "Ivan", status_name="Done")
    done_issue["fields"].update({"created": "2026-04-01", "updated": "2026-04-20", "summary": "Late done"})
    changelog = [{
        "created": "2026-04-20T10:00:00",
        "items": [{"field": "status", "fromString": "In Progress", "toString": "Done"}],
    }]

    class FakeJira:
        def get_all_fields(self):
            return [{"name": "Story points", "id": "customfield_1"}]

        def get_all_sprints_from_board(self, board_id, state, start=0, limit=50):
            return {"values": [{
                "id": 1, "name": "S1", "state": "closed",
                "startDate": "2026-04-01", "endDate": "2026-04-14", "completeDate": "2026-04-14",
            }], "isLast": True}

        def get_all_issues_for_sprint_in_board(self, board_id, sprint_id, fields, start=0, limit=100):
            return {"issues": [done_issue], "isLast": True}

        def jql(self, jql, fields, start=0, limit=100):
            return {"issues": [done_issue], "total": 1}

    config = Config(
        analytics_enabled=True,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 6, 30),
        boards=[jira_analytics.BoardConfig(101, "Team A", backlog_jql="project = A")],
        done_statuses=["Done"],
    )
    with patch("jira_analytics.jira_client", return_value=FakeJira()), \
         patch("jira_analytics.load_period_source_cache", return_value=None), \
         patch("jira_analytics.save_period_source_cache"), \
         patch("jira_analytics.fetch_issue_changelog", return_value=changelog):
        report = collect_period_result(TEST_ENV, config)

    assert report.sprints[0].backlog_count == 0
    assert report.sprints[0].not_observed_count == 1
    assert report.sprints[0].completed_later_count == 1


def test_collect_period_builds_team_scope_and_waiting_only_backlog_health():
    import jira_analytics

    def scope_issue(key, status_name, status_key):
        item = issue(key, status_key, status_name=status_name)
        item["fields"].update({
            "created": "2026-04-01", "updated": "2026-04-20", "summary": key,
        })
        return item

    issues = [
        scope_issue("A-DONE", "Закрыта", "done"),
        scope_issue("A-ACTIVE", "В работе", "indeterminate"),
        scope_issue("A-WAIT", "Новая", "new"),
        scope_issue("A-UNKNOWN", "Review", "indeterminate"),
    ]

    class FakeJira:
        def get_all_fields(self):
            return [{"name": "Story points", "id": "customfield_1"}]

        def get_all_sprints_from_board(self, board_id, state, start=0, limit=50):
            return {"values": [{
                "id": 1, "name": "S1", "state": "closed",
                "startDate": "2026-04-01", "endDate": "2026-04-14", "completeDate": "2026-04-14",
            }], "isLast": True}

        def get_all_issues_for_sprint_in_board(self, board_id, sprint_id, fields, start=0, limit=100):
            return {"issues": [issues[2]], "isLast": True}

        def jql(self, jql, fields, start=0, limit=100):
            return {"issues": issues, "total": len(issues)}

    config = Config(
        analytics_enabled=True,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 6, 30),
        boards=[jira_analytics.BoardConfig(101, "Team A", backlog_jql="project = A")],
        done_statuses=["done"], active_statuses=["в работе"], waiting_statuses=["Новая"],
    )
    with patch("jira_analytics.jira_client", return_value=FakeJira()), \
         patch("jira_analytics.load_period_source_cache", return_value=None), \
         patch("jira_analytics.save_period_source_cache"), \
         patch("jira_analytics.fetch_issue_changelog", return_value=[]):
        report = collect_period_result(TEST_ENV, config)

    scope = report.team_scope[0]
    assert (scope.total, scope.done, scope.active, scope.waiting, scope.unknown) == (4, 1, 1, 1, 1)
    assert {item.key: (item.status, item.status_category) for item in scope.items} == {
        "A-DONE": ("Закрыта", "done"),
        "A-ACTIVE": ("В работе", "active"),
        "A-WAIT": ("Новая", "waiting"),
        "A-UNKNOWN": ("Review", "unknown"),
    }
    assert report.backlog_health[0].total_open == 1
    assert [item.key for item in report.backlog_health[0].items] == ["A-WAIT"]
    assert report.sprints[0].backlog_count == 1
    assert report.sprints[0].active_count == 0


def test_collect_period_missing_sprint_dates_makes_routing_unknown():
    import jira_analytics

    class FakeJira:
        def get_all_fields(self):
            return [{"name": "Story points", "id": "customfield_1"}]

        def get_all_sprints_from_board(self, board_id, state, start=0, limit=50):
            return {"values": [
                {"id": 1, "name": "S1", "state": "closed", "startDate": "2026-04-01", "endDate": "2026-04-14", "completeDate": "2026-04-14"},
                {"id": 2, "name": "Broken", "state": "closed", "startDate": None, "endDate": None},
            ], "isLast": True}

        def get_all_issues_for_sprint_in_board(self, board_id, sprint_id, fields, start=0, limit=100):
            return {"issues": [issue("A-1", "new")], "isLast": True}

        def jql(self, jql, fields, start=0, limit=100):
            return {"issues": [], "total": 0}

    config = Config(
        analytics_enabled=True, period_start=date(2026, 4, 1), period_end=date(2026, 6, 30),
        boards=[jira_analytics.BoardConfig(101, "Team A", backlog_jql="project = A")],
    )
    with patch("jira_analytics.jira_client", return_value=FakeJira()), \
         patch("jira_analytics.load_period_source_cache", return_value=None), \
         patch("jira_analytics.save_period_source_cache"), \
         patch("jira_analytics.fetch_issue_changelog", return_value=[]):
        report = collect_period_result(TEST_ENV, config)

    assert report.sprints[0].unknown_count == 1
    assert report.sprints[0].not_observed_count == 0


def test_load_config_backlog_jql(tmp_path):
    cfg = load_config_text(
        """
analytics_enabled: true
period_start: 2026-04-01
period_end: 2026-06-30
boards:
  - id: 101
    name: Team A
    backlog_jql: "project = AL AND Team = 'Team A' AND statusCategory != Done"
"""
    )
    assert cfg.boards[0].backlog_jql == "project = AL AND Team = 'Team A' AND statusCategory != Done"

    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    saved = load_config_text(path.read_text(encoding="utf-8"))
    assert saved.boards[0].backlog_jql == cfg.boards[0].backlog_jql


def test_load_config_board_without_backlog_jql():
    cfg = load_config_text(
        """
analytics_enabled: true
period_start: 2026-04-01
period_end: 2026-06-30
boards:
  - id: 101
    name: Team A
"""
    )
    assert cfg.boards[0].backlog_jql is None


def test_single_sprint_classifies_backlog():
    s1 = SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "new")])
    report = build_period_report([s1], None, ["done"], {"Team A": {"A-1"}})
    assert report.sprints[0].not_done_count == 1
    assert report.sprints[0].spillover_count == 0
    assert report.sprints[0].backlog_count == 1
    assert report.sprints[0].reassigned_count == 0


def test_load_config_backlog_jql_must_be_string():
    try:
        load_config_text(
            """
analytics_enabled: true
period_start: 2026-04-01
period_end: 2026-06-30
boards:
  - id: 101
    name: Team A
    backlog_jql: [1, 2, 3]
"""
        )
    except SystemExit as exc:
        assert "backlog_jql" in str(exc)
    else:
        raise AssertionError("non-string backlog_jql must fail")


def test_historical_resolve_not_done():
    changelog = [
        {
            "created": "2026-04-15T10:00:00.000+0000",
            "items": [{"field": "status", "fromString": "In Progress", "toString": "Done"}],
        },
    ]
    current = {"name": "Done", "statusCategory": {"key": "done"}}
    assert not resolve_historical_status(changelog, current, datetime(2026, 4, 10), ["done"])


def test_historical_resolve_done_before_cutoff():
    changelog = [
        {
            "created": "2026-04-10T10:00:00.000+0000",
            "items": [{"field": "status", "fromString": "In Progress", "toString": "Done"}],
        },
    ]
    current = {"name": "Done", "statusCategory": {"key": "done"}}
    assert resolve_historical_status(changelog, current, datetime(2026, 4, 15), ["done"])


def test_historical_done_in_sprint_summary():
    changelog = [
        {
            "created": "2026-04-20T10:00:00.000+0000",
            "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"}],
        },
    ]
    current = {"name": "In Progress", "statusCategory": {"key": "indeterminate"}}
    sprints = [
        SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "done", 5)]),
    ]
    hd: dict[tuple[str, int], bool] = {}
    hd[("A-1", 1)] = resolve_historical_status(changelog, current, datetime(2026, 4, 14), ["done"])
    report = build_period_report(sprints, "customfield_1", ["done"], historical_done=hd)
    assert report.sprints[0].done_issues == 0
    assert report.sprints[0].not_done_count == 1


def test_active_sprint_uses_current_status():
    sprints = [
        SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), None, [issue("A-1", "done", 5)]),
    ]
    report = build_period_report(sprints, "customfield_1", ["done"])
    assert report.sprints[0].done_issues == 1


def test_historical_spillover_from_historical_not_done():
    changelog = [
        {
            "created": "2026-05-01T10:00:00.000+0000",
            "items": [{"field": "status", "fromString": "To Do", "toString": "Done"}],
        },
    ]
    current = {"name": "Done", "statusCategory": {"key": "done"}}
    s1 = SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "done", 5)])
    s2 = SprintIssues("Team A", 2, "S2", date(2026, 4, 15), date(2026, 4, 28), datetime(2026, 4, 28), [issue("A-1", "done", 5)])
    hd: dict[tuple[str, int], bool] = {}
    hd[("A-1", 1)] = resolve_historical_status(changelog, current, datetime(2026, 4, 14), ["done"])
    hd[("A-1", 2)] = True
    report = build_period_report([s1, s2], "customfield_1", ["done"], historical_done=hd)
    assert report.sprints[0].not_done_count == 1
    assert report.sprints[0].spillover_count == 1
    assert report.sprints[0].spillover_completed == 1


def test_historical_regression_multiple_transitions():
    changelog = [
        {
            "created": "2026-04-15T12:00:00.000+0000",
            "items": [{"field": "status", "fromString": "In Progress", "toString": "Review"}],
        },
        {
            "created": "2026-04-15T14:00:00.000+0000",
            "items": [{"field": "status", "fromString": "Review", "toString": "Done"}],
        },
    ]
    current = {"name": "Done", "statusCategory": {"key": "done"}}
    assert not resolve_historical_status(changelog, current, datetime(2026, 4, 15, 10, 0), ["done"])


def test_historical_same_day_transition_after_cutoff():
    changelog = [
        {
            "created": "2026-04-15T15:00:00.000+0000",
            "items": [{"field": "status", "fromString": "In Progress", "toString": "Done"}],
        },
    ]
    current = {"name": "Done", "statusCategory": {"key": "done"}}
    assert not resolve_historical_status(changelog, current, datetime(2026, 4, 15, 10, 0), ["done"])


def test_aggregate_respects_historical_done():
    s1 = SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "done", 5)])
    hd: dict[tuple[str, int], bool] = {("A-1", 1): False}
    report = build_period_report([s1], "customfield_1", ["done"], historical_done=hd)
    assert report.sprints[0].done_issues == 0
    assert report.boards[0].done_issues == 0
    assert report.portfolio.done_issues == 0
    assert report.portfolio.done_points == 0.0


def test_aggregate_closed_false_active_done_counts_done():
    s_closed = SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "done", 5)])
    s_active = SprintIssues("Team A", 2, "S2", date(2026, 4, 15), date(2026, 4, 28), None, [issue("A-1", "done", 5)])
    hd: dict[tuple[str, int], bool] = {("A-1", 1): False}
    report = build_period_report([s_closed, s_active], "customfield_1", ["done"], historical_done=hd)
    assert report.sprints[0].done_issues == 0
    assert report.sprints[1].done_issues == 1
    assert report.boards[0].done_issues == 1
    assert report.boards[0].done_points == 5.0
    assert report.portfolio.done_issues == 1
    assert report.portfolio.done_points == 5.0


def test_aggregate_changed_story_points_dont_exceed_100_pct():
    s1 = SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "new", 3)])
    s2 = SprintIssues("Team A", 2, "S2", date(2026, 4, 15), date(2026, 4, 28), datetime(2026, 4, 28), [issue("A-1", "done", 8)])
    report = build_period_report([s1, s2], "customfield_1", ["done"])
    assert report.sprints[0].total_points == 3.0
    assert report.sprints[1].total_points == 8.0
    assert report.boards[0].total_points == 8.0
    assert report.boards[0].done_points == 8.0
    assert report.boards[0].completion_pct <= 100.0


def test_assignee_stats_basic():
    sprints = [
        SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "done", 3, "Ivan"), issue("A-2", "new", 5, "Ivan")]),
    ]
    report = build_period_report(sprints, "customfield_1", ["done"])
    assert len(report.assignees) == 1
    a = report.assignees[0]
    assert a.display_name == "Ivan"
    assert a.total_assigned == 2
    assert a.done == 1
    assert a.done_sp == 3.0
    assert a.total_sp == 8.0


def test_assignee_stats_uses_historical_assignee_for_closed_sprint():
    sprints = [
        SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "new", 5, "New Owner"), issue("A-2", "new", 1, "Old Owner")]),
    ]
    changelog = {
        "A-1": [
            {
                "created": "2026-04-20T10:00:00.000+0000",
                "items": [{"field": "assignee", "fromString": "Old Owner", "toString": "New Owner"}],
            },
        ],
    }

    report = build_period_report(sprints, "customfield_1", ["done"], status_changelogs=changelog)

    assert [a.display_name for a in report.assignees] == ["Old Owner"]


def test_assignee_spillover():
    s1 = SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "new", 5, "Ivan")])
    s2 = SprintIssues("Team A", 2, "S2", date(2026, 4, 15), date(2026, 4, 28), datetime(2026, 4, 28), [issue("A-1", "new", 5, "Petr")])
    report = build_period_report([s1, s2], "customfield_1", ["done"])
    ivan = next(a for a in report.assignees if a.display_name == "Ivan")
    assert ivan.spillover == 1
    assert ivan.reassigned == 0


def test_assignee_empty_sprint():
    sprints = [
        SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), []),
    ]
    report = build_period_report(sprints, None, ["done"])
    assert report.assignees == []


def test_assignee_items_backlog_breakdown():
    sprints = [
        SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "done", 3, "Ivan"), issue("A-2", "new", 5, "Ivan")]),
    ]
    report = build_period_report(sprints, "customfield_1", ["done"], {"Team A": {"A-2"}})
    a = report.assignees[0]
    assert a.backlog == 1
    backlog_items = [i for i in a.items if i.category == "backlog"]
    assert len(backlog_items) == 1
    assert backlog_items[0].key == "A-2"


def test_assignee_items_spillover_breakdown():
    s1 = SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "new", 5, "Ivan")])
    s2 = SprintIssues("Team A", 2, "S2", date(2026, 4, 15), date(2026, 4, 28), datetime(2026, 4, 28), [issue("A-1", "new", 5, "Petr")])
    report = build_period_report([s1, s2], "customfield_1", ["done"])
    ivan = next(a for a in report.assignees if a.display_name == "Ivan")
    assert ivan.spillover == 1
    spill_items = [i for i in ivan.items if i.category == "spillover"]
    assert len(spill_items) == 1
    assert spill_items[0].key == "A-1"


def test_assignee_items_reassigned_breakdown():
    s1 = SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "new", 5, "Ivan")])
    s2 = SprintIssues("Team A", 2, "S2", date(2026, 4, 15), date(2026, 4, 28), datetime(2026, 4, 28), [issue("A-2", "done", 3, "Ivan")])
    report = build_period_report([s1, s2], "customfield_1", ["done"])
    ivan = report.assignees[0]
    assert ivan.reassigned == 1
    reassigned_items = [i for i in ivan.items if i.category == "reassigned"]
    assert len(reassigned_items) == 1
    assert reassigned_items[0].key == "A-1"


def test_collect_period_result_progress_callback():
    import jira_analytics

    calls: list[str] = []

    class FakeJira:
        def get_all_fields(self):
            return [{"name": "Story points", "id": "customfield_1"}]
        def get_all_sprints_from_board(self, board_id, state, start=0, limit=50):
            return {"values": [
                {"id": 1, "name": "Sprint 1", "startDate": "2026-04-01T00:00:00.000+0000", "endDate": "2026-04-14T00:00:00.000+0000", "state": "closed", "completeDate": "2026-04-14T00:00:00.000+0000"},
            ], "isLast": True}
        def get_all_issues_for_sprint_in_board(self, board_id, sprint_id, fields, start=0, limit=100):
            return {"issues": [issue("A-1", "done", 3)], "isLast": True}

    old_jira_client = jira_analytics.jira_client
    jira_analytics.jira_client = lambda env, timeout=None: FakeJira()
    try:
        collect_period_result(
            TEST_ENV,
            Config(analytics_enabled=True, period_start=date(2026,4,1), period_end=date(2026,6,30),
                   boards=[jira_analytics.BoardConfig(101, "Team A")]),
            progress=lambda text, cur, tot: calls.append(text),
        )
    finally:
        jira_analytics.jira_client = old_jira_client

    assert any("boards" in c for c in calls)
    assert any("Loaded" in c and "sprints" in c for c in calls)
    assert any("Calculating metrics" in c for c in calls)


def test_changelog_cache_ttl_config(tmp_path):
    cfg = load_config_text("changelog_cache_ttl_hours: 48\n")
    assert cfg.changelog_cache_ttl_hours == 48

    cfg.changelog_cache_ttl_hours = 72
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    saved = load_config_text(path.read_text(encoding="utf-8"))
    assert saved.changelog_cache_ttl_hours == 72


def test_changelog_cache_namespace_and_full_refresh_bypass(tmp_path):
    import jira_analytics

    class FakeJira:
        def __init__(self):
            self.calls = 0

        def get_issue_changelog(self, key, start=0, limit=100):
            self.calls += 1
            return [{"created": "2026-04-01", "items": []}]

    old_dir = jira_analytics.CHANGELOG_CACHE_DIR
    jira_analytics.CHANGELOG_CACHE_DIR = tmp_path / "changelog"
    try:
        jira_analytics.save_changelog_cache("A-1", [], "account-a")
        assert jira_analytics.load_changelog_cache("A-1", 24, "account-a") == []
        assert jira_analytics.load_changelog_cache("A-1", 24, "account-b") is None

        jira = FakeJira()
        assert jira_analytics.fetch_issue_changelog(jira, "A-1", 24, "account-a") == []
        assert jira.calls == 0
        assert jira_analytics.fetch_issue_changelog(jira, "A-1", 24, "account-a", force=True)
        assert jira.calls == 1
    finally:
        jira_analytics.CHANGELOG_CACHE_DIR = old_dir


def test_changelog_cache_write_failure_keeps_fetched_history():
    import jira_analytics

    class FakeJira:
        def get_issue_changelog(self, key, start=0, limit=100):
            return [{"created": "2026-04-02", "items": []}]

    with patch("jira_analytics.save_changelog_cache", side_effect=OSError("disk full")):
        assert jira_analytics.fetch_issue_changelog(FakeJira(), "A-1", force=True) == [
            {"created": "2026-04-02", "items": []}
        ]


def test_clear_changelog_cache_removes_namespaced_files(tmp_path):
    import jira_analytics

    old_dir = jira_analytics.CHANGELOG_CACHE_DIR
    jira_analytics.CHANGELOG_CACHE_DIR = tmp_path / "changelog"
    try:
        jira_analytics.save_changelog_cache("A-1", [], "account-a")
        path = jira_analytics._changelog_cache_path("A-1", "account-a")
        assert path.exists()
        jira_analytics.clear_changelog_cache()
        assert not path.exists()
    finally:
        jira_analytics.CHANGELOG_CACHE_DIR = old_dir


def test_atomic_cache_write_failure_preserves_previous_file(tmp_path):
    import jira_analytics

    path = tmp_path / "cache.json"
    path.write_text('{"old": true}', encoding="utf-8")
    with patch("jira_analytics.os.replace", side_effect=OSError("disk full")):
        try:
            jira_analytics._atomic_write_json(path, {"new": True})
        except OSError:
            pass
        else:
            raise AssertionError("atomic cache write must report replace failure")
    assert json.loads(path.read_text(encoding="utf-8")) == {"old": True}


def test_period_source_cache_save_failure_keeps_report_available():
    import jira_analytics

    config = Config(
        analytics_enabled=True,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 6, 30),
        boards=[],
    )
    with patch("jira_analytics.jira_client", return_value=object()), \
         patch("jira_analytics.field_id", return_value="customfield_1"), \
         patch("jira_analytics.load_period_source_cache", return_value=None), \
         patch("jira_analytics.save_period_source_cache", side_effect=OSError("disk full")):
        report = collect_period_result(TEST_ENV, config)
    assert any("cache" in error.lower() and "disk full" in error for error in report.errors)


def test_period_cache_preserves_and_invalidates_by_config_signature(tmp_path):
    import jira_analytics
    import jira_period_analytics as jpa
    old_cache = jira_analytics.PERIOD_CACHE
    cfg_path = tmp_path / "cache.json"
    jira_analytics.PERIOD_CACHE = cfg_path
    try:
        env = {"JIRA_URL": "https://jira.example.org"}
        cfg_a = Config(analytics_enabled=True, period_start=date(2026, 4, 1), period_end=date(2026, 6, 30),
                       boards=[jira_analytics.BoardConfig(101, "A")])
        cfg_b = Config(analytics_enabled=True, period_start=date(2026, 1, 1), period_end=date(2026, 3, 31),
                       boards=[jira_analytics.BoardConfig(202, "B")])

        sig_a = jira_analytics._config_signature(env, cfg_a)
        sig_b = jira_analytics._config_signature(env, cfg_b)
        assert sig_a != sig_b, "different configs must have different signatures"

        sprint = jpa.SprintSummary("A", 1, "S1", date(2026,4,1), date(2026,4,14), 5, 3, 20.0, 12.0, 60.0, 2, 0, 0, 0, 0, 0, 0, 0.0, 50.0, 3.0, 7.0, 10.0, 2.0, 5.0, {}, {})
        board = jpa.BoardAggregate("A", 1, 5, 3, 20.0, 12.0, 12.0, 60.0, 2, 0, 0, 0.0, 0, 0, 0, 0, 0.0, 50.0)
        portfolio = jpa.PortfolioAggregate(1, 1, 5, 3, 20.0, 12.0, 12.0, 60.0, 2, 0, 0, 0, 0, 0, 0, 0.0, 50.0)
        report = jpa.PeriodReport(portfolio, [board], [sprint], [], [])

        jira_analytics.save_period_cache(report, sig_a)
        assert cfg_path.exists()

        loaded = jira_analytics.load_period_cache(sig_a)
        assert loaded is not None
        assert loaded.portfolio.boards_count == 1

        loaded_b = jira_analytics.load_period_cache(sig_b)
        assert loaded_b is None, "different config signatures must invalidate cache"

        caches = jira_analytics.load_period_cache(sig_a)
        assert caches is not None, "same config signatures must load cache"
    finally:
        jira_analytics.PERIOD_CACHE = old_cache
        if cfg_path.exists():
            cfg_path.unlink()


def test_period_cache_rejects_old_routing_shape(tmp_path):
    import dataclasses
    import jira_analytics

    old_cache = jira_analytics.PERIOD_CACHE
    jira_analytics.PERIOD_CACHE = tmp_path / "old-routing.json"
    try:
        sprint = SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "new")])
        report = build_period_report([sprint], "customfield_1", ["done"])
        raw = dataclasses.asdict(report)
        raw["portfolio"].pop("completed_later_count")
        jira_analytics.PERIOD_CACHE.write_text(json.dumps({"config_sig": "current", "report": raw}, default=str), encoding="utf-8")
        assert jira_analytics.load_period_cache("current") is None
    finally:
        jira_analytics.PERIOD_CACHE = old_cache


def test_period_cache_preserves_routing_detail_fields(tmp_path):
    import jira_analytics
    import jira_period_analytics as jpa

    old_cache = jira_analytics.PERIOD_CACHE
    jira_analytics.PERIOD_CACHE = tmp_path / "routing-roundtrip.json"
    try:
        sprint = SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "new")])
        changelog = {"A-1": [{
            "created": "2026-04-20", "items": [{"field": "status", "fromString": "Open", "toString": "Done"}],
        }]}
        scope_issue = issue("A-1", "indeterminate", status_name="В работе")
        scope_issue["fields"].update({"summary": "Active", "created": "2026-04-01", "updated": "2026-04-20"})
        statuses = jpa.BoardStatuses(["done"], ["В работе"], ["Новая"])
        scope = jpa.compute_team_scope([scope_issue], "Team A", statuses)
        report = build_period_report(
            [sprint], "customfield_1", ["done"], status_changelogs=changelog,
            team_scope=[scope], scope_statuses={"Team A": {"A-1": jpa.StatusClassification("В работе", "active")}},
            classification_complete_boards={"Team A"},
        )
        jira_analytics.save_period_cache(report, "current")
        loaded = jira_analytics.load_period_cache("current")

        assert loaded is not None
        assert loaded.assignees[0].items[0].route_category == "active"
        assert loaded.assignees[0].items[0].completed_later is True
        assert loaded.assignees[0].items[0].current_status == "В работе"
        assert loaded.assignees[0].items[0].current_status_category == "active"
        assert loaded.portfolio.active_count == 1
        assert loaded.team_scope == [scope]
        assert loaded == report
    finally:
        jira_analytics.PERIOD_CACHE = old_cache


def test_period_cache_rejects_old_status_shape(tmp_path):
    import dataclasses
    import jira_analytics

    old_cache = jira_analytics.PERIOD_CACHE
    jira_analytics.PERIOD_CACHE = tmp_path / "old-status-shape.json"
    try:
        sprint = SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "new")])
        raw = dataclasses.asdict(build_period_report([sprint], "customfield_1", ["done"]))
        raw["portfolio"].pop("active_count")
        raw.pop("team_scope")
        jira_analytics.PERIOD_CACHE.write_text(json.dumps({"config_sig": "current", "report": raw}, default=str), encoding="utf-8")
        assert jira_analytics.load_period_cache("current") is None
    finally:
        jira_analytics.PERIOD_CACHE = old_cache


def test_period_source_cache_roundtrip_and_identity(tmp_path):
    import jira_analytics

    old_cache = jira_analytics.PERIOD_SOURCE_CACHE
    jira_analytics.PERIOD_SOURCE_CACHE = tmp_path / "source_roundtrip.json"
    env = {"JIRA_URL": "https://jira.example.org", "JIRA_USERNAME": "alice"}
    config = Config(story_points_field="Story points")
    payload = {"points_field": "customfield_1", "sprints": {"101:1": {"issues": [], "removed_issues": []}}}
    try:
        save_period_source_cache(env, config, payload)
        assert load_period_source_cache(env, config) == payload
        assert load_period_source_cache({**env, "JIRA_USERNAME": "bob"}, config) is None
    finally:
        jira_analytics.PERIOD_SOURCE_CACHE = old_cache


def test_collect_period_result_reuses_closed_sprint_cache_and_full_refresh_bypasses_it(tmp_path):
    import jira_analytics

    calls = {"issues": 0}

    class FakeJira:
        def get_all_fields(self):
            return [{"name": "Story points", "id": "customfield_1"}]

        def get_all_sprints_from_board(self, board_id, state, start=0, limit=50):
            return {"values": [{
                "id": 1, "name": "Sprint 1", "state": "closed",
                "startDate": "2026-04-01T00:00:00.000+0000",
                "endDate": "2026-04-14T00:00:00.000+0000",
                "completeDate": "2026-04-14T00:00:00.000+0000",
            }], "isLast": True}

        def get_all_issues_for_sprint_in_board(self, board_id, sprint_id, fields, start=0, limit=100):
            calls["issues"] += 1
            return {"issues": [issue("A-1", "done", 3)], "isLast": True}

    old_cache = jira_analytics.PERIOD_SOURCE_CACHE
    old_jira_client = jira_analytics.jira_client
    old_fetch_changelog = jira_analytics.fetch_issue_changelog
    jira_analytics.PERIOD_SOURCE_CACHE = tmp_path / "source_collect.json"
    jira_analytics.jira_client = lambda env, timeout=None: FakeJira()
    jira_analytics.fetch_issue_changelog = lambda jira, key, ttl_hours=24, namespace="", force=False: []
    config = Config(
        analytics_enabled=True,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 6, 30),
        boards=[jira_analytics.BoardConfig(101, "Team A")],
    )
    env = {"JIRA_URL": "https://jira.example.org", "JIRA_USERNAME": "alice"}
    try:
        collect_period_result(env, config)
        collect_period_result(env, config)
        assert calls["issues"] == 1
        collect_period_result(env, config, full_refresh=True)
        assert calls["issues"] == 2
    finally:
        jira_analytics.PERIOD_SOURCE_CACHE = old_cache
        jira_analytics.jira_client = old_jira_client
        jira_analytics.fetch_issue_changelog = old_fetch_changelog


def test_full_refresh_uses_cached_closed_sprint_when_network_fails(tmp_path):
    import jira_analytics

    runtime = {"fail": False}

    class FakeJira:
        def get_all_fields(self):
            return [{"name": "Story points", "id": "customfield_1"}]

        def get_all_sprints_from_board(self, board_id, state, start=0, limit=50):
            return {"values": [{
                "id": 1, "name": "Closed", "state": "closed",
                "startDate": "2026-04-01", "endDate": "2026-04-14", "completeDate": "2026-04-14",
            }], "isLast": True}

        def get_all_issues_for_sprint_in_board(self, board_id, sprint_id, fields, start=0, limit=100):
            if runtime["fail"]:
                raise RuntimeError("temporary sprint failure")
            return {"issues": [issue("A-1", "done", 3)], "isLast": True}

    old_cache = jira_analytics.PERIOD_SOURCE_CACHE
    old_jira_client = jira_analytics.jira_client
    old_fetch_changelog = jira_analytics.fetch_issue_changelog
    jira_analytics.PERIOD_SOURCE_CACHE = tmp_path / "source_full_fallback.json"
    jira_analytics.jira_client = lambda env, timeout=None: FakeJira()
    jira_analytics.fetch_issue_changelog = lambda jira, key, ttl_hours=24, namespace="", force=False: []
    config = Config(analytics_enabled=True, period_start=date(2026, 4, 1), period_end=date(2026, 6, 30), boards=[jira_analytics.BoardConfig(101, "Team A")])
    env = {"JIRA_URL": "https://jira.example.org", "JIRA_USERNAME": "alice"}
    try:
        collect_period_result(env, config)
        runtime["fail"] = True
        report = collect_period_result(env, config, full_refresh=True)
        assert report.portfolio.sprints_count == 1
        assert any("temporary sprint failure" in error for error in report.errors)
    finally:
        jira_analytics.PERIOD_SOURCE_CACHE = old_cache
        jira_analytics.jira_client = old_jira_client
        jira_analytics.fetch_issue_changelog = old_fetch_changelog


def test_full_refresh_field_lookup_failure_does_not_replace_source_cache(tmp_path):
    import jira_analytics

    old_cache = jira_analytics.PERIOD_SOURCE_CACHE
    jira_analytics.PERIOD_SOURCE_CACHE = tmp_path / "source_field.json"
    env = {"JIRA_URL": "https://jira.example.org", "JIRA_USERNAME": "alice"}
    config = Config(
        analytics_enabled=True, period_start=date(2026, 4, 1), period_end=date(2026, 6, 30), boards=[]
    )
    payload = {"points_field": "customfield_1", "sprints": {}}
    save_period_source_cache(env, config, payload)
    try:
        with patch("jira_analytics.jira_client", return_value=object()), \
             patch("jira_analytics.field_id", side_effect=SystemExit("field denied")):
            try:
                collect_period_result(env, config, full_refresh=True)
            except SystemExit:
                pass
            else:
                raise AssertionError("full refresh must stop when fields cannot be refreshed")
        assert load_period_source_cache(env, config) == payload
    finally:
        jira_analytics.PERIOD_SOURCE_CACHE = old_cache


def test_period_source_cache_does_not_store_sprints_without_points_field(tmp_path):
    import jira_analytics

    runtime = {"fields": 0, "issues": 0}

    class FakeJira:
        def get_all_fields(self):
            runtime["fields"] += 1
            return [] if runtime["fields"] == 1 else [{"name": "Story points", "id": "customfield_1"}]

        def get_all_sprints_from_board(self, board_id, state, start=0, limit=50):
            return {"values": [{"id": 1, "name": "Closed", "state": "closed", "startDate": "2026-04-01", "endDate": "2026-04-14", "completeDate": "2026-04-14"}], "isLast": True}

        def get_all_issues_for_sprint_in_board(self, board_id, sprint_id, fields, start=0, limit=100):
            runtime["issues"] += 1
            return {"issues": [issue("A-1", "done", 3)], "isLast": True}

    old_cache = jira_analytics.PERIOD_SOURCE_CACHE
    old_jira_client = jira_analytics.jira_client
    old_fetch_changelog = jira_analytics.fetch_issue_changelog
    jira_analytics.PERIOD_SOURCE_CACHE = tmp_path / "source_no_points.json"
    jira_analytics.jira_client = lambda env, timeout=None: FakeJira()
    jira_analytics.fetch_issue_changelog = lambda jira, key, ttl_hours=24, namespace="", force=False: []
    config = Config(analytics_enabled=True, period_start=date(2026, 4, 1), period_end=date(2026, 6, 30), boards=[jira_analytics.BoardConfig(101, "Team A")])
    try:
        collect_period_result(TEST_ENV, config)
        collect_period_result(TEST_ENV, config)
        assert runtime["issues"] == 2
    finally:
        jira_analytics.PERIOD_SOURCE_CACHE = old_cache
        jira_analytics.jira_client = old_jira_client
        jira_analytics.fetch_issue_changelog = old_fetch_changelog


def test_collect_period_source_cache_refreshes_active_and_falls_back_on_board_error(tmp_path):
    import jira_analytics

    runtime = {"deny_board": False, "issues": []}

    class FakeJira:
        def get_all_fields(self):
            return [{"name": "Story points", "id": "customfield_1"}]

        def get_all_sprints_from_board(self, board_id, state, start=0, limit=50):
            if runtime["deny_board"]:
                raise RuntimeError("board unavailable")
            return {"values": [
                {"id": 1, "name": "Closed", "state": "closed", "startDate": "2026-04-01", "endDate": "2026-04-14", "completeDate": "2026-04-14"},
                {"id": 2, "name": "Active", "state": "active", "startDate": "2026-04-15", "endDate": "2026-04-28"},
            ], "isLast": True}

        def get_all_issues_for_sprint_in_board(self, board_id, sprint_id, fields, start=0, limit=100):
            runtime["issues"].append(sprint_id)
            return {"issues": [issue(f"A-{sprint_id}", "done", 3)], "isLast": True}

    old_cache = jira_analytics.PERIOD_SOURCE_CACHE
    old_jira_client = jira_analytics.jira_client
    old_fetch_changelog = jira_analytics.fetch_issue_changelog
    jira_analytics.PERIOD_SOURCE_CACHE = tmp_path / "source_fallback.json"
    jira_analytics.jira_client = lambda env, timeout=None: FakeJira()
    jira_analytics.fetch_issue_changelog = lambda jira, key, ttl_hours=24, namespace="", force=False: []
    config = Config(
        analytics_enabled=True,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 6, 30),
        boards=[jira_analytics.BoardConfig(101, "Team A")],
    )
    env = {"JIRA_URL": "https://jira.example.org", "JIRA_USERNAME": "alice"}
    try:
        collect_period_result(env, config)
        collect_period_result(env, config)
        assert runtime["issues"] == [1, 2, 2]

        runtime["deny_board"] = True
        report = collect_period_result(env, config)
        assert report.portfolio.sprints_count == 1
        assert report.sprints[0].sprint_id == 1
        assert any("board unavailable" in error for error in report.errors)
    finally:
        jira_analytics.PERIOD_SOURCE_CACHE = old_cache
        jira_analytics.jira_client = old_jira_client
        jira_analytics.fetch_issue_changelog = old_fetch_changelog


def test_collect_period_result_propagates_forbidden_instead_of_saving_partial_report(tmp_path):
    import jira_analytics

    class Response:
        status_code = 403
        reason = "Forbidden"

    class HttpError(Exception):
        response = Response()

    class FakeJira:
        def get_all_fields(self):
            return [{"name": "Story points", "id": "customfield_1"}]

        def get_all_sprints_from_board(self, board_id, state, start=0, limit=50):
            raise HttpError("Forbidden")

    old_cache = jira_analytics.PERIOD_SOURCE_CACHE
    old_jira_client = jira_analytics.jira_client
    jira_analytics.PERIOD_SOURCE_CACHE = tmp_path / "source_forbidden.json"
    jira_analytics.jira_client = lambda env, timeout=None: FakeJira()
    try:
        try:
            collect_period_result(
                {"JIRA_URL": "https://jira.example.org", "JIRA_USERNAME": "alice"},
                Config(
                    analytics_enabled=True,
                    period_start=date(2026, 4, 1),
                    period_end=date(2026, 6, 30),
                    boards=[jira_analytics.BoardConfig(101, "Team A")],
                ),
            )
        except HttpError:
            pass
        else:
            raise AssertionError("403 must abort period refresh")
        assert not jira_analytics.PERIOD_SOURCE_CACHE.exists()
    finally:
        jira_analytics.PERIOD_SOURCE_CACHE = old_cache
        jira_analytics.jira_client = old_jira_client


def test_period_source_cache_retries_only_missing_removed_issues(tmp_path):
    import jira_analytics

    calls = {"issues": 0, "report": 0}

    class FakeJira:
        def get_all_fields(self):
            return [{"name": "Story points", "id": "customfield_1"}]

        def get_all_sprints_from_board(self, board_id, state, start=0, limit=50):
            return {"values": [{
                "id": 1, "name": "Closed", "state": "closed",
                "startDate": "2026-04-01", "endDate": "2026-04-14", "completeDate": "2026-04-14",
            }], "isLast": True}

        def get_all_issues_for_sprint_in_board(self, board_id, sprint_id, fields, start=0, limit=100):
            calls["issues"] += 1
            return {"issues": [issue("A-1", "done", 3)], "isLast": True}

        def get(self, path):
            calls["report"] += 1
            if calls["report"] == 1:
                raise RuntimeError("report unavailable")
            return {"contents": {"puntedIssues": [{"key": "A-2", "summary": "Removed"}]}}

    old_cache = jira_analytics.PERIOD_SOURCE_CACHE
    old_jira_client = jira_analytics.jira_client
    old_fetch_changelog = jira_analytics.fetch_issue_changelog
    jira_analytics.PERIOD_SOURCE_CACHE = tmp_path / "source_removed.json"
    jira_analytics.jira_client = lambda env, timeout=None: FakeJira()
    jira_analytics.fetch_issue_changelog = lambda jira, key, ttl_hours=24, namespace="", force=False: []
    config = Config(
        analytics_enabled=True, period_start=date(2026, 4, 1), period_end=date(2026, 6, 30),
        boards=[jira_analytics.BoardConfig(101, "Team A")],
    )
    try:
        first = collect_period_result(TEST_ENV, config)
        second = collect_period_result(TEST_ENV, config)
        assert first.sprints[0].removed_count == 0
        assert second.sprints[0].removed_count == 1
        assert calls == {"issues": 1, "report": 2}
    finally:
        jira_analytics.PERIOD_SOURCE_CACHE = old_cache
        jira_analytics.jira_client = old_jira_client
        jira_analytics.fetch_issue_changelog = old_fetch_changelog


def test_reopened_sprint_is_fetched_again_after_it_closes(tmp_path):
    import jira_analytics

    runtime = {"state": "closed", "issues": 0, "complete": "2026-04-14"}

    class FakeJira:
        def get_all_fields(self):
            return [{"name": "Story points", "id": "customfield_1"}]

        def get_all_sprints_from_board(self, board_id, state, start=0, limit=50):
            item = {"id": 1, "name": "Sprint", "state": runtime["state"], "startDate": "2026-04-01", "endDate": "2026-04-14"}
            if runtime["state"] == "closed":
                item["completeDate"] = runtime["complete"]
            return {"values": [item], "isLast": True}

        def get_all_issues_for_sprint_in_board(self, board_id, sprint_id, fields, start=0, limit=100):
            runtime["issues"] += 1
            return {"issues": [issue("A-1", "done", 3)], "isLast": True}

    old_cache = jira_analytics.PERIOD_SOURCE_CACHE
    old_jira_client = jira_analytics.jira_client
    old_fetch_changelog = jira_analytics.fetch_issue_changelog
    jira_analytics.PERIOD_SOURCE_CACHE = tmp_path / "source_reopened.json"
    jira_analytics.jira_client = lambda env, timeout=None: FakeJira()
    jira_analytics.fetch_issue_changelog = lambda jira, key, ttl_hours=24, namespace="", force=False: []
    config = Config(analytics_enabled=True, period_start=date(2026, 4, 1), period_end=date(2026, 6, 30), boards=[jira_analytics.BoardConfig(101, "Team A")])
    try:
        collect_period_result(TEST_ENV, config)
        runtime["state"] = "active"
        collect_period_result(TEST_ENV, config)
        runtime["state"] = "closed"
        runtime["complete"] = "2026-04-20"
        collect_period_result(TEST_ENV, config)
        assert runtime["issues"] == 3
    finally:
        jira_analytics.PERIOD_SOURCE_CACHE = old_cache
        jira_analytics.jira_client = old_jira_client
        jira_analytics.fetch_issue_changelog = old_fetch_changelog


def test_load_config_short_list():
    cfg = load_config_text(
        """
- AL-5948
- project = AL
"""
    )
    assert cfg.warn_days == 7
    assert cfg.items == ["AL-5948", "project = AL"]


def test_alert_kind():
    today = date(2026, 6, 30)
    assert alert_kind(date(2026, 6, 29), today, 7) == "overdue"
    assert alert_kind(date(2026, 7, 7), today, 7) == "soon"
    assert alert_kind(date(2026, 7, 8), today, 7) is None


def test_alert_issue_reads_assignee_display_name():
    alert = alert_issue(
        {
            "key": "AL-1",
            "fields": {
                "summary": "Task",
                "status": {"name": "Open"},
                "Target end": "2026-06-29",
                "assignee": {"displayName": "Ivan Petrov"},
            },
        },
        "Target end",
        7,
        date(2026, 6, 30),
        jira_url=TEST_ENV["JIRA_URL"],
    )
    assert alert.assignee == "Ivan Petrov"


def test_alert_issue_uses_fallback_assignee():
    alert = alert_issue(
        {
            "key": "AL-1",
            "fields": {"summary": "Task", "status": {"name": "Open"}, "Target end": "2026-06-29"},
        },
        "Target end",
        7,
        date(2026, 6, 30),
        jira_url=TEST_ENV["JIRA_URL"],
    )
    assert alert.assignee == "Unassigned"


def test_parse_date_invalid_value():
    assert parse_date("not-a-date") is None


def test_parse_datetime_normalizes_offsets_to_utc():
    import jira_period_analytics as jpa

    assert jpa.parse_datetime("2026-04-14T03:00:00+03:00") == datetime(2026, 4, 14, 0, 0)


def test_build_notify_command_uses_standard_notifications():
    cmd = build_notify_command("title", "body")
    assert "org.freedesktop.Notifications" in cmd
    assert "org.kde.knotifications" not in cmd
    assert "org.freedesktop.Notifications.Notify" in cmd


def test_notify_without_gdbus(monkeypatch=None):
    class MissingGdbus:
        def __call__(self, *args, **kwargs):
            raise FileNotFoundError("gdbus")

    import jira_analytics

    old_run = jira_analytics.subprocess.run
    jira_analytics.subprocess.run = MissingGdbus()
    try:
        assert notify("title", "body") is False
    finally:
        jira_analytics.subprocess.run = old_run


def test_cli_help_exits():
    import jira_analytics_cli

    try:
        jira_analytics_cli.main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("--help должен завершаться через SystemExit")


def test_compute_backlog_health():
    from datetime import datetime, timedelta, date
    today = date.today()
    def make_issue(created_days_ago, updated_days_ago):
        created = (today - timedelta(days=created_days_ago)).isoformat()
        updated = (today - timedelta(days=updated_days_ago)).isoformat()
        return {"key": f"T-{created_days_ago}", "fields": {"created": created, "updated": updated,
                "summary": f"Task {created_days_ago}", "status": {"name": "Open"},
                "assignee": {"displayName": "User"}}}
    issues = [
        make_issue(10, 5), make_issue(30, 15), make_issue(45, 10),
        make_issue(90, 95), make_issue(100, 95), make_issue(180, 95),
        make_issue(200, 50), make_issue(365, 200), make_issue(400, 400),
    ]
    bh = compute_backlog_health(issues, "Test Board", today)
    assert bh.board_name == "Test Board"
    assert bh.total_open == 9
    assert bh.aging_30plus == 8      # 30, 45, 90, 100, 180, 200, 365, 400
    assert bh.aging_90plus == 6
    assert bh.aging_180plus == 4
    assert bh.aging_365plus == 2
    assert bh.stale_count == 5
    assert len(bh.items) == 9
    for item in bh.items:
        assert item.key.startswith("T-")
        assert item.age_days > 0


def test_compute_backlog_health_filters_done():
    from datetime import datetime, timedelta, date
    today = date.today()
    def make(key, created_days, updated_days, status_name):
        created = (today - timedelta(days=created_days)).isoformat()
        updated = (today - timedelta(days=updated_days)).isoformat()
        return {"key": key, "fields": {"created": created, "updated": updated,
                "summary": key, "status": {"name": status_name},
                "assignee": {"displayName": "User"}}}
    issues = [
        make("OK-1", 100, 10, "Open"),
        make("OK-2", 200, 20, "In Progress"),
        make("DONE-1", 50, 5, "Done"),
        make("DONE-2", 300, 30, "Closed"),
        make("DONE-3", 400, 400, "Завершена"),
    ]
    bh = compute_backlog_health(issues, "Test", today, done_statuses=["Done", "Closed", "Завершена"])
    assert bh.total_open == 2  # только OK-1 и OK-2
    assert bh.aging_90plus == 2  # 100 и 200
    assert bh.aging_365plus == 0  # ни одна из открытых
    assert len(bh.items) == 2
    assert {i.key for i in bh.items} == {"OK-1", "OK-2"}


def test_compute_backlog_health_empty():
    assert compute_backlog_health([], "Empty Board") is not None
    bh = compute_backlog_health([], "Empty Board")
    assert bh.total_open == 0
    assert bh.aging_30plus == 0
    assert bh.aging_avg_days == 0.0
    assert bh.aging_max_days == 0


def test_assignee_external_owner_core():
    s1 = SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14),
          datetime(2026, 4, 14), [issue("A-1", "new", 5, "Ivan")])
    changelog = {"A-1": [{"created": "2026-04-20T10:00:00.000+0000",
        "items": [{"field": "assignee", "fromString": "Alexander", "toString": "Ivan"}]}]}
    report = build_period_report([s1], "customfield_1", ["done"], status_changelogs=changelog)
    names = [a.display_name for a in report.assignees]
    assert "External / Other" in names, f"core must always group external, got {names}"
    ext = next(a for a in report.assignees if a.display_name == "External / Other")
    assert len(ext.items) == 1
    assert ext.items[0].original_name == "Alexander"
    assert ext.items[0].key == "A-1"
    assert ext.board_name == "Team A"


def test_assignee_external_owner_cache():
    import dataclasses
    s1 = SprintIssues("Team A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14),
          datetime(2026, 4, 14), [issue("A-1", "new", 5, "Ivan")])
    changelog = {"A-1": [{"created": "2026-04-20T10:00:00.000+0000",
        "items": [{"field": "assignee", "fromString": "Alexander", "toString": "Ivan"}]}]}
    report = build_period_report([s1], "customfield_1", ["done"], status_changelogs=changelog)
    raw = dataclasses.asdict(report)
    for a in raw["assignees"]:
        for item in a["items"]:
            assert item.get("original_name") is not None, f"original_name must be in asdict for cache"
    for a in raw["assignees"]:
        for item in a["items"]:
            if item.get("original_name"):
                reconstructed = AssigneeIssue(**{k: v for k, v in item.items()})
                assert reconstructed.original_name == item["original_name"]


def _make_chart_result(sprint_issues, backlog_health=None):
    report = build_period_report(sprint_issues, "customfield_1", ["done"], backlog_health=backlog_health)
    return report


def test_chart_slots_sorting():
    s2 = SprintIssues("A", 2, "S2", date(2026, 5, 1), date(2026, 5, 14), datetime(2026, 5, 14), [issue("A-2", "done", 3, "Ivan")])
    s1 = SprintIssues("A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "done", 5, "Ivan")])
    result = _make_chart_result([s2, s1])
    slots = build_chart_slots(result.sprints, period_fn=period_label_for_sprint)
    labels = [s["chartSlotLabel"] for s in slots]
    assert labels[0] < labels[1], f"slots should be chronological, got {labels}"


def test_chart_slots_aggregation():
    s1 = SprintIssues("A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "done", 3, "Ivan")])
    s2 = SprintIssues("A", 2, "S2", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-2", "new", 5, "Ivan")])
    result = _make_chart_result([s1, s2])
    slots = build_chart_slots(result.sprints, period_fn=period_label_for_sprint)
    assert len(slots) == 1, f"same date range should be aggregated, got {len(slots)} slots"
    assert int(slots[0]["total"]) == 2
    assert float(slots[0]["totalPoints"]) == 8.0


def test_chart_slots_labels_global():
    s1 = SprintIssues("A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "done", 3, "Ivan")])
    s2 = SprintIssues("A", 2, "S2", date(2026, 5, 1), date(2026, 5, 14), datetime(2026, 5, 14), [issue("A-2", "new", 5, "Ivan")])
    result = _make_chart_result([s1, s2])
    slots = build_chart_slots(result.sprints, period_fn=period_label_for_sprint)
    labels = [s["chartSlotLabel"] for s in slots]
    assert labels[0] == "Q2S1", f"expected Q2S1, got {labels[0]}"
    assert labels[1] == "Q2S2", f"expected Q2S2, got {labels[1]}"


def test_chart_slots_labels_cross_team():
    s_a = SprintIssues("A", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("A-1", "done", 3, "Ivan")])
    s_b = SprintIssues("B", 1, "S1", date(2026, 4, 1), date(2026, 4, 14), datetime(2026, 4, 14), [issue("B-1", "done", 5, "Petr")])
    result = _make_chart_result([s_a, s_b])
    slots = build_chart_slots(result.sprints, period_fn=period_label_for_sprint)
    labels = [s["chartSlotLabel"] for s in slots]
    assert labels[0] == labels[1], f"same date range across teams should share label, got {labels}"


def test_chart_slots_lead_time_percentiles_use_correct_fields():
    from dataclasses import dataclass

    @dataclass
    class FakeSummary:
        board_name: str
        start_date: date
        end_date: date
        done_issues: int = 0
        total_issues: int = 0
        total_points: float = 0.0
        done_points: float = 0.0
        spillover_count: int = 0
        spillover_completed: int = 0
        backlog_count: int = 0
        reassigned_count: int = 0
        not_done_count: int = 0
        removed_count: int = 0
        removed_points: float = 0.0
        flow_efficiency_pct: float = 0.0
        lead_time_p50: float = 0.0
        lead_time_p85: float = 0.0
        lead_time_p95: float = 0.0
        aging_avg_days: float = 0.0
        aging_max_days: int = 0
        status_distribution: dict = None

        def __post_init__(self):
            if self.status_distribution is None:
                self.status_distribution = {}

    def fake_period(s, e):
        return "Q2"

    s1 = FakeSummary("A", date(2026,4,1), date(2026,4,14),
        done_issues=1, total_issues=1, lead_time_p50=5.0, lead_time_p85=12.0, lead_time_p95=20.0)
    s2 = FakeSummary("A", date(2026,4,1), date(2026,4,14),
        done_issues=1, total_issues=1, lead_time_p50=8.0, lead_time_p85=15.0, lead_time_p95=25.0)

    slots = build_chart_slots([s1, s2], period_fn=fake_period)
    assert len(slots) == 1
    s = slots[0]
    assert s["leadTimeP50"] != s["leadTimeP85"], f"P50={s['leadTimeP50']} should differ from P85={s['leadTimeP85']}"
    assert s["leadTimeP85"] != s["leadTimeP95"], f"P85={s['leadTimeP85']} should differ from P95={s['leadTimeP95']}"
    assert float(s["leadTimeP50"]) >= 5.0, f"P50 must be >= 5.0, got {s['leadTimeP50']}"
    assert float(s["leadTimeP95"]) >= 20.0, f"P95 must be >= 20.0, got {s['leadTimeP95']}"


def test_discover_modules_two_valid_sorted():
    with TemporaryDirectory() as d:
        root = Path(d)
        write_module(root, "b", _valid_manifest(id="b", order=5))
        write_module(root, "a", _valid_manifest(id="a", order=0))
        modules, errors = discover_modules(root)
        assert errors == []
        assert len(modules) == 2
        assert modules[0].module_id == "a"
        assert modules[1].module_id == "b"


def test_discover_modules_same_order_sorted_by_id():
    with TemporaryDirectory() as d:
        root = Path(d)
        write_module(root, "z", _valid_manifest(id="z", order=10))
        write_module(root, "a", _valid_manifest(id="a", order=10))
        modules, errors = discover_modules(root)
        assert errors == []
        assert [m.module_id for m in modules] == ["a", "z"]


def test_discover_missing_builtin_dir_returns_error():
    modules, errors = discover_modules(Path("/nonexistent/jira/modules"))
    assert modules == []
    assert len(errors) == 1


def test_discover_missing_user_dir_not_error():
    with TemporaryDirectory() as d:
        root = Path(d)
        write_module(root, "m", _valid_manifest(id="m", order=0))
        modules, errors = discover_modules(root, user_dir=Path("/nonexistent/user/modules"))
        assert errors == []
        assert len(modules) == 1


def test_discover_malformed_json_skipped():
    with TemporaryDirectory() as d:
        root = Path(d)
        mdir = root / "bad"
        mdir.mkdir(parents=True)
        (mdir / "Module.qml").write_text("import QtQuick 2.12\nItem {}\n", encoding="utf-8")
        (mdir / "module.json").write_text("not json", encoding="utf-8")
        modules, errors = discover_modules(root)
        assert modules == []
        assert len(errors) == 1
        assert "bad" in errors[0]


def test_discover_missing_required_field_skipped():
    with TemporaryDirectory() as d:
        root = Path(d)
        write_module(root, "no_id", {"title": "X", "qml": "M.qml", "order": 0, "apiVersion": 1})
        modules, errors = discover_modules(root)
        assert modules == []
        assert len(errors) == 1


def test_discover_wrong_field_types_skipped():
    bads = [
        {"id": 42, "title": "X", "qml": "M.qml", "order": 0, "apiVersion": 1},
        {"id": "ok", "title": 123, "qml": "M.qml", "order": 0, "apiVersion": 1},
        {"id": "ok", "title": "X", "qml": 99, "order": 0, "apiVersion": 1},
        {"id": "ok", "title": "X", "qml": "M.qml", "order": "zero", "apiVersion": 1},
        {"id": "ok", "title": "X", "qml": "M.qml", "order": 0, "apiVersion": "2"},
        {"id": "ok", "title": "X", "qml": "M.qml", "order": True, "apiVersion": 1},
        {"id": "ok", "title": "X", "qml": "M.qml", "order": 0, "apiVersion": True},
        {"id": "ok", "title": "X", "qml": "M.qml", "order": 0, "apiVersion": 1.0},
    ]
    for i, bad in enumerate(bads):
        with TemporaryDirectory() as d:
            root = Path(d)
            write_module(root, f"m{i}", bad)
            modules, errors = discover_modules(root)
            assert modules == [], f"case {i}: expected empty, got {[m.module_id for m in modules]}"
            assert len(errors) == 1, f"case {i}: expected 1 error, got {errors}"


def test_discover_wrong_api_version_skipped():
    with TemporaryDirectory() as d:
        root = Path(d)
        write_module(root, "mv", _valid_manifest(id="mv", apiVersion=2))
        modules, errors = discover_modules(root)
        assert modules == []
        assert len(errors) == 1


def test_discover_missing_qml_skipped():
    with TemporaryDirectory() as d:
        root = Path(d)
        mdir = root / "noqml"
        mdir.mkdir(parents=True)
        (mdir / "module.json").write_text(json.dumps(_valid_manifest(id="noqml")), encoding="utf-8")
        modules, errors = discover_modules(root)
        assert modules == []
        assert len(errors) == 1


def test_discover_absolute_qml_path_skipped():
    with TemporaryDirectory() as d:
        root = Path(d)
        write_module(root, "abs", _valid_manifest(id="abs", qml="/etc/passwd"))
        modules, errors = discover_modules(root)
        assert modules == []
        assert len(errors) == 1


def test_discover_qml_traversal_skipped():
    with TemporaryDirectory() as d:
        root = Path(d)
        write_module(root, "traversal", _valid_manifest(id="traversal", qml="../outside.qml"))
        (root / "outside.qml").write_text("import QtQuick 2.12\nItem {}\n", encoding="utf-8")
        modules, errors = discover_modules(root)
        assert modules == []
        assert len(errors) == 1


def test_discover_symlink_outside_skipped():
    with TemporaryDirectory() as d:
        root = Path(d)
        mdir = root / "sym"
        mdir.mkdir(parents=True)
        outside = root / "outside.qml"
        outside.write_text("import QtQuick 2.12\nItem {}\n", encoding="utf-8")
        symlink_path = mdir / "link.qml"
        os.symlink(str(outside), str(symlink_path))
        (mdir / "module.json").write_text(json.dumps(_valid_manifest(id="sym", qml="link.qml")), encoding="utf-8")
        modules, errors = discover_modules(root)
        assert modules == []
        assert len(errors) == 1


def test_discover_duplicate_builtin_keeps_first():
    with TemporaryDirectory() as d:
        root = Path(d)
        write_module(root, "first", _valid_manifest(id="dup", order=0))
        write_module(root, "second", _valid_manifest(id="dup", order=10))
        modules, errors = discover_modules(root)
        assert errors == []
        assert len(modules) == 1
        assert modules[0].qml_path.parent.name == "first"


def test_discover_user_duplicate_id_not_replace_builtin():
    with TemporaryDirectory() as d:
        builtin = Path(d) / "builtin"
        user = Path(d) / "user"
        write_module(builtin, "b", _valid_manifest(id="shared", order=0))
        write_module(user, "u", _valid_manifest(id="shared", order=100, title="External"))
        modules, errors = discover_modules(builtin, user_dir=user)
        assert errors == []
        assert len(modules) == 1
        assert modules[0].builtin


def test_discover_two_user_duplicate_keeps_first_stable():
    with TemporaryDirectory() as d:
        builtin = Path(d) / "builtin"
        builtin.mkdir()
        user = Path(d) / "user"
        write_module(user, "first_user", _valid_manifest(id="udup", order=0, title="First"))
        write_module(user, "second_user", _valid_manifest(id="udup", order=10, title="Second"))
        modules, errors = discover_modules(builtin, user_dir=user)
        assert len(modules) == 1
        assert modules[0].title == "First"


def test_discover_dotdot_hidden_qml_accepted():
    with TemporaryDirectory() as d:
        root = Path(d)
        manifest = _valid_manifest(id="hidden", qml="..hidden.qml")
        mdir = write_module(root, "hidden", manifest)
        (mdir / "..hidden.qml").write_text("import QtQuick 2.12\nItem {}\n", encoding="utf-8")
        modules, errors = discover_modules(root)
        assert errors == []
        assert len(modules) == 1
        assert modules[0].module_id == "hidden"


def test_discover_non_utf8_manifest_skipped():
    with TemporaryDirectory() as d:
        root = Path(d)
        mdir = root / "bad_enc"
        mdir.mkdir()
        (mdir / "Module.qml").write_text("import QtQuick 2.12\nItem {}\n", encoding="utf-8")
        (mdir / "module.json").write_bytes(b'{"id":\xff\xfe}')
        modules, errors = discover_modules(root)
        assert modules == []
        assert len(errors) == 1


def test_discover_cyclic_symlink_skipped():
    with TemporaryDirectory() as d:
        root = Path(d)
        mdir = root / "cyclic"
        mdir.mkdir()
        link = mdir / "loop.qml"
        os.symlink(str(link), str(link))
        manifest = _valid_manifest(id="cyclic", qml="loop.qml")
        (mdir / "module.json").write_text(json.dumps(manifest), encoding="utf-8")
        modules, errors = discover_modules(root)
        assert modules == []
        assert len(errors) == 1


def test_discover_permission_error_iterdir_returns_root_error():
    with TemporaryDirectory() as d:
        root = Path(d)
        write_module(root, "m", _valid_manifest(id="m", order=0))
        with patch.object(Path, "iterdir", side_effect=PermissionError):
            modules, errors = discover_modules(root)
            assert modules == []
            assert len(errors) >= 1


def test_discover_recursion_error_skipped():
    with TemporaryDirectory() as d:
        root = Path(d)
        write_module(root, "rec", _valid_manifest(id="rec"))
        with patch("jira_modules.json.loads", side_effect=RecursionError):
            modules, errors = discover_modules(root)
        assert modules == []
        assert len(errors) == 1


def test_discover_invalid_id_rejected():
    bad_ids = ["Valid", "has-hyphen", "1leading", "trailing\n"]
    for bid in bad_ids:
        with TemporaryDirectory() as d:
            root = Path(d)
            write_module(root, "m", _valid_manifest(id=bid))
            modules, errors = discover_modules(root)
            assert modules == [], f"id {bid!r} should be rejected"
            assert len(errors) == 1, f"id {bid!r}: expected 1 error, got {errors}"


def test_discover_unknown_fields_ignored():
    with TemporaryDirectory() as d:
        root = Path(d)
        write_module(root, "extra", {
            "id": "extra",
            "title": "Extra",
            "qml": "Module.qml",
            "order": 0,
            "apiVersion": 1,
            "description": "should be ignored",
            "author": "me",
        })
        modules, errors = discover_modules(root)
        assert errors == []
        assert len(modules) == 1
        assert modules[0].module_id == "extra"


def test_builtin_manifests_discovery():
    repo_modules = Path(__file__).resolve().parent / "modules"
    assert repo_modules.is_dir(), f"modules dir not found: {repo_modules}"
    modules, errors = discover_modules(repo_modules)
    assert errors == []
    assert [m.module_id for m in modules] == [
        "target_end",
        "period_analytics",
        "backlog_health",
        "assignees",
        "charts",
    ]


class FakeBackend(QObject):
    checkingChanged = pyqtSignal()
    errorsTextChanged = pyqtSignal()
    itemsTextChanged = pyqtSignal()
    periodCheckingChanged = pyqtSignal()
    periodStatusTextChanged = pyqtSignal()
    periodErrorsTextChanged = pyqtSignal()
    periodFromCacheChanged = pyqtSignal()
    periodLastRefreshChanged = pyqtSignal()
    periodKpiChanged = pyqtSignal()
    settingsChanged = pyqtSignal()
    backlogSortChanged = pyqtSignal()
    backlogAgeFilterChanged = pyqtSignal()
    assigneeBoardNamesChanged = pyqtSignal()
    assigneeKpiChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._checking = False
        self._errors_text = ""
        self._items_text = ""
        self._period_checking = False
        self._period_status_text = ""
        self._period_errors_text = ""
        self._period_from_cache = False
        self._period_last_refresh = ""
        self._period_kpi = {}
        self._selected_team = ""
        self._backlog_sort = ""
        self._backlog_active_statuses = "|done|"
        self._forgotten_age_days = 30
        self._assignee_board_names = []
        self._assignee_kpi = {}
        self._jira_url = ""
        self.calls: list[tuple[str, tuple]] = []

    def _record(self, name, *args):
        self.calls.append((name, args))

    def refresh(self, automatic=False):
        self._record("refresh", automatic)

    def saveItems(self, text):
        self._record("saveItems", text)

    def refreshPeriod(self):
        self._record("refreshPeriod")

    def setSelectedTeam(self, team):
        self._record("setSelectedTeam", team)

    def setBacklogSort(self, sort_key):
        self._record("setBacklogSort", sort_key)

    def setAssigneeBoardFilter(self, name):
        self._record("setAssigneeBoardFilter", name)

    @property
    def checking(self):
        return self._checking

    @property
    def errorsText(self):
        return self._errors_text

    @property
    def itemsText(self):
        return self._items_text

    @property
    def periodChecking(self):
        return self._period_checking

    @property
    def periodStatusText(self):
        return self._period_status_text

    @property
    def periodErrorsText(self):
        return self._period_errors_text

    @property
    def periodFromCache(self):
        return self._period_from_cache

    @property
    def periodLastRefresh(self):
        return self._period_last_refresh

    @property
    def periodKpi(self):
        return self._period_kpi

    @property
    def selectedTeam(self):
        return self._selected_team

    @property
    def backlogSort(self):
        return self._backlog_sort

    @property
    def backlogAgeFilter(self):
        if self._backlog_sort == "stale":
            return "stale"
        if self._backlog_sort == "aging365":
            return "365+"
        if self._backlog_sort == "forgotten":
            return "forgotten"
        return ""

    @property
    def backlogActiveStatuses(self):
        return self._backlog_active_statuses

    @property
    def forgottenAgeDays(self):
        return self._forgotten_age_days

    @property
    def assigneeBoardNames(self):
        return self._assignee_board_names

    @property
    def assigneeKpi(self):
        return self._assignee_kpi

    @property
    def jiraUrl(self):
        return self._jira_url

    @jiraUrl.setter
    def jiraUrl(self, value):
        self._jira_url = value


def test_module_api_model_properties():
    fake = FakeBackend()
    tm = QObject()
    pbm = QObject()
    psm = QObject()
    bhm = QObject()
    am = QObject()
    csm = QObject()
    api = ModuleApi(fake, tm, pbm, psm, bhm, am, csm)
    assert api.taskModel is tm
    assert api.periodBoardModel is pbm
    assert api.periodSprintModel is psm
    assert api.backlogHealthModel is bhm
    assert api.assigneeModel is am
    assert api.chartSlotsModel is csm


def test_module_api_state_reads_backend():
    fake = FakeBackend()
    api = ModuleApi(fake, QObject(), QObject(), QObject(), QObject(), QObject(), QObject())
    fake._checking = True
    fake._errors_text = "err"
    fake._items_text = "items"
    fake._period_checking = True
    fake._period_status_text = "pstatus"
    fake._period_errors_text = "perr"
    fake._period_from_cache = True
    fake._period_last_refresh = "12:00"
    fake._period_kpi = {"k": "v"}
    fake._selected_team = "Team A"
    fake._backlog_sort = "stale"
    fake._backlog_active_statuses = "|done|open|"
    fake._forgotten_age_days = 14
    fake._assignee_board_names = ["B1", "B2"]
    fake._assignee_kpi = {"a": 1}
    assert api.checking is True
    assert api.errorsText == "err"
    assert api.itemsText == "items"
    assert api.periodChecking is True
    assert api.periodStatusText == "pstatus"
    assert api.periodErrorsText == "perr"
    assert api.periodFromCache is True
    assert api.periodLastRefresh == "12:00"
    assert api.periodKpi == {"k": "v"}
    assert api.selectedTeam == "Team A"
    assert api.backlogSort == "stale"
    assert api.backlogAgeFilter == "stale"
    assert api.backlogActiveStatuses == "|done|open|"
    assert api.forgottenAgeDays == 14
    assert api.assigneeBoardNames == ["B1", "B2"]
    assert api.assigneeKpi == {"a": 1}


def test_module_api_signal_forwarding():
    fake = FakeBackend()
    api = ModuleApi(fake, QObject(), QObject(), QObject(), QObject(), QObject(), QObject())

    pairs = [
        (fake.checkingChanged, api.checkingChanged, "checking"),
        (fake.errorsTextChanged, api.errorsTextChanged, "errorsText"),
        (fake.itemsTextChanged, api.itemsTextChanged, "itemsText"),
        (fake.periodCheckingChanged, api.periodCheckingChanged, "periodChecking"),
        (fake.periodStatusTextChanged, api.periodStatusTextChanged, "periodStatusText"),
        (fake.periodErrorsTextChanged, api.periodErrorsTextChanged, "periodErrors"),
        (fake.periodFromCacheChanged, api.periodFromCacheChanged, "periodFromCache"),
        (fake.periodLastRefreshChanged, api.periodLastRefreshChanged, "periodLastRefresh"),
        (fake.periodKpiChanged, api.periodKpiChanged, "periodKpi"),
        (fake.backlogSortChanged, api.backlogSortChanged, "backlogSort"),
        (fake.backlogAgeFilterChanged, api.backlogAgeFilterChanged, "backlogAgeFilter"),
        (fake.assigneeBoardNamesChanged, api.assigneeBoardNamesChanged, "assigneeBoardNames"),
        (fake.assigneeKpiChanged, api.assigneeKpiChanged, "assigneeKpi"),
    ]

    for backend_signal, _facade_signal, label in pairs:
        fired = set()
        _facade_signal.connect(lambda s=label: fired.add(s))
        backend_signal.emit()
        assert fired == {label}, f"signal {label}: expected {{{label}}}, got {fired}"

    fired = set()
    api.selectedTeamChanged.connect(lambda: fired.add("selectedTeam"))
    api.backlogActiveStatusesChanged.connect(lambda: fired.add("backlogActiveStatuses"))
    api.forgottenAgeDaysChanged.connect(lambda: fired.add("forgottenAgeDays"))
    fake.settingsChanged.emit()
    assert fired == {"selectedTeam", "backlogActiveStatuses", "forgottenAgeDays"}


def test_module_api_refresh_target_calls_backend():
    fake = FakeBackend()
    api = ModuleApi(fake, QObject(), QObject(), QObject(), QObject(), QObject(), QObject())
    api.refreshTarget()
    assert fake.calls == [("refresh", (False,))]


def test_module_api_save_items_calls_backend():
    fake = FakeBackend()
    api = ModuleApi(fake, QObject(), QObject(), QObject(), QObject(), QObject(), QObject())
    api.saveItems("line1\nline2")
    assert fake.calls == [("saveItems", ("line1\nline2",))]


def test_module_api_is_forgotten():
    fake = FakeBackend()
    fake.config = Config(forgotten_age_days=30, active_statuses=["In Progress"])
    api = ModuleApi(fake, QObject(), QObject(), QObject(), QObject(), QObject(), QObject())
    assert api.isForgotten("In Progress", 30) is True
    assert api.isForgotten("in progress", 30) is True
    assert api.isForgotten("To Do", 30) is False
    assert api.isForgotten("In Progress", 29) is False
    fake.config = None
    assert api.isForgotten("In Progress", 30) is False


def test_module_api_refresh_period_calls_backend():
    fake = FakeBackend()
    api = ModuleApi(fake, QObject(), QObject(), QObject(), QObject(), QObject(), QObject())
    api.refreshPeriod()
    assert fake.calls == [("refreshPeriod", ())]


def test_module_api_select_team_calls_backend():
    fake = FakeBackend()
    api = ModuleApi(fake, QObject(), QObject(), QObject(), QObject(), QObject(), QObject())
    api.selectTeam("Team A")
    assert fake.calls == [("setSelectedTeam", ("Team A",))]


def test_module_api_set_backlog_sort_calls_backend():
    fake = FakeBackend()
    api = ModuleApi(fake, QObject(), QObject(), QObject(), QObject(), QObject(), QObject())
    api.setBacklogSort("stale")
    assert fake.calls == [("setBacklogSort", ("stale",))]


def test_module_api_set_assignee_board_filter_calls_backend():
    fake = FakeBackend()
    api = ModuleApi(fake, QObject(), QObject(), QObject(), QObject(), QObject(), QObject())
    api.setAssigneeBoardFilter("Board X")
    assert fake.calls == [("setAssigneeBoardFilter", ("Board X",))]


def test_module_api_open_issue_valid_key():
    fake = FakeBackend()
    fake._jira_url = "https://jira.example.org/"
    opened = []
    api = ModuleApi(fake, QObject(), QObject(), QObject(), QObject(), QObject(), QObject(), open_url=lambda u: opened.append(u.toString()))
    result = api.openIssue("ABC-123")
    assert result is True
    assert opened == ["https://jira.example.org/browse/ABC-123"]


def test_module_api_open_issue_empty_string_false():
    fake = FakeBackend()
    fake._jira_url = "https://jira.example.org"
    opened = []
    api = ModuleApi(fake, QObject(), QObject(), QObject(), QObject(), QObject(), QObject(), open_url=lambda u: opened.append(u.toString()))
    assert api.openIssue("") is False
    assert opened == []


def test_module_api_open_issue_url_rejected():
    fake = FakeBackend()
    fake._jira_url = "https://jira.example.org"
    opened = []
    api = ModuleApi(fake, QObject(), QObject(), QObject(), QObject(), QObject(), QObject(), open_url=lambda u: opened.append(u.toString()))
    assert api.openIssue("https://jira.example.org/browse/ABC-123") is False
    assert opened == []


def test_module_api_open_issue_jql_rejected():
    fake = FakeBackend()
    fake._jira_url = "https://jira.example.org"
    opened = []
    api = ModuleApi(fake, QObject(), QObject(), QObject(), QObject(), QObject(), QObject(), open_url=lambda u: opened.append(u.toString()))
    assert api.openIssue("project = TEST") is False
    assert opened == []


def test_module_api_open_issue_lowercase_key_rejected():
    fake = FakeBackend()
    fake._jira_url = "https://jira.example.org"
    opened = []
    api = ModuleApi(fake, QObject(), QObject(), QObject(), QObject(), QObject(), QObject(), open_url=lambda u: opened.append(u.toString()))
    assert api.openIssue("abc-123") is False
    assert opened == []


def test_module_api_open_issue_empty_jira_url_false():
    fake = FakeBackend()
    fake._jira_url = ""
    opened = []
    api = ModuleApi(fake, QObject(), QObject(), QObject(), QObject(), QObject(), QObject(), open_url=lambda u: opened.append(u.toString()))
    assert api.openIssue("ABC-123") is False
    assert opened == []


def test_module_api_open_sprint_report_invalid_board_id_false():
    fake = FakeBackend()
    fake._jira_url = "https://jira.example.org"
    opened = []
    api = ModuleApi(fake, QObject(), QObject(), QObject(), QObject(), QObject(), QObject(), open_url=lambda u: opened.append(u.toString()))
    assert api.openSprintReport(0, 1) is False
    assert api.openSprintReport(-1, 1) is False
    assert opened == []


def test_module_api_open_sprint_report_invalid_sprint_id_false():
    fake = FakeBackend()
    fake._jira_url = "https://jira.example.org"
    opened = []
    api = ModuleApi(fake, QObject(), QObject(), QObject(), QObject(), QObject(), QObject(), open_url=lambda u: opened.append(u.toString()))
    assert api.openSprintReport(1, 0) is False
    assert api.openSprintReport(1, -1) is False
    assert opened == []


def test_module_api_open_sprint_report_empty_jira_url_false():
    fake = FakeBackend()
    fake._jira_url = ""
    opened = []
    api = ModuleApi(fake, QObject(), QObject(), QObject(), QObject(), QObject(), QObject(), open_url=lambda u: opened.append(u.toString()))
    assert api.openSprintReport(1, 1) is False
    assert opened == []


def test_module_api_open_sprint_report_valid_url():
    fake = FakeBackend()
    fake._jira_url = "https://jira.example.org/"
    opened = []
    api = ModuleApi(fake, QObject(), QObject(), QObject(), QObject(), QObject(), QObject(), open_url=lambda u: opened.append(u.toString()))
    result = api.openSprintReport(42, 99)
    assert result is True
    assert opened == ["https://jira.example.org/secure/RapidBoard.jspa#?rapidView=42&view=reporting&chart=sprintRetrospective&sprint=99"]


def test_save_settings_preserves_config_items():
    from jira_analytics import save_config, load_config
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "jira-analytics.yaml"
        env_path = tmp_path / "jira-analytics.env"
        env_path.write_text("JIRA_URL=https://jira.example.org\nJIRA_USERNAME=user\nJIRA_TOKEN=tok\nJIRA_PASSWORD=\n")
        config = Config()
        config.items = ["issuetype = Epic AND statusCategory != Done"]
        save_config(config_path, config)

        with patch("jira_analytics.DEFAULT_CONFIG", config_path), \
             patch("jira_analytics_gui.DEFAULT_CONFIG", config_path), \
             patch("jira_analytics.DEFAULT_ENV", env_path), \
             patch("jira_analytics_gui.DEFAULT_ENV", env_path), \
             patch("jira_analytics.ensure_user_files", lambda: None):
            backend = Backend(
                None, None, None, None, None, None, None,
            )
            backend.refresh = lambda automatic=False: None
            assert backend.config is not None
            assert backend.config.items == ["issuetype = Epic AND statusCategory != Done"]
            call_count = [0]
            backend.refresh = lambda automatic=False: call_count.__setitem__(0, call_count[0] + 1)
            backend.saveSettings(
                "https://jira.example.org", "user", "tok", "", 7, 2, 60, 15, 30, "Target end", False
            )
            reloaded = load_config(config_path)
            assert reloaded.items == ["issuetype = Epic AND statusCategory != Done"], "config.items should be preserved"
            assert call_count[0] == 0, "refresh should not be triggered by saveSettings"


def test_period_display_date_helpers():
    import jira_analytics_gui
    assert jira_analytics_gui.format_period_date(date(2026, 7, 1)) == "01-07-2026"
    assert jira_analytics_gui.parse_period_date("30-09-2026") == date(2026, 9, 30)
    assert jira_analytics_gui.parse_period_date("01-99-2026") is None
    assert jira_analytics_gui.parse_period_date("2026-09-30") is None
    assert jira_analytics_gui.parse_period_date("1-7-2026") is None
    assert jira_analytics_gui.parse_period_date("") is None


def test_qt_translator_maps_qml_runtime_contexts():
    from translations import GettextTranslatorQt, get_translations_path

    translator = GettextTranslatorQt("jira_qt", get_translations_path(__file__), "ru_RU")
    assert translator.translate("Main", "Settings") == "Настройки"
    assert translator.translate("PeriodAnalyticsTab", "Period analytics") == "Периодная аналитика"
    assert translator.translate("Main.qml", "Settings") == "Настройки"


def test_backend_projects_period_setup_state(tmp_path):
    import jira_analytics
    import jira_analytics_gui
    config_path = tmp_path / "jira-analytics.yaml"
    env_path = tmp_path / "jira-analytics.env"
    save_config(config_path, Config(analytics_enabled=True))
    env_path.write_text("JIRA_URL=https://jira.example.org\nJIRA_USERNAME=user\nJIRA_TOKEN=tok\nJIRA_PASSWORD=\n")

    with patch("jira_analytics.DEFAULT_CONFIG", config_path), \
         patch("jira_analytics_gui.DEFAULT_CONFIG", config_path), \
         patch("jira_analytics.DEFAULT_ENV", env_path), \
         patch("jira_analytics_gui.DEFAULT_ENV", env_path), \
         patch("jira_analytics_gui.ensure_user_files", lambda: None):
        backend = Backend(None, None, None, None, None, None, None)
        start, end = jira_analytics.default_period()
        assert backend.analyticsEnabled is True
        assert backend.periodStart == jira_analytics_gui.format_period_date(start)
        assert backend.periodEnd == jira_analytics_gui.format_period_date(end)
        assert backend.setupRequired is True
        cache_calls = []
        backend.start_period_cache_load = lambda: cache_calls.append("cache")
        backend.loadCachedAnalytics()
        assert cache_calls == []

        notifications = backend.config.notifications_enabled
        backend.toggleNotifications()
        assert backend.config.notifications_enabled is notifications
        assert jira_analytics.load_config(config_path).notifications_enabled is notifications


def test_save_initial_setup_is_atomic(tmp_path):
    from jira_analytics import load_config
    import jira_analytics_gui
    config_path = tmp_path / "jira-analytics.yaml"
    env_path = tmp_path / "jira-analytics.env"
    save_config(config_path, Config(analytics_enabled=True))
    env_path.write_text("JIRA_URL=\nJIRA_USERNAME=\nJIRA_TOKEN=\nJIRA_PASSWORD=\n")

    with patch("jira_analytics.DEFAULT_CONFIG", config_path), \
         patch("jira_analytics_gui.DEFAULT_CONFIG", config_path), \
         patch("jira_analytics.DEFAULT_ENV", env_path), \
         patch("jira_analytics_gui.DEFAULT_ENV", env_path), \
         patch("jira_analytics_gui.ensure_user_files", lambda: None):
        backend = Backend(None, None, None, None, None, None, None)
        calls = []
        backend.refresh = lambda automatic=False: calls.append(("refresh", automatic))
        backend.start_timers = lambda: calls.append(("timers", None))

        assert backend.saveInitialSetup("jira.example.org", "user", "tok", "", "AL-1", True, "", "") is False
        assert env_values(env_path).get("JIRA_USERNAME", "") == ""
        assert load_config(config_path).period_start is None
        assert calls == []

        with patch("jira_analytics_gui.save_config", side_effect=OSError("disk full")):
            assert backend.saveInitialSetup("jira.example.org", "user", "tok", "", "AL-1", True, "01-07-2026", "30-09-2026") is False
        assert env_values(env_path).get("JIRA_USERNAME", "") == ""
        assert load_config(config_path).period_start is None
        assert backend.config.period_start is None
        assert calls == []

        assert backend.saveInitialSetup("jira.example.org", "user", "tok", "", "AL-1", True, "01-07-2026", "30-09-2026") is True
        saved = load_config(config_path)
        assert saved.analytics_enabled is True
        assert saved.period_start == date(2026, 7, 1)
        assert saved.period_end == date(2026, 9, 30)
        assert saved.items == ["AL-1"]
        assert env_values(env_path)["JIRA_USERNAME"] == "user"
        assert backend.setupRequired is False
        assert calls == [("refresh", False), ("timers", None)]

        calls.clear()
        assert backend.saveInitialSetup("jira.example.org", "user", "tok", "", "AL-1", False, "", "") is True
        saved = load_config(config_path)
        assert saved.analytics_enabled is False
        assert saved.period_start is None
        assert saved.period_end is None
        assert calls == [("refresh", False), ("timers", None)]


def test_worker_error_details_distinguishes_auth_failure():
    import jira_analytics
    import jira_analytics_gui

    class Response:
        def __init__(self, status_code, reason):
            self.status_code = status_code
            self.reason = reason

    class HttpError(Exception):
        def __init__(self, status_code, reason, message=""):
            super().__init__(message)
            self.response = Response(status_code, reason)

    class ApiError(Exception):
        def __init__(self, reason):
            super().__init__()
            self.reason = reason

    assert jira_analytics_gui.worker_error_details(HttpError(401, "Unauthorized")) == ("Unauthorized", 401)
    assert jira_analytics_gui.worker_error_details(ApiError(HttpError(401, "Unauthorized"))) == ("Unauthorized", 401)
    assert jira_analytics_gui.worker_error_details(RuntimeError("Unauthorized (401)")) == ("Unauthorized (401)", 401)
    assert jira_analytics_gui.worker_error_details(HttpError(403, "Forbidden")) == ("Forbidden", 403)
    assert jira_analytics_gui.worker_error_details(RuntimeError("Forbidden (403)")) == ("Forbidden (403)", 403)
    assert jira_analytics_gui.worker_error_details(TimeoutError("timed out")) == ("timed out", 0)
    assert jira_analytics.http_status_code(RuntimeError("Issue ABC-403 was not found")) == 0
    assert jira_analytics.http_status_code(RuntimeError("Error loading sprint Demo (403): timed out")) == 0

    failures = []
    worker = jira_analytics_gui.RefreshWorker({}, Config(), False)
    worker.failed.connect(lambda error, status_code: failures.append((error, status_code)))
    with patch("jira_analytics_gui.collect_alert_result", side_effect=HttpError(401, "Unauthorized")):
        worker.run()
    assert failures == [("Unauthorized", 401)]


def test_period_worker_passes_full_refresh_flag():
    import jira_analytics_gui

    results = []
    worker = jira_analytics_gui.PeriodRefreshWorker({}, Config(), full_refresh=True)
    worker.finished.connect(results.append)
    with patch("jira_analytics_gui.collect_period_result", return_value="report") as collect:
        worker.run()
    assert results == ["report"]
    collect.assert_called_once_with({}, worker.config, worker._progress, full_refresh=True)


def test_period_cache_save_worker_reports_write_failure():
    import jira_analytics_gui

    failures = []
    finished = []
    worker = jira_analytics_gui.PeriodCacheSaveWorker(object(), {}, Config())
    worker.failed.connect(lambda error, signature: failures.append((error, signature)))
    worker.finished.connect(lambda: finished.append(True))
    with patch("jira_analytics_gui.save_period_cache", side_effect=OSError("disk full")):
        worker.run()
    assert failures == [("disk full", worker.signature)]
    assert finished == [True]

    succeeded = []
    worker = jira_analytics_gui.PeriodCacheSaveWorker(object(), {}, Config())
    worker.succeeded.connect(succeeded.append)
    with patch("jira_analytics_gui.save_period_cache"):
        worker.run()
    assert succeeded == [worker.signature]


def test_period_cache_save_runs_latest_pending_result():
    import jira_analytics_gui

    backend = Backend.__new__(Backend)
    backend._period_cache_save_worker = object()
    backend._period_cache_save_thread = object()
    backend._period_cache_save_pending = ("new-report", {}, Config())
    backend._shutting_down = False
    calls = []
    backend.start_period_cache_save = lambda result, env=None, config=None: calls.append(result)
    backend.period_cache_save_thread_done()
    assert calls == ["new-report"]

    backend._period_cache_save_pending = ("shutdown-report", {}, Config())
    backend._shutting_down = True
    backend.period_cache_save_thread_done()
    assert calls == ["new-report"]


def test_period_cache_save_queues_while_previous_thread_is_finishing():
    backend = Backend.__new__(Backend)
    backend.env = {}
    backend.config = Config()
    backend._period_cache_save_worker = "old-worker"
    backend._period_cache_save_thread = object()
    backend._period_cache_save_pending = None
    backend.start_period_cache_save("new-report")
    assert backend._period_cache_save_worker == "old-worker"
    assert backend._period_cache_save_pending[0] == "new-report"


def test_period_cache_save_ignores_failure_for_previous_settings(tmp_path):
    import jira_analytics_gui

    config_path = tmp_path / "cache-save-config.yaml"
    env_path = tmp_path / "cache-save.env"
    save_config(config_path, Config(analytics_enabled=False))
    env_path.write_text("JIRA_URL=https://jira.example.org\nJIRA_USERNAME=alice\nJIRA_TOKEN=tok\nJIRA_PASSWORD=\n")
    with patch("jira_analytics_gui.DEFAULT_CONFIG", config_path), \
         patch("jira_analytics_gui.DEFAULT_ENV", env_path), \
         patch("jira_analytics_gui.ensure_user_files", lambda: None), \
         patch("jira_analytics_gui._config_signature", return_value="current"):
        backend = Backend(None, None, None, None, None, None, None)
        backend.period_cache_save_failed("disk full", "previous")
        assert backend._period_errors_text == ""
        with patch("sys.stderr"):
            backend.period_cache_save_failed("disk full", "current")
        assert "disk full" in backend._period_errors_text
        backend.period_cache_save_succeeded("current")
        assert backend._period_errors_text == ""


def test_workers_snapshot_mutable_settings():
    import jira_analytics_gui

    env = {"JIRA_URL": "https://jira.example.org"}
    config = Config(story_points_field="Old field")
    worker = jira_analytics_gui.PeriodRefreshWorker(env, config)
    env["JIRA_URL"] = "https://other.example.org"
    config.story_points_field = "New field"
    assert worker.env["JIRA_URL"] == "https://jira.example.org"
    assert worker.config.story_points_field == "Old field"


def test_backend_can_clear_account_specific_data(tmp_path):
    import jira_analytics_gui

    config_path = tmp_path / "jira-analytics.yaml"
    env_path = tmp_path / "jira-analytics.env"
    save_config(config_path, Config(analytics_enabled=False))
    env_path.write_text("JIRA_URL=https://jira.example.org\nJIRA_USERNAME=alice\nJIRA_TOKEN=tok\nJIRA_PASSWORD=\n")
    with patch("jira_analytics.DEFAULT_CONFIG", config_path), \
         patch("jira_analytics_gui.DEFAULT_CONFIG", config_path), \
         patch("jira_analytics.DEFAULT_ENV", env_path), \
         patch("jira_analytics_gui.DEFAULT_ENV", env_path), \
         patch("jira_analytics_gui.ensure_user_files", lambda: None):
        backend = Backend(None, None, None, None, None, None, None)
        backend._period_kpi = {"boards": 1}
        backend._all_sprints = [{"sprintId": 1}]
        backend._assignee_board_filter = "Old board"
        backend.saveSettings("https://other.example.org", "bob", "new-token", "", 7, 1, 10, 15, 90, "Target end", True)
        assert backend._period_kpi == {}
        assert backend._all_sprints == []
        assert backend._assignee_board_filter == ""


def test_backend_ignores_cache_loaded_for_previous_settings(tmp_path):
    import jira_analytics_gui

    config_path = tmp_path / "jira-analytics.yaml"
    env_path = tmp_path / "jira-analytics.env"
    save_config(config_path, Config(analytics_enabled=False))
    env_path.write_text("JIRA_URL=https://jira.example.org\nJIRA_USERNAME=alice\nJIRA_TOKEN=tok\nJIRA_PASSWORD=\n")
    with patch("jira_analytics.DEFAULT_CONFIG", config_path), \
         patch("jira_analytics_gui.DEFAULT_CONFIG", config_path), \
         patch("jira_analytics.DEFAULT_ENV", env_path), \
         patch("jira_analytics_gui.DEFAULT_ENV", env_path), \
         patch("jira_analytics_gui.ensure_user_files", lambda: None), \
         patch("jira_analytics_gui._config_signature", return_value="current"):
        backend = Backend(None, None, None, None, None, None, None)
        backend.period_cache_loaded(object(), "previous")
        assert backend._period_kpi == {}


def test_refresh_failed_returns_to_setup_only_for_auth(tmp_path):
    from contextlib import redirect_stderr
    from io import StringIO

    import jira_analytics_gui

    class Timer:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    config_path = tmp_path / "jira-analytics.yaml"
    env_path = tmp_path / "jira-analytics.env"
    save_config(config_path, Config(analytics_enabled=False))
    env_path.write_text("JIRA_URL=https://jira.example.org\nJIRA_USERNAME=user\nJIRA_TOKEN=tok\nJIRA_PASSWORD=\n")

    with patch("jira_analytics.DEFAULT_CONFIG", config_path), \
         patch("jira_analytics_gui.DEFAULT_CONFIG", config_path), \
         patch("jira_analytics.DEFAULT_ENV", env_path), \
         patch("jira_analytics_gui.DEFAULT_ENV", env_path), \
         patch("jira_analytics_gui.ensure_user_files", lambda: None):
        backend = Backend(None, None, None, None, None, None, None)
        interval_calls = []
        backend.start_interval_timer = lambda: interval_calls.append("start")
        backend.refresh_failed("timed out", 0)
        assert backend.setupRequired is False
        assert backend.statusText == "Connection error: timed out"
        assert interval_calls == ["start"]

        interval_calls = []
        backend.start_interval_timer = lambda: interval_calls.append("start")
        backend.task_model = type("TaskModel", (), {"set_items": lambda self, items: None})()
        result = type("Result", (), {"alerts": [], "errors": []})()
        backend.refresh_done(result, False)
        assert interval_calls == ["start"]

        backend.first_timer = Timer()
        backend.interval_timer = Timer()
        backend.refresh_failed("Forbidden", 403)
        assert backend.setupRequired is False
        assert backend.statusText == "Jira API access forbidden: Forbidden"
        assert backend._api_forbidden is True
        assert backend.first_timer.stopped
        assert backend.interval_timer.stopped

        interval_calls = []
        backend.start_interval_timer = lambda: interval_calls.append("start")
        backend.refresh_done(result, True)
        assert interval_calls == []

        backend.first_timer = Timer()
        backend.interval_timer = Timer()
        stderr = StringIO()
        with redirect_stderr(stderr):
            backend.refresh_failed("Unauthorized", 401)
        assert backend.setupRequired is True
        assert backend.statusText == "Authentication failed: Unauthorized"
        assert stderr.getvalue().strip() == "Authentication failed: Unauthorized"
        assert backend.first_timer.stopped
        assert backend.interval_timer.stopped

        backend.refresh = lambda automatic=False: None
        backend.start_timers = lambda: None
        assert backend.saveInitialSetup("jira.example.org", "user", "new-token", "", "AL-1", False, "", "") is True
        assert backend.setupRequired is False


def test_onboarding_qml_declares_layout_and_date_constraints():
    qml = (Path(__file__).with_name("qml") / "Main.qml").read_text(encoding="utf-8")
    assert "minimumHeight: backend && backend.setupRequired ? Math.max(loginContent.implicitHeight, periodContent.implicitHeight) + 104 : 500" in qml
    assert "setupCard.height + 48" not in qml
    assert "function showSetupStep(step)" in qml
    assert "id: setupTransition" in qml
    assert "Behavior on height" in qml
    assert "onClicked: root.showSetupStep(1)" in qml
    assert "onClicked: root.showSetupStep(0)" in qml
    assert "onAuthenticationFailed: root.showSetupStep(0)" in qml
    assert qml.count('inputMask: "99-99-9999"') == 2
    assert qml.count("validator: RegExpValidator {") == 2
    assert qml.count('regExp: /(0[1-9]|[12][0-9]|3[01])-(0[1-9]|1[0-2])-[0-9]{4}/') == 2
    assert "RegularExpressionValidator" not in qml
    assert "enabled: setupAnalyticsEnabled.checked" in qml
    assert "setupPeriodStart.acceptableInput && setupPeriodEnd.acceptableInput" in qml
    assert 'setupAnalyticsEnabled.checked ? setupPeriodStart.text : ""' in qml
    assert 'setupAnalyticsEnabled.checked ? setupPeriodEnd.text : ""' in qml
    assert "id: backSetupButton" in qml
    assert "id: fullRefreshButton" in qml
    assert 'text: qsTr("Full refresh history")' in qml
    assert "root.saveSettings()" in qml
    assert "backend.refreshPeriod(true)" in qml
    assert "!backend.checking && !backend.periodChecking" in qml


def test_routing_qml_uses_observed_categories():
    root = Path(__file__).parent / "modules"
    period = (root / "period_analytics" / "PeriodAnalyticsTab.qml").read_text(encoding="utf-8")
    assignees = (root / "assignees" / "AssigneesTab.qml").read_text(encoding="utf-8")
    charts = (root / "charts" / "ChartsTab.qml").read_text(encoding="utf-8")

    assert 'qsTr("Reassigned")' not in period + assignees + charts
    assert "completedLater" in period + assignees + charts
    assert "observedOtherBoard" in period + assignees + charts
    assert "notObserved" in period + assignees + charts
    assert "unknown" in period + assignees + charts
    assert "routeCategory" in assignees


def test_period_qml_displays_removed_count():
    qml = (Path(__file__).parent / "modules" / "period_analytics" / "PeriodAnalyticsTab.qml").read_text(encoding="utf-8")

    assert "api.periodKpi.removed ||" in qml
    assert qml.count(".arg(removed)") == 2
    assert "removed %12" in qml
    assert "removed %12 SP" not in qml


if __name__ == "__main__":
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        test_chart_slots_sorting()
        test_chart_slots_aggregation()
        test_chart_slots_labels_global()
        test_chart_slots_labels_cross_team()
        test_chart_slots_lead_time_percentiles_use_correct_fields()
        test_period_label_for_sprint_uses_two_thirds_rule()
        test_assignee_external_owner_core()
        test_assignee_external_owner_cache()
        test_issue_key_from_text()
        test_source_kind()
        test_normalize_jira_url()
        test_items_from_text()
        test_sprint_overlaps_period()
        test_summarize_sprint_counts_not_done_and_unestimated()
        test_summarize_sprint_lead_time_uses_changelog_fallback()
        test_summarize_sprint_lead_time_prefers_resolutiondate()
        test_summarize_sprint_counts_removed_issues_separately()
        test_sprint_report_removed_issues_reads_punted_issues()
        test_period_report_deduplicates_portfolio_totals()
        test_is_done_uses_configured_statuses()
        test_is_done_uses_default_russian_statuses()
        test_is_forgotten_threshold_boundary()
        test_is_forgotten_status_case_insensitive()
        test_is_forgotten_requires_active_status()
        test_is_forgotten_active_statuses_case_insensitive()
        test_flow_efficiency_splits_active_waiting_and_done_time()
        test_flow_efficiency_counts_unknown_as_waiting()
        test_flow_efficiency_ignores_transitions_after_closed_sprint_cutoff()
        test_period_report_exposes_flow_efficiency()
        test_period_report_flow_efficiency_uses_historical_done_filter()
        test_load_config_mapping()
        test_load_config_gui_fields()
        test_save_config_preserves_items_and_toggles_notifications(tmp_path)
        test_load_period_config()
        test_analytics_enabled_by_default()
        test_analytics_enabled_rejects_string()
        test_config_period_rejects_non_iso_suffix()
        test_default_period_is_current_quarter()
        test_enabled_analytics_allows_initial_empty_period_and_boards()
        test_period_config_rejects_incomplete_pair()
        test_yaml_example_contains_all_config_fields()
        test_analytics_disabled_config()
        test_period_config_validation()
        test_period_config_allows_empty_boards_when_enabled()
        test_save_config_preserves_period_fields(tmp_path)
        test_load_config_per_board_statuses(tmp_path)
        test_board_statuses_empty_list_preserved(tmp_path)
        test_resolve_board_statuses_override_and_fallback()
        test_classify_status_groups_and_exact_name()
        test_build_period_report_uses_per_board_done_statuses()
        test_config_signature_includes_board_statuses()
        test_parse_datetime_normalizes_offsets_to_utc()
        test_read_write_include_active_sprints(tmp_path)
        test_include_active_sprints_rejects_string()
        test_save_env_and_env_values(tmp_path)
        test_env_values_keeps_empty_values(tmp_path)
        test_ensure_user_files_copies_config_without_overwriting(tmp_path)
        test_ensure_user_files_falls_back_when_system_env_unreadable(tmp_path)
        test_collect_alert_result_deduplicates_by_key()
        test_collect_alert_result_sorts_by_days()
        test_collect_alert_result_keeps_source_errors()
        test_collect_alert_result_uses_default_items_when_empty()
        test_collect_period_result_uses_agile_api_with_partial_failure()
        test_partial_board_failure_makes_cross_board_routing_unknown()
        test_collect_period_result_respects_include_active_sprints()
        test_future_sprint_is_routing_only_and_not_reported()
        test_dump_sanitizes_by_default_and_raw_flag_keeps_raw(tmp_path)
        test_collect_period_result_progress_callback()
        test_compute_spillover_detects_carryover()
        test_compute_spillover_completed()
        test_compute_spillover_reassigned()
        test_compute_spillover_backlog()
        test_build_period_report_routes_current_active_with_exact_status()
        test_compute_spillover_observes_other_board_and_keeps_legacy_reassigned()
        test_compute_spillover_distinguishes_not_observed_from_unknown()
        test_active_sprint_routing_is_unknown_even_when_task_is_observed_later()
        test_future_sprint_can_start_on_origin_end_date()
        test_completed_later_is_outcome_and_survives_reopening()
        test_collect_period_filters_current_done_from_broad_backlog_jql()
        test_collect_period_builds_team_scope_and_waiting_only_backlog_health()
        test_collect_period_missing_sprint_dates_makes_routing_unknown()
        test_load_config_backlog_jql(tmp_path)
        test_load_config_board_without_backlog_jql()
        test_single_sprint_classifies_backlog()
        test_load_config_backlog_jql_must_be_string()
        test_historical_resolve_not_done()
        test_historical_resolve_done_before_cutoff()
        test_historical_done_in_sprint_summary()
        test_active_sprint_uses_current_status()
        test_historical_spillover_from_historical_not_done()
        test_historical_regression_multiple_transitions()
        test_historical_same_day_transition_after_cutoff()
        test_aggregate_respects_historical_done()
        test_aggregate_closed_false_active_done_counts_done()
        test_aggregate_changed_story_points_dont_exceed_100_pct()
        test_assignee_stats_basic()
        test_assignee_stats_uses_historical_assignee_for_closed_sprint()
        test_assignee_spillover()
        test_assignee_empty_sprint()
        test_assignee_items_backlog_breakdown()
        test_assignee_items_spillover_breakdown()
        test_assignee_items_reassigned_breakdown()
        test_changelog_cache_ttl_config(tmp_path)
        test_changelog_cache_namespace_and_full_refresh_bypass(tmp_path)
        test_changelog_cache_write_failure_keeps_fetched_history()
        test_clear_changelog_cache_removes_namespaced_files(tmp_path)
        test_atomic_cache_write_failure_preserves_previous_file(tmp_path)
        test_period_source_cache_save_failure_keeps_report_available()
        test_period_cache_preserves_and_invalidates_by_config_signature(tmp_path)
        test_period_cache_rejects_old_routing_shape(tmp_path)
        test_period_cache_preserves_routing_detail_fields(tmp_path)
        test_period_cache_rejects_old_status_shape(tmp_path)
        test_period_source_cache_roundtrip_and_identity(tmp_path)
        test_collect_period_result_reuses_closed_sprint_cache_and_full_refresh_bypasses_it(tmp_path)
        test_full_refresh_uses_cached_closed_sprint_when_network_fails(tmp_path)
        test_full_refresh_field_lookup_failure_does_not_replace_source_cache(tmp_path)
        test_period_source_cache_does_not_store_sprints_without_points_field(tmp_path)
        test_collect_period_source_cache_refreshes_active_and_falls_back_on_board_error(tmp_path)
        test_collect_period_result_propagates_forbidden_instead_of_saving_partial_report(tmp_path)
        test_period_source_cache_retries_only_missing_removed_issues(tmp_path)
        test_reopened_sprint_is_fetched_again_after_it_closes(tmp_path)
        test_load_config_short_list()
        test_alert_kind()
        test_alert_issue_reads_assignee_display_name()
        test_alert_issue_uses_fallback_assignee()
        test_parse_date_invalid_value()
        test_build_notify_command_uses_standard_notifications()
        test_notify_without_gdbus()
        test_cli_help_exits()
        test_compute_backlog_health()
        test_compute_backlog_health_filters_done()
        test_compute_backlog_health_empty()
        test_discover_modules_two_valid_sorted()
        test_discover_modules_same_order_sorted_by_id()
        test_discover_missing_builtin_dir_returns_error()
        test_discover_missing_user_dir_not_error()
        test_discover_malformed_json_skipped()
        test_discover_missing_required_field_skipped()
        test_discover_wrong_field_types_skipped()
        test_discover_wrong_api_version_skipped()
        test_discover_missing_qml_skipped()
        test_discover_absolute_qml_path_skipped()
        test_discover_qml_traversal_skipped()
        test_discover_symlink_outside_skipped()
        test_discover_duplicate_builtin_keeps_first()
        test_discover_user_duplicate_id_not_replace_builtin()
        test_discover_two_user_duplicate_keeps_first_stable()
        test_discover_dotdot_hidden_qml_accepted()
        test_discover_non_utf8_manifest_skipped()
        test_discover_cyclic_symlink_skipped()
        test_discover_permission_error_iterdir_returns_root_error()
        test_discover_recursion_error_skipped()
        test_discover_invalid_id_rejected()
        test_discover_unknown_fields_ignored()
        test_builtin_manifests_discovery()
        test_module_api_model_properties()
        test_module_api_state_reads_backend()
        test_module_api_signal_forwarding()
        test_module_api_refresh_target_calls_backend()
        test_module_api_save_items_calls_backend()
        test_module_api_is_forgotten()
        test_module_api_refresh_period_calls_backend()
        test_module_api_select_team_calls_backend()
        test_module_api_set_backlog_sort_calls_backend()
        test_module_api_set_assignee_board_filter_calls_backend()
        test_module_api_open_issue_valid_key()
        test_module_api_open_issue_empty_string_false()
        test_module_api_open_issue_url_rejected()
        test_module_api_open_issue_jql_rejected()
        test_module_api_open_issue_lowercase_key_rejected()
        test_module_api_open_issue_empty_jira_url_false()
        test_module_api_open_sprint_report_invalid_board_id_false()
        test_module_api_open_sprint_report_invalid_sprint_id_false()
        test_module_api_open_sprint_report_empty_jira_url_false()
        test_module_api_open_sprint_report_valid_url()
        test_save_settings_preserves_config_items()
        test_period_display_date_helpers()
        test_qt_translator_maps_qml_runtime_contexts()
        test_backend_projects_period_setup_state(tmp_path)
        test_save_initial_setup_is_atomic(tmp_path)
        test_worker_error_details_distinguishes_auth_failure()
        test_period_worker_passes_full_refresh_flag()
        test_period_cache_save_worker_reports_write_failure()
        test_period_cache_save_runs_latest_pending_result()
        test_period_cache_save_queues_while_previous_thread_is_finishing()
        test_period_cache_save_ignores_failure_for_previous_settings(tmp_path)
        test_workers_snapshot_mutable_settings()
        test_backend_can_clear_account_specific_data(tmp_path)
        test_backend_ignores_cache_loaded_for_previous_settings(tmp_path)
        test_refresh_failed_returns_to_setup_only_for_auth(tmp_path)
        test_onboarding_qml_declares_layout_and_date_constraints()
        test_routing_qml_uses_observed_categories()
        test_period_qml_displays_removed_count()
