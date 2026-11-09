"""Version 2.33: weekly profiling rules pinned to a day, a rule as your approval, and when each table is next due."""
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import SRC  # noqa: F401

from pdcat import profilerules as P
from pdcat.pacing import queue

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    """Days are local days; pin "local" to UTC so the dates below hold on any machine (works on Windows too)."""
    monkeypatch.setattr(P, "_local", lambda t: t.astimezone(timezone.utc))


def _asked(cat, when: str) -> None:
    cat.conn.execute("UPDATE profile_rules SET last_requested = ? WHERE asset_key = ?", (when, SALES))
    cat.conn.commit()


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def test_next_due_weekly_every_7_days_and_pinned_to_monday():
    wed = "2026-11-04T10:00:00+00:00"
    assert P.next_due("weekly", wed) == at("2026-11-11T10:00:00")
    assert P.next_due("weekly", wed, day=0) == at("2026-11-09T00:00:00")       # the Monday after
    assert P.next_due("weekly", "2026-11-09T07:00:00+00:00", day=0) == at("2026-11-16T00:00:00")  # profiled Monday
    assert P.next_due("weekly", None, day=0) <= at("2000-01-01T00:00:00")      # never profiled: due now
    assert P.next_due("never", wed) is None and P.next_due("once", wed) is None
    assert P.next_due("monthly", wed, day=0) == at("2026-12-04T10:00:00")      # the day only affects weekly


def test_monday_rule_is_due_on_monday_and_catches_up_a_missed_one(catalogue):
    P.set_rule(catalogue, [SALES], "weekly")
    _asked(catalogue, "2026-11-04T10:00:00+00:00")                              # last done on a Wednesday
    assert P.due(catalogue, at("2026-11-09T07:00:00")) == []                    # every 7 days: not yet
    catalogue.set_setting(P.WEEKLY_DAY, 0)
    assert P.due(catalogue, at("2026-11-08T23:59:00")) == []                    # Sunday
    assert P.due(catalogue, at("2026-11-09T07:00:00")) == [SALES]              # Monday
    assert P.due(catalogue, at("2026-11-10T07:00:00")) == [SALES]              # Monday missed: Tuesday catches up
    _asked(catalogue, "2026-11-09T07:00:00+00:00")
    assert P.due(catalogue, at("2026-11-09T18:00:00")) == []                    # once a Monday
    assert P.due(catalogue, at("2026-11-16T06:00:00")) == [SALES]


def test_rule_as_approval_queues_tables_approved(catalogue):
    P.set_rule(catalogue, [SALES], "weekly")
    catalogue.set_setting(P.RULE_APPROVES, True)
    assert P.request_due(catalogue) == 1
    assert {q["asset_key"]: q["state"] for q in queue(catalogue)}[SALES] == "approved"


def test_rule_waits_for_approval_by_default(catalogue):
    P.set_rule(catalogue, [SALES], "weekly")
    assert P.request_due(catalogue) == 1
    assert {q["asset_key"]: q["state"] for q in queue(catalogue)}[SALES] == "queued"


def test_profiling_list_says_when_next_due(catalogue):
    P.set_rule(catalogue, [SALES], "weekly")
    row = next(r for r in P.profiling_list(catalogue) if r["key"] == SALES)
    assert row["next_due"] == "now"
    P.request_due(catalogue)
    row = next(r for r in P.profiling_list(catalogue) if r["key"] == SALES)
    assert row["next_due"] == "in the queue"
    catalogue.conn.execute("DELETE FROM profile_queue")
    catalogue.conn.commit()
    row = next(r for r in P.profiling_list(catalogue) if r["key"] == SALES)
    assert row["next_due"] not in ("", "now", "in the queue")                   # a date a week on
    others = [r for r in P.profiling_list(catalogue) if r["key"] != SALES]
    assert all(r["next_due"] == "" for r in others)


def test_profiling_page_timing_and_queue_now(catalogue, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from test_app import goto
    monkeypatch.setenv("PDCAT_DB", str(catalogue.path))
    app = AppTest.from_file(str(Path(__file__).parents[1] / "src" / "pdcat" / "app.py"), default_timeout=30)
    app.run()
    goto(app, "Profiling")
    assert not app.exception
    app.selectbox(key="prof-weekday").set_value(0).run()
    assert not app.exception
    app.checkbox(key="prof-rule-approves").check().run()
    assert not app.exception
    assert P.weekly_day(catalogue) == 0 and P.rule_approves(catalogue)
    assert any("Weekly on Monday" in (e.label or "") for e in app.expander)
