"""Version 2: search details, library sorting, sensitive columns, review bulk actions, history, type checks."""
import json

import pandas as pd
import pytest

from pdcat import curation

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"
STORE = "sql://warehouse/forecastdb/dbo.store"


# ---- search ------------------------------------------------------------------

def test_search_shows_where_it_matched_and_the_matching_columns(catalogue):
    hits = catalogue.search("units")
    top = [dict(h) for h in hits if h["key"] == SALES][0]
    assert "**" in top["snippet"]
    cols = catalogue.matching_columns(SALES, "units")
    assert [c["name"] for c in cols] == ["Units"]
    assert cols[0]["description"] == "Units sold, net of returns"


def test_columns_only_search(catalogue):
    rows = catalogue.search_columns("region")
    assert [(r["item"], r["column_name"]) for r in rows] == [("dbo.Store", "Region")]


def test_deprecated_items_sink_in_search(catalogue):
    first = catalogue.search("store")[0]["key"]
    curation.set_item_info(catalogue, first, lifecycle="deprecated")
    assert catalogue.search("store")[-1]["key"] == first


def test_your_column_description_wins_and_is_searchable(catalogue):
    curation.set_column_info(catalogue, STORE, "Region", description="Sales region, from the store master")
    assert curation.column_description(catalogue, STORE, "Region") == "Sales region, from the store master"
    assert any(h["key"] == STORE for h in catalogue.search("master"))


def test_describe_once_apply_everywhere(catalogue):
    catalogue.conn.execute("INSERT INTO columns (asset_key, name, ordinal, data_type) VALUES (?, 'storeid', 9, 'int')",
                           (STORE,))
    n = curation.describe_everywhere(catalogue, "StoreId", "Store number from the store master")
    assert n == 2
    assert {c["description"] for c in curation.same_named_columns(catalogue, "storeid")} == \
        {"Store number from the store master"}


# ---- sensitive values ---------------------------------------------------------

def test_sensitive_column_values_are_never_kept(catalogue):
    catalogue.save_profile(STORE, [{"name": "Region", "rows": 40, "nulls": 0, "distinct": 4, "min": "East",
                                    "max": "West", "top": [["North", 12]]}], "test")
    curation.set_column_info(catalogue, STORE, "Region", sensitive=True)
    p = catalogue.profile(STORE)[0]
    assert p["min_value"] is None and json.loads(p["top_values"]) == [] and p["distinct_count"] == 4
    # A later profile doesn't bring the values back.
    catalogue.save_profile(STORE, [{"name": "Region", "rows": 40, "nulls": 0, "min": "East", "max": "West",
                                    "top": [["North", 12]]}], "test")
    assert catalogue.profile(STORE)[0]["max_value"] is None


def test_sensitive_names_are_suggested(catalogue):
    catalogue.conn.execute("INSERT INTO columns (asset_key, name, ordinal, data_type) VALUES (?, 'CustomerEmail', 5, "
                           "'nvarchar(200)')", (STORE,))
    names = [s["name"] for s in curation.sensitive_suggestions(catalogue)]
    assert names == ["CustomerEmail"]
    curation.dismiss_sensitive_suggestion(catalogue, STORE, "CustomerEmail")
    assert curation.sensitive_suggestions(catalogue) == []


# ---- libraries -----------------------------------------------------------------

