"""Version 2.1: rules written in plain words become checks run with each profile."""
from datetime import datetime, timedelta, timezone

from pdcat import review, rulechecks, suggestions

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"
COLS = ["Units", "OrderId", "Status", "SaleDate"]


def test_parse_rules():
    p = rulechecks.parse_rule
    assert p("Units is never negative", COLS) == {"check": "min", "column": "Units", "value": 0.0, "strict": False}
    assert p("units is always positive", COLS)["strict"] is True
    assert p("OrderId is unique", COLS)["check"] == "unique"
    assert p("OrderId has no duplicates", COLS)["check"] == "unique"
    assert p("Status is never blank", COLS)["check"] == "not_null"
    assert p("Units is between 0 and 500", COLS) == {"check": "range", "column": "Units", "low": 0.0, "high": 500.0}
    assert p("Units is at most 1,000", COLS)["value"] == 1000.0
    assert p("Status is one of Open, Closed, Held", COLS)["values"] == ["Open", "Closed", "Held"]
    assert p("Status is Open or Closed", COLS)["values"] == ["Open", "Closed"]
    assert p("has at least 1000 rows", COLS)["check"] == "min_rows"
    r = p("loaded daily by 7am", COLS)
    assert (r["check"], r["days"], r["by_hour"]) == ("fresh", 1, 7)
    assert p("refreshed every 3 days", COLS)["days"] == 3
    assert p("the team likes this table", COLS)["check"] == "text"


def _profile(catalogue, rows=10, min_units="-2", nulls=0, top=None, max_date=None):
    catalogue.save_profile(SALES, [
        {"name": "Units", "rows": rows, "nulls": 0, "distinct": 8, "min": min_units, "max": "40"},
        {"name": "Status", "rows": rows, "nulls": nulls, "distinct": 2, "top": top or [["Open", 6], ["Closed", 4]]},
        {"name": "SaleDate", "data_type": "date", "rows": rows, "nulls": 0, "distinct": 3,
         "min": "2020-01-01", "max": max_date or "2020-01-03"},
    ], "full table")


def test_checks_run_on_the_profile_and_go_to_review(catalogue):
    assert rulechecks.results_for(catalogue, SALES) == []
    uid = rulechecks.add_check(catalogue, SALES, "Units is never negative")
    rulechecks.add_check(catalogue, SALES, "Status is one of Open, Closed")
    rulechecks.add_check(catalogue, SALES, "loaded daily")
    _profile(catalogue)
    states = {r["text"]: r["result"]["state"] for r in rulechecks.results_for(catalogue, SALES)}
    assert states == {"Units is never negative": "fail", "Status is one of Open, Closed": "pass",
                      "loaded daily": "fail"}
    review.refresh_tasks(catalogue)
    assert [t for t in review.open_tasks(catalogue, rule="rule_check") if t["asset_key"] == SALES]
    today = datetime.now(timezone.utc).date().isoformat()
    _profile(catalogue, min_units="0", max_date=today)
    assert all(r["result"]["state"] == "pass" for r in rulechecks.results_for(catalogue, SALES))
    assert rulechecks.review_tasks(catalogue, _task) == []
    rulechecks.delete_check(catalogue, uid)
    assert len(rulechecks.checks_for(catalogue, SALES)) == 2


def _task(rule, key, title, detail="", version=""):
    return review.Task(f"{rule}:{key}:{version}", rule, key, title, detail, 1.0)


def test_one_of_beyond_top_values_and_not_profiled(catalogue):
    rulechecks.add_check(catalogue, SALES, "Status is one of Open, Closed")
    assert rulechecks.results_for(catalogue, SALES)[0]["result"]["state"] == "unknown"
    _profile(catalogue, top=[["Open", 6], ["Void", 1]])
    r = rulechecks.results_for(catalogue, SALES)[0]["result"]
    assert r["state"] == "fail" and "Void" in r["detail"]


def test_fresh_uses_file_modified_time(catalogue):
    old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    check = {"key": "x", "rule": rulechecks.parse_rule("refreshed weekly", [])}
    assert rulechecks.run_check(catalogue, check, [], {"attrs": {"modified": old}})["state"] == "pass"
    check = {"key": "x", "rule": rulechecks.parse_rule("loaded daily", [])}
    assert rulechecks.run_check(catalogue, check, [], {"attrs": {"modified": old}})["state"] == "fail"


def test_rules_in_notes_become_suggestions(catalogue):
    catalogue.add_note(SALES + "#Units", "Units is never negative. Returns are in another table.")
    _profile(catalogue)
    assert rulechecks.find_rules(catalogue) >= 1
    [s] = suggestions.open_suggestions(catalogue, "check", subject=SALES)
    assert s["payload"]["text"] == "Units is never negative"
    suggestions.accept(catalogue, s["id"])
    assert rulechecks.checks_for(catalogue, SALES)[0]["rule"]["check"] == "min"
    rulechecks.find_rules(catalogue)
    assert not suggestions.open_suggestions(catalogue, "check", subject=SALES)
