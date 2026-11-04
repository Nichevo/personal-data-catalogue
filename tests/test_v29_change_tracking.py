"""Version 2.9: change tracking (growth readings, profile history, change trackers, the change feed, profiling
rules and database size)."""
import copy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import FAKE_DB, SRC

from pdcat import changes, dbsize, growth, profilerules, suggestions, trackers
from pdcat.scan_files import scan_folder
from pdcat.scan_sqlserver import ingest
from pdcat.store import Catalogue

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"
PLANT = "sql://warehouse/forecastdb/dbo.tableplant"
HEADER = "PlantID,ColumnName,ValueBefore,ValueAfter,FieldName,Table,ChangeDate\n"
ROWS = ("P1,WERKS_NAME,,Alpha,Plant name,TABLEPlant,2026-10-01\n"
        "P1,STATUS,A,B,Status,TABLEPlant,2026-10-02\n"
        "P2,STATUS,A,,Status,TABLEPlant,2026-10-02\n"
        "P3,WERKS_NAME,X,Y,Plant name,TABLEPlant,2026-10-03\n")


def _with_plant(rows: int = 3) -> dict:
    db = copy.deepcopy(FAKE_DB)
    db["objects"].append({"schema_name": "dbo", "object_name": "TABLEPlant", "object_type": "U ", "row_count": rows,
                          "description": None, "definition": None})
    for i, c in enumerate(["PlantID", "WERKS_NAME", "STATUS"]):
        db["columns"].append({"schema_name": "dbo", "object_name": "TABLEPlant", "column_name": c, "column_id": i + 1,
                              "type_name": "nvarchar", "max_length": 20, "precision": 0, "scale": 0,
                              "is_nullable": True, "description": None})
    return db


@pytest.fixture
def tracked(tmp_path: Path, catalogue: Catalogue):
    with catalogue.scan(SRC.name):
        ingest(catalogue, SRC, "ForecastDB", _with_plant())
    folder = tmp_path / "changes"
    folder.mkdir()
    (folder / "ChangesX_TABLEPlant.csv").write_text(HEADER + ROWS)
    scan_folder(catalogue, "changes", folder)
    trackers.detect(catalogue)
    return catalogue, folder / "ChangesX_TABLEPlant.csv", "files://changes/ChangesX_TABLEPlant.csv"


# ---- growth readings ---------------------------------------------------------------------------------

@pytest.fixture
def clock(monkeypatch):
    """Each scan a minute after the last, so readings don't share a timestamp."""
    t = [datetime.now(timezone.utc) + timedelta(days=1)]

    def tick() -> str:
        t[0] += timedelta(minutes=1)
        return t[0].isoformat(timespec="seconds")
    monkeypatch.setattr(growth, "now", tick)


def test_scans_keep_row_counts_only_when_they_move(catalogue, clock):
    first = growth.row_series(catalogue, [SALES])
    assert [r["rows"] for r in first] == [1200]
    with catalogue.scan(SRC.name):
        ingest(catalogue, SRC, "ForecastDB", FAKE_DB)
    assert len(growth.row_series(catalogue, [SALES])) == 1        # same count: nothing new kept
    db = copy.deepcopy(FAKE_DB)
    db["objects"][0]["row_count"] = 2500
    with catalogue.scan(SRC.name):
        ingest(catalogue, SRC, "ForecastDB", db)
    assert [r["rows"] for r in growth.row_series(catalogue, [SALES])] == [1200, 2500]
    assert growth.jumps(catalogue, [SALES])[0]["to"] == 2500
    up, _ = growth.growers(catalogue, "2000-01-01")
    assert up[0]["key"] == SALES and up[0]["change"] > 1