def test_third_party_packages_are_sorted_out(catalogue, tmp_path):
    from pdcat.config import PythonSource
    from pdcat.libclass import HOME, OWN, STDLIB, THIRD, UNKNOWN, Classifier, packages_used
    from pdcat.scan_python import scan_project

    proj = tmp_path / "ml"
    proj.mkdir()
    (proj / "requirements.txt").write_text("lightgbm==4.3\nsomethingobscure\n")
    (proj / "train.py").write_text("import os\nimport lightgbm as lgb\nimport somethingobscure\nimport mystery\n"
                                   "from features import load\n")
    scan_project(catalogue, PythonSource(name="ml", path=proj))
    clf = Classifier(catalogue)
    assert clf.classify("lightgbm", "ml") == THIRD
    assert clf.classify("somethingobscure", "ml") == THIRD  # declared, though not installed here
    assert clf.classify("os", "ml") == STDLIB
    assert clf.classify("train", "ml") == OWN
    assert clf.classify("features", "ml") == HOME  # lives in the forecasting project
    assert clf.classify("mystery", "ml") == UNKNOWN
    rows = {r["package"]: r for r in packages_used(catalogue)}
    assert rows["lightgbm"]["purpose"] == "gradient boosting models"
    assert rows["lightgbm"]["declared in"] == "ml"
    from pdcat.codemap import uncatalogued_libraries
    assert "lightgbm" not in {m["name"] for m in uncatalogued_libraries(catalogue)}


# ---- review --------------------------------------------------------------------

def test_review_counts_and_bulk_actions(catalogue):
    from pdcat.review import count_open, ensure_fresh, open_tasks, set_tasks_status
    ensure_fresh(catalogue)
    tasks = open_tasks(catalogue, limit=1000)
    assert count_open(catalogue) == len(tasks) > 2
    set_tasks_status(catalogue, [t["id"] for t in tasks[:2]], "dismissed")
    assert count_open(catalogue) == len(tasks) - 2
    rev = catalogue.revision()
    ensure_fresh(catalogue)  # nothing changed: no recompute, no revision bump
    assert catalogue.revision() == rev


# ---- change history ------------------------------------------------------------

def test_rescan_records_column_and_row_changes(catalogue):
    from conftest import FAKE_DB, SRC

    from pdcat.history import history
    from pdcat.scan_sqlserver import ingest

    db = json.loads(json.dumps(FAKE_DB))
    db["objects"][0]["row_count"] = 1500
    db["columns"] = [c for c in db["columns"] if c["column_name"] != "Units"]
    db["columns"][0]["type_name"] = "bigint"
    db["columns"].append({"schema_name": "dbo", "object_name": "SalesDaily", "column_name": "Revenue", "column_id": 3,
                          "type_name": "money", "max_length": 8, "precision": 19, "scale": 4, "is_nullable": True,
                          "description": None})
    with catalogue.scan(SRC.name, prefix="sql://warehouse/forecastdb"):
        ingest(catalogue, SRC, "ForecastDB", db)
    changes = {(h["change"], h["detail"]) for h in history(catalogue, SALES)}
    assert ("column removed", "Units decimal(18,2)") in changes
    assert ("column added", "Revenue money") in changes
    assert ("column retyped", "StoreId int → bigint") in changes
    assert ("row count", "1,200 → 1,500") in changes


def test_note_flagged_when_its_column_changes(catalogue):
    from conftest import FAKE_DB, SRC

    from pdcat.scan_sqlserver import ingest
    from pdcat.staleness import stale_reasons

    note = catalogue.add_note(f"{SALES}#Units", "Net of returns since 2024.")
    assert stale_reasons(catalogue, SALES) == {}
    db = json.loads(json.dumps(FAKE_DB))
    db["columns"] = [c for c in db["columns"] if c["column_name"] != "Units"]
    with catalogue.scan(SRC.name):
        ingest(catalogue, SRC, "ForecastDB", db)
    assert "column Units is no longer there" in stale_reasons(catalogue, SALES)[note]
    catalogue.archive_note(note)
    assert stale_reasons(catalogue, SALES) == {}


def test_scanning_one_database_leaves_the_others_alone(catalogue):
    from conftest import FAKE_DB, SRC

    from pdcat.scan_sqlserver import ingest
    with catalogue.scan(SRC.name, prefix="sql://warehouse/otherdb"):
        ingest(catalogue, SRC, "OtherDB", FAKE_DB)
    with catalogue.scan(SRC.name, prefix="sql://warehouse/otherdb"):
        ingest(catalogue, SRC, "OtherDB", {"objects": [], "columns": []})
    assert catalogue.get_asset(SALES)["status"] == "active"
    assert catalogue.get_asset("sql://warehouse/otherdb/dbo.salesdaily")["status"] == "missing"


