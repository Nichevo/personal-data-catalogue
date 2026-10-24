from pdcat.config import PythonSource
from pdcat.scan_python import scan_project
from pdcat.scan_sqlserver import connection_string, ingest
from pdcat.config import SqlServerSource

from conftest import FAKE_DB, SRC

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"
OUTPUT = "sql://warehouse/forecastdb/ml.forecastoutput"
FEATURES = "py://forecasting/src/features.py"


def test_sql_metadata_is_stored(catalogue):
    sales = catalogue.get_asset(SALES)
    assert sales["kind"] == "table"
    assert sales["description"] == "Daily unit sales per store"
    assert sales["attrs"]["row_count"] == 1200
    cols = catalogue.columns(SALES)
    assert [c["name"] for c in cols] == ["StoreId", "Units"]
    assert cols[1]["data_type"] == "decimal(18,2)"


def test_python_files_are_scanned_and_venv_skipped(catalogue):
    project = catalogue.get_asset("py://forecasting")
    assert project["description"] == "Weekly store-level sales forecast."
    names = [c["name"] for c in catalogue.children("py://forecasting")]
    assert names == ["src/features.py"]
    script = catalogue.get_asset(FEATURES)
    assert script["description"] == "Build weekly sales features for the forecast model."
    assert script["attrs"]["functions"] == ["load", "save"]
    assert "pandas" in script["attrs"]["imports"]


def test_code_references_are_matched_to_tables(catalogue):
    uses = {(l["relation"], l["dst_key"] or l["dst_ref"]) for l in catalogue.links_from(FEATURES)}
    assert ("reads", SALES) in uses                       # via f-string constant
    assert ("reads", "sql://warehouse/forecastdb/dbo.store") in uses  # bare name -> dbo
    assert ("writes", OUTPUT) in uses                     # to_sql with schema
    assert ("writes", "file:outputs/features.parquet") in uses
    assert ("reads", "table:staging.mystery") in uses     # stays unmatched
    assert [r["dst_ref"] for r in catalogue.unresolved_refs()] == ["table:staging.mystery"]


def test_used_by_includes_views_procedures_and_scripts(catalogue):
    used_by = {(l["src_key"], l["relation"]) for l in catalogue.links_to(OUTPUT)}
    assert (FEATURES, "writes") in used_by
    assert ("sql://warehouse/forecastdb/ml.vforecastlatest", "reads") in used_by
    assert ("sql://warehouse/forecastdb/ml.usp_loadforecast", "writes") in used_by


def test_notes_survive_rescans_and_are_searchable(catalogue, project):
    catalogue.add_note(SALES, "Refreshed nightly at 2am; late on Mondays.", "refresh, gotcha")
    catalogue.add_note(f"{SALES}#Units", "Excludes online orders.")
    with catalogue.scan(SRC.name):
        ingest(catalogue, SRC, "ForecastDB", FAKE_DB)
    assert len(catalogue.notes(SALES)) == 2
    assert [r["key"] for r in catalogue.search("monday")] == [SALES]
    assert [r["key"] for r in catalogue.search("gotcha")] == [SALES]
    assert catalogue.search("online orders")[0]["key"] == SALES
    assert catalogue.search("daily")[0]["key"] == SALES  # snake/dotted names split into words


def test_search_filters_and_needs_documentation(catalogue):
    assert {r["kind"] for r in catalogue.search("forecast", kind="script")} == {"script"}
    todo = [r["key"] for r in catalogue.needs_documentation()]
    assert SALES not in todo                # has a description
    assert todo[0] == OUTPUT                # used by the most files
    catalogue.add_note(OUTPUT, "Final forecast per store and week.")
    assert OUTPUT not in [r["key"] for r in catalogue.needs_documentation()]


def test_dropped_objects_are_marked_missing(catalogue):
    smaller = {"objects": FAKE_DB["objects"][1:], "columns": FAKE_DB["columns"][2:]}
    with catalogue.scan(SRC.name):
        ingest(catalogue, SRC, "ForecastDB", smaller)
    assert catalogue.get_asset(SALES)["status"] == "missing"
    assert catalogue.columns(SALES) == []