def test_scan_counts_items_and_files_per_folder(tmp_path, catalogue):
    folder = tmp_path / "data"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.csv").write_text("x\n1\n")
    (folder / "sub" / "b.csv").write_text("x\n1\n")
    scan_folder(catalogue, "data", folder)
    top = {r["folder"]: r["files"] for r in growth.folder_series(catalogue, "data", depth=None)}
    assert top["."] == 2 and top["sub"] == 1
    kinds = {r["kind"] for r in growth.catalogue_series(catalogue)}
    assert {"table", "data_file", "described"} <= kinds
    assert growth.folder_table(catalogue, depth=0, source="data")[0]["files"] == 2


def test_backfill_turns_old_change_history_into_readings(tmp_path):
    cat = Catalogue(tmp_path / "old.db")
    with cat.scan(SRC.name):
        ingest(cat, SRC, "ForecastDB", FAKE_DB)
    with cat.conn:
        cat.conn.execute("DELETE FROM row_counts")
        cat.conn.execute("DELETE FROM scan_counts")
        cat.conn.execute("INSERT INTO schema_history (asset_key, at, change, detail) VALUES (?, ?, 'row count', ?)",
                         (SALES, "2026-09-01T00:00:00+00:00", "1,000 → 1,200"))
    got = growth.backfill(cat)
    assert got["row_counts"] >= 2
    assert [r["rows"] for r in growth.row_series(cat, [SALES])][:2] == [1000, 1200]
    assert growth.backfill(cat) == {}                               # only once
    assert growth.catalogue_series(cat)


def test_profiles_are_kept_when_a_table_is_profiled_again(catalogue):
    cols = [{"name": "Units", "rows": 100, "nulls": 0, "distinct": 50, "min": 1, "max": 9, "mean": 4.0}]
    catalogue.save_profile(SALES, cols, "test")
    with catalogue.conn:   # as if profiled last month
        catalogue.conn.execute("UPDATE profile_history SET at = '2026-01-01T00:00:00+00:00'")
    catalogue.save_profile(SALES, cols, "test")                     # same numbers: no new copy
    moved = [{**cols[0], "rows": 200, "nulls": 60}]
    catalogue.save_profile(SALES, moved, "test")
    hist = growth.profile_history(catalogue, SALES, "Units")
    assert [h["row_count"] for h in hist] == [100, 200]
    drift = growth.profile_drift(catalogue, keys={SALES})
    assert drift and "null share" in drift[0]["detail"]


def test_thinning_keeps_one_reading_a_week_then_a_month(catalogue):
    today = date(2026, 11, 3)
    rows = [(SALES, (today - timedelta(days=d)).isoformat() + "T00:00:00+00:00", 1000 + d) for d in range(1, 500)]
    with catalogue.conn:
        catalogue.conn.executemany("INSERT OR REPLACE INTO row_counts (asset_key, at, rows) VALUES (?, ?, ?)", rows)
    removed = growth.thin_history(catalogue, 90, 365, today=today)
    assert removed["row_counts"] > 300
    left = growth.row_series(catalogue, [SALES])
    recent = [r for r in left if r["at"] >= (today - timedelta(days=90)).isoformat()]
    assert len(recent) >= 89                                         # every reading in the last 90 days
    assert len(left) < 90 + 60 + 20


# ---- change trackers ---------------------------------------------------------------------------------

def test_shape_and_tracked_name():
    s = trackers.shape_of(["PlantID", "ColumnName", "ValueBefore", "ValueAfter", "FieldName", "Table", "ChangeDate"])
    assert s == {**s, "layout": "long", "date": "ChangeDate", "column": "ColumnName", "label": "FieldName",
                 "before": "ValueBefore", "after": "ValueAfter", "table": "Table", "key": "PlantID"}
    assert trackers.shape_of(["MANDANT", "TABNAME", "TABKEY", "FNAME", "CHNGIND", "VALUE_NEW", "VALUE_OLD"])[
        "layout"] == "long"
    assert trackers.shape_of(["__$start_lsn", "__$operation", "PlantID", "Name"])["layout"] == "wide"
    assert trackers.tracked_name(None, "ChangesX_TABLEPlant", ["ChangesX_"])[0] == "TABLEPlant"
    assert trackers.tracked_name(None, "MyPrefix_Plant", ["MyPrefix_"])[0] == "Plant"
    assert trackers.tracked_name(None, "Plant_changes", [])[0] == "Plant"
    assert trackers.tracked_name(None, "dbo_Plant_CT", [])[0] == "Plant"
    assert trackers.tracked_name(None, "PlantHistory", [])[0] == "Plant"