def test_freshness_from_the_main_date_column(catalogue):
    from pdcat.history import freshness
    catalogue.save_profile(SALES, [{"name": "SaleDate", "data_type": "date", "rows": 10, "nulls": 0,
                                    "min": "2020-01-01", "max": "2021-01-31"}], "test")
    f = freshness(catalogue, SALES)
    assert f["column"] == "SaleDate" and f["latest"] == "2021-01-31" and f["stale"]
    curation.set_item_info(catalogue, SALES, refresh="daily")
    assert freshness(catalogue, SALES)["limit_days"] == 3


# ---- profiles: range charts and data types ---------------------------------------

def test_numeric_and_date_ranges():
    from pdcat.profiling import profile_dataframe
    df = pd.DataFrame({"units": range(100), "day": pd.date_range("2026-01-01", periods=100)})
    p = {c["name"]: c for c in profile_dataframe(df)}
    assert len(p["units"]["histogram"]["counts"]) == 20 and sum(p["units"]["histogram"]["counts"]) == 100
    assert p["units"]["percentiles"]["p50"] == 49.5
    assert p["day"]["histogram"]["labels"][:2] == ["2026-01", "2026-02"]


@pytest.mark.parametrize("values,declared,code", [
    (["2026-01-03", "2026-02-04", "2026-03-05", "2026-04-06", "2026-05-07"], "varchar(20)", "text_dates"),
    (["12", "13.5", "1,200", "7", "8"], "varchar(10)", "text_numbers"),
    ([20260101, 20260102, 20260215, 20251231, 20240229], "int", "int_yyyymmdd"),
    ([1.0, 2.0, 3.0, 4.0, 5.0], "float", "float_whole"),
    (["Y", "N", "Y", "Y", "N"], "char(1)", "yes_no"),
    (["ab", "cd", "ef", "gh", "ij"], "varchar(255)", "wide_text"),
])
def test_wrong_data_types_are_spotted(values, declared, code):
    from pdcat.profiling import type_hints
    assert code in [h["code"] for h in type_hints(pd.Series(values), declared)]


def test_midnight_datetimes():
    from pdcat.profiling import type_hints
    s = pd.Series(pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"]))
    assert [h["code"] for h in type_hints(s, "datetime")] == ["midnight"]


def test_sql_profile_uses_a_sample_for_big_tables():
    from pdcat.profiling import sql_profile_queries
    agg, _ = sql_profile_queries("dbo", "Big", [{"name": "x", "data_type": "int"}], 1000, sample_for_aggregates=5000)
    assert "FROM (SELECT TOP (5000) * FROM [dbo].[Big] WITH (NOLOCK)) AS s" in agg


# ---- relationships -----------------------------------------------------------------

def test_joins_in_sql():
    from pdcat.relationships import joins_in_sql
    sql = "SELECT * FROM dbo.SalesDaily s JOIN dbo.Store st ON st.StoreId = s.StoreId WHERE 1=1"
    assert joins_in_sql(sql) == [("dbo.store", "StoreId", "dbo.salesdaily", "StoreId")]


# ---- knowledge -----------------------------------------------------------------------

def test_knowledge_shows_where_its_trigger_words_appear(catalogue):
    from pdcat.knowledge import item_text, save_knowledge, triggered
    uid = save_knowledge(catalogue, "Freight is always sea freight", "Air freight is booked as a separate cost.",
                         section="Business rules", triggers="freight, shipping")
    assert [k["uid"] for k in triggered(catalogue, "monthly freight cost")] == [uid]
    assert triggered(catalogue, "store sales") == []
    assert catalogue.search("freight")[0]["key"] == f"kb://{uid}"
    assert triggered(catalogue, item_text(catalogue, SALES), SALES) == []