def test_failed_scan_changes_nothing(catalogue):
    try:
        with catalogue.scan(SRC.name):
            catalogue.upsert_asset("sql://warehouse/x", "table", "x", SRC.name)
            raise RuntimeError("connection dropped")
    except RuntimeError:
        pass
    assert catalogue.get_asset("sql://warehouse/x") is None
    assert catalogue.get_asset(SALES)["status"] == "active"


def test_deleted_script_is_marked_missing(catalogue, project):
    (project / "src" / "features.py").unlink()
    scan_project(catalogue, PythonSource(name="forecasting", path=project))
    assert catalogue.get_asset(FEATURES)["status"] == "missing"
    assert catalogue.links_from(FEATURES) == []


def test_connection_string_uses_windows_login_by_default():
    s = connection_string(SqlServerSource(name="w", server="SRV\\INST", databases=["db"]), "db")
    assert "Trusted_Connection=yes" in s and "SERVER=SRV\\INST" in s and "DATABASE=db" in s
    s = connection_string(SqlServerSource(name="w", server="s", databases=["db"],
                                          extra="Authentication=ActiveDirectoryInteractive;"), "db")
    assert "Trusted_Connection" not in s


def test_export_round_trips_everything(catalogue, tmp_path):
    import csv
    import io
    import json
    import zipfile

    from pdcat.export import to_csv_zip, to_json, write_export

    catalogue.add_note(SALES, "Refreshed nightly, café data é.", "refresh")
    data = json.loads(to_json(catalogue))
    assert set(data) == {"assets", "columns", "links", "notes"}
    assert data["notes"][0]["body"] == "Refreshed nightly, café data é."
    assert any(a["key"] == SALES and a["attrs"]["row_count"] == 1200 for a in data["assets"])
    assert any(l["dst"] == "table:staging.mystery" and not l["matched"] for l in data["links"])

    with zipfile.ZipFile(io.BytesIO(to_csv_zip(catalogue))) as z:
        assert sorted(z.namelist()) == ["assets.csv", "columns.csv", "links.csv", "notes.csv"]
        notes = list(csv.DictReader(io.StringIO(z.read("notes.csv").decode("utf-8-sig"))))
    assert notes[0]["asset_key"] == SALES and notes[0]["tags"] == "refresh"

    paths = write_export(catalogue, tmp_path / "out", "csv")
    assert {p.name for p in paths} == {"assets.csv", "columns.csv", "links.csv", "notes.csv"}


def test_pasted_sql_is_linked_searchable_kept_and_exported(catalogue, project):
    from pdcat.export import export_data

    key = catalogue.save_snippet(
        "Weekly units by region",
        "SELECT st.Region, SUM(s.Units) FROM SalesDaily s JOIN dbo.Store st ON st.StoreId = s.StoreId GROUP BY st.Region",
        description="Used for the regional forecast check.", tags="forecasting, adhoc",
        database="warehouse/ForecastDB")
    snippet = catalogue.get_asset(key)
    assert snippet["kind"] == "sql_snippet" and snippet["source"] == "manual"
    assert {l["dst_key"] for l in catalogue.links_from(key)} == {SALES, "sql://warehouse/forecastdb/dbo.store"}
    assert key in {l["src_key"] for l in catalogue.links_to(SALES)}
    assert catalogue.search("regional")[0]["key"] == key
    assert catalogue.search("adhoc")[0]["key"] == key

    # Rescanning other sources leaves it alone.
    scan_project(catalogue, PythonSource(name="forecasting", path=project))
    assert catalogue.get_asset(key)["status"] == "active"
    assert len(catalogue.links_from(key)) == 2

    # Editing replaces its links.
    catalogue.save_snippet("Weekly units", "SELECT * FROM ml.ForecastOutput", key=key)
    assert [l["dst_key"] for l in catalogue.links_from(key)] == [OUTPUT]

    data = export_data(catalogue)
    assert any(a["key"] == key and a["attrs"]["sql"] == "SELECT * FROM ml.ForecastOutput" for a in data["assets"])
    assert any(l["src_key"] == key for l in data["links"])

    catalogue.delete_snippet(key)
    assert catalogue.get_asset(key) is None and catalogue.links_from(key) == []