def test_tracker_is_found_and_linked_to_its_table(tracked):
    cat, _, key = tracked
    t = trackers.list_trackers(cat)[0]
    assert t["key"] == key and t["shape"]["layout"] == "long"
    link = t["links"][0]
    assert link["table_key"] == PLANT and link["band"] == "likely" and "ChangesX_" in link["reason"]
    trackers.set_link(cat, key, PLANT, "confirmed")
    trackers.detect(cat)                                             # a decision survives the next scan
    assert trackers.list_trackers(cat)[0]["links"][0]["state"] == "confirmed"


def test_a_different_prefix_can_be_set(tmp_path, catalogue):
    with catalogue.scan(SRC.name):
        ingest(catalogue, SRC, "ForecastDB", _with_plant())
    folder = tmp_path / "c"
    folder.mkdir()
    (folder / "Delta-TABLEPlant.csv").write_text("PlantID,Col,Old,New\nP1,A,1,2\n")
    scan_folder(catalogue, "c", folder)
    assert trackers.detect(catalogue) == 0
    catalogue.set_setting("tracker_prefixes", ["Delta-"])
    assert trackers.detect(catalogue) == 1
    assert trackers.list_trackers(catalogue)[0]["links"][0]["table_key"] == PLANT


def test_reading_counts_changes_by_column_and_only_new_rows_after(tracked):
    cat, path, key = tracked
    r = trackers.read_changes(cat, key)
    assert r["rows"] == 4 and r["from"] == "2026-10-01" and r["to"] == "2026-10-03"
    cols = {c["column"]: c for c in trackers.column_counts(cat, key)}
    assert cols["WERKS_NAME"]["new"] == 1 and cols["WERKS_NAME"]["changed"] == 1
    assert cols["STATUS"]["removed"] == 1 and cols["STATUS"]["label"] == "Status"
    assert cols["STATUS"]["keys"] == 2
    with path.open("a") as f:
        f.write("P4,STATUS,B,C,Status,TABLEPlant,2026-10-03\nP5,STATUS,B,C,Status,TABLEPlant,2026-10-05\n")
    again = trackers.read_changes(cat, key)
    assert again["rows"] == 3                                        # the last day again, plus the new rows
    cols = {c["column"]: c for c in trackers.column_counts(cat, key)}
    assert cols["STATUS"]["changes"] == 4 and cols["WERKS_NAME"]["changes"] == 2   # nothing counted twice
    assert cols["STATUS"]["keys"] == 4
    full = trackers.read_changes(cat, key, full=True)
    assert full["rows"] == 6
    assert {c["column"]: c["changes"] for c in trackers.column_counts(cat, key)} == {"STATUS": 4, "WERKS_NAME": 2}


def test_watched_columns_default_to_the_busiest_and_can_be_picked(tracked):
    cat, _, key = tracked
    trackers.read_changes(cat, key)
    assert set(trackers.watched(cat, key)) == {"STATUS", "WERKS_NAME"}
    trackers.set_watch(cat, key, ["STATUS"])
    assert trackers.watched(cat, key) == ["STATUS"]
    trackers.set_link(cat, key, PLANT, "confirmed")
    rows = changes.tracked_rows(cat, "2026-01-01")
    assert rows and {r["column"] for r in rows} == {"STATUS"} and {r["item"] for r in rows} == {PLANT}
    summary = trackers.table_summary(cat, PLANT)
    assert summary[0]["changes"] == 4 and summary[0]["columns"][0]["watched"]


def test_field_names_are_offered_as_business_names(tracked):
    cat, _, key = tracked
    trackers.read_changes(cat, key)
    open_ = suggestions.open_suggestions(cat, "tracker_field")
    assert {s["payload"]["label"] for s in open_} == {"Plant name"}   # STATUS/Status is the same name
    suggestions.accept(cat, open_[0]["id"])
    row = cat.conn.execute("SELECT business_name FROM column_info WHERE asset_key = ? AND column_name = 'WERKS_NAME'",
                           (PLANT,)).fetchone()
    assert row and row[0] == "Plant name"


def test_not_a_tracker_sticks(tracked):
    cat, _, key = tracked
    trackers.set_not_tracker(cat, key)
    trackers.detect(cat)
    assert not trackers.list_trackers(cat)
    trackers.set_not_tracker(cat, key, False)
    trackers.detect(cat)
    assert trackers.list_trackers(cat)


def test_unlinked_tracker_goes_on_the_review_list(tracked):
    from pdcat.review import open_tasks, refresh_tasks
    cat, _, key = tracked
    refresh_tasks(cat)
    assert any(t["rule"] == "tracker_link" and t["asset_key"] == key for t in open_tasks(cat, limit=1000))
    trackers.set_link(cat, key, PLANT, "confirmed")
    refresh_tasks(cat)
    assert not any(t["rule"] == "tracker_link" for t in open_tasks(cat, limit=1000))


def test_sql_tracker_is_read_with_paced_grouped_queries(catalogue):
    db = _with_plant()
    db["objects"].append({"schema_name": "dbo", "object_name": "ChangesX_TABLEPlant", "object_type": "U ",
                          "row_count": 10, "description": None, "definition": None})
    for i, c in enumerate(["PlantID", "ColumnName", "ValueBefore", "ValueAfter", "FieldName", "Table",
                           "ChangeDate"]):
        db["columns"].append({"schema_name": "dbo", "object_name": "ChangesX_TABLEPlant", "column_name": c,
                              "column_id": i + 1, "type_name": "nvarchar", "max_length": 50, "precision": 0,
                              "scale": 0, "is_nullable": True, "description": None})
    with catalogue.scan(SRC.name):
        ingest(catalogue, SRC, "ForecastDB", db)
    trackers.detect(catalogue)
    key = "sql://warehouse/forecastdb/dbo.changesx_tableplant"
    assert trackers.list_trackers(catalogue)[0]["links"][0]["table_key"] == PLANT
    sent = []

    class Cursor:
        description = None
        connection = type("C", (), {"timeout": 0})()

        def execute(self, sql):
            sent.append(sql)
            self.description = [("a",)]
            if "keys affected" in sql or "TOP (" in sql:
                self.rows = [("TABLEPlant", "STATUS", "P1", 3, "2026-10-02")]
            elif "label" in sql:
                self.rows = [("WERKS", "Plant", 2)]
            else:
                self.rows = [("TABLEPlant", "2026-10-02", "STATUS", "changed", 3),
                             ("TABLEPlant", "2026-10-03", "WERKS", "new", 2)]

        def fetchall(self):
            return self.rows

    class Conn:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    catalogue.set_setting("sql_pause_seconds", 0)
    r = trackers.read_changes(catalogue, key, sources=[SRC], connector=lambda src, db: Conn())
    assert r["changes"] == 5 and len(sent) == 3
    assert all("WITH (NOLOCK)" in q and "GROUP BY" in q for q in sent)
    assert {c["column"]: c["changes"] for c in trackers.column_counts(catalogue, key)} == {"STATUS": 3, "WERKS": 2}
    logged = catalogue.conn.execute("SELECT COUNT(*) FROM query_log WHERE purpose LIKE 'change summary%'").fetchone()
    assert logged[0] == 3
    again = trackers.sql_queries(catalogue.get_asset(key), trackers.list_trackers(catalogue)[0]["shape"],
                                 "2026-10-03")
    assert all(">= '2026-10-03'" in q for _, q in again)


def test_sensitive_key_column_keeps_no_key_values(tracked):
    from pdcat.curation import set_column_info
    cat, _, key = tracked
    set_column_info(cat, key, "PlantID", sensitive=True)
    trackers.read_changes(cat, key)
    assert cat.conn.execute("SELECT COUNT(*) FROM tracker_keys").fetchone()[0] == 0
    assert trackers.column_counts(cat, key)


# ---- the change feed ---------------------------------------------------------------------------------

def test_feed_filters_groups_and_saved_views(tracked):
    cat, _, key = tracked
    trackers.read_changes(cat, key)
    trackers.set_link(cat, key, PLANT, "confirmed")
    from pdcat.curation import set_item_info
    set_item_info(cat, SALES, purpose="Daily sales")
    rows = changes.feed(cat, "2026-01-01")
    cats = {r["category"] for r in rows}
    assert {"scan", "edit", "tracked"} <= cats
    edits = changes.apply_filters(cat, rows, {"categories": ["edit"]})
    assert edits and all(r["category"] == "edit" for r in edits)
    assert changes.apply_filters(cat, rows, {"text": "daily sales"})
    grouped = changes.group(cat, rows, ["category", "kind"])
    assert grouped and {"What", "Kind of item", "changes", "items"} <= set(grouped[0])
    by_week = changes.group(cat, rows, ["week"])
    assert by_week[0]["Week"].startswith("week of")
    changes.save_view(cat, "Plants", {"categories": ["tracked"]})
    assert changes.saved_views(cat)["Plants"] == {"categories": ["tracked"]}
    changes.delete_view(cat, "Plants")
    assert changes.saved_views(cat) == {}
    with pytest.raises(ValueError):
        changes.save_view(cat, " ", {})


def test_hide_noise_leaves_out_small_row_moves_and_old_items(catalogue):
    from pdcat.curation import set_item_info
    rows = [{"at": "2026-10-01T00:00:00", "item": SALES, "name": "x", "kind": "table", "source": "w",
             "category": "rows", "change": "row count", "detail": "1,000 → 1,010", "n": 1, "column": ""},
            {"at": "2026-10-01T00:00:00", "item": SALES, "name": "x", "kind": "table", "source": "w",
             "category": "rows", "change": "row count", "detail": "1,000 → 2,000", "n": 1, "column": ""}]
    assert len(changes.apply_filters(catalogue, rows, {"hide_noise": True})) == 1
    assert len(changes.apply_filters(catalogue, rows, {"hide_noise": False})) == 2
    set_item_info(catalogue, SALES, relevance="not relevant")
    assert not changes.apply_filters(catalogue, rows, {"hide_noise": True})


def test_changes_since_last_visit(catalogue):
    assert changes.since_last_visit(catalogue) == (0, None)
    changes.mark_seen(catalogue)
    db = copy.deepcopy(FAKE_DB)
    db["columns"].append({"schema_name": "dbo", "object_name": "Store", "column_name": "City", "column_id": 2,
                          "type_name": "nvarchar", "max_length": 100, "precision": 0, "scale": 0,
                          "is_nullable": True, "description": None})
    with catalogue.scan(SRC.name):
        ingest(catalogue, SRC, "ForecastDB", db)
    n, _ = changes.since_last_visit(catalogue)
    assert n >= 1


# ---- profiling rules and database size ---------------------------------------------------------------

def test_profiling_rules_queue_tables_when_due(catalogue):
    from pdcat.pacing import queue
    profilerules.set_rule(catalogue, [SALES], "weekly")
    assert profilerules.due(catalogue) == [SALES]
    assert profilerules.request_due(catalogue) == 1
    assert {q["asset_key"]: q["state"] for q in queue(catalogue)}[SALES] == "queued"   # still needs approval
    assert profilerules.due(catalogue) == []                          # waiting in the queue
    catalogue.conn.execute("DELETE FROM profile_queue")
    later = datetime.now(timezone.utc) + timedelta(days=8)
    assert profilerules.due(catalogue, later) == [SALES]
    profilerules.set_rule(catalogue, [SALES], "never")
    assert profilerules.due(catalogue, later) == []
    row = next(r for r in profilerules.profiling_list(catalogue) if r["key"] == SALES)
    assert row["rule"] == "never" and row["used"]
    with pytest.raises(ValueError):
        profilerules.set_rule(catalogue, [SALES], "hourly")


def test_database_size_and_tidy_up(catalogue):
    sizes = {s["table"]: s for s in dbsize.table_sizes(catalogue)}
    assert sizes["assets"]["rows"] > 0
    r = dbsize.tidy_up(catalogue)
    assert r["after"] > 0 and "row_counts" in r["removed"]
    assert catalogue.setting("tidied_at")
    assert set(dbsize.yearly_estimate(catalogue)) == {"row counts", "folder counts", "profile copies"}


def test_export_and_copilot_pack_include_change_tracking(tracked):
    from pdcat.aipack import build
    from pdcat.export import export_data
    cat, _, key = tracked
    trackers.read_changes(cat, key)
    trackers.set_link(cat, key, PLANT, "confirmed")
    data = export_data(cat)
    assert data["tracked_changes"] and data["change_trackers"] and data["row_counts_over_time"]
    pack = build(cat)
    assert "change-tracking.md" in pack and "Status (2)" in pack["change-tracking.md"]


# ---- the app -----------------------------------------------------------------------------------------

@pytest.fixture
def app(tracked, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    cat, _, key = tracked
    trackers.read_changes(cat, key)
    monkeypatch.setenv("PDCAT_DB", str(cat.path))
    return AppTest.from_file(str(Path(__file__).parents[1] / "src" / "pdcat" / "app.py"), default_timeout=30)


def test_change_tracking_pages_render(app):
    from test_app import goto
    app.run()
    goto(app, "Change tracking")
    assert not app.exception
    assert app.title[0].value == "Change tracking"
    app.selectbox(key="chg-by1").set_value("category").run()
    assert not app.exception
    app.radio(key="sub-Change tracking").set_value("Graphs").run()
    for graph in ("Table growth", "Files in folders", "Report changes", "Catalogue growth", "Scans",
                  "Change volume", "Tracked changes"):
        app.radio(key="chg-graph").set_value(graph).run()
        assert not app.exception, graph
    for per in ("day", "week", "month", "auto"):
        app.selectbox(key="chg-per").set_value(per).run()
        assert not app.exception, per
    for graph in ("Table growth", "Report changes", "Scans", "Change volume", "Tracked changes"):
        app.radio(key="chg-graph").set_value(graph).run()
        assert not app.exception, graph
    app.radio(key="sub-Change tracking").set_value("Change trackers").run()
    assert not app.exception
    assert any("tracks" in b.label for b in app.button)
    [b for b in app.button if b.label == "✓"][0].click().run()
    assert not app.exception
    watch = [m for m in app.multiselect if (m.key or "").startswith("trk-watch-")][0]
    watch.set_value(["STATUS"]).run()
    assert not app.exception


def test_item_history_tab_shows_tracked_changes(app):
    from test_app import open_tab
    app.query_params["asset"] = PLANT
    app.run()
    open_tab(app, "history")
    assert any("Tracked changes" in m.value for m in app.markdown)


def test_profiling_and_database_size_pages(app):
    from test_app import goto
    app.run()
    goto(app, "Profiling")
    assert not app.exception
    goto(app, "Database size")
    assert not app.exception
    [b for b in app.button if b.label == "Tidy up now"][0].click().run()
    assert not app.exception
    assert any("→" in s.value for s in app.success)
