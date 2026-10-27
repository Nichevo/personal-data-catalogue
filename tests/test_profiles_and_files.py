import json
import os
import sqlite3

import pandas as pd

from pdcat.config import PythonSource
from pdcat.export import export_data
from pdcat.profiling import combine_sql_profile, profile_dataframe, profile_file, sql_profile_queries
from pdcat.scan_files import scan_folder
from pdcat.scan_python import scan_project
from pdcat.store import Catalogue

from conftest import SRC  # noqa: F401

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"


def test_profile_dataframe():
    df = pd.DataFrame({"store": [1, 2, 2, None], "region": ["N", "S", "S", "S"],
                       "day": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"])})
    p = {c["name"]: c for c in profile_dataframe(df)}
    assert p["store"]["nulls"] == 1 and p["store"]["distinct"] == 2
    assert p["store"]["min"] == 1 and p["store"]["max"] == 2 and round(p["store"]["mean"], 3) == 1.667
    assert p["region"]["top"][0] == ["S", 3]
    assert str(p["day"]["min"]).startswith("2026-01-01")


def test_csv_and_parquet_file_profiles(tmp_path):
    df = pd.DataFrame({"a": range(10), "b": list("xyxyxyxyxy")})
    df.to_csv(tmp_path / "x.csv", index=False, sep=";")
    df.to_parquet(tmp_path / "x.parquet")
    for name in ("x.csv", "x.parquet"):
        attrs, profiles, method = profile_file(tmp_path / name)
        assert attrs["row_count"] == 10 and attrs["row_count_exact"]
        assert attrs["size_bytes"] > 0 and attrs["modified"]
        assert [p["name"] for p in profiles] == ["a", "b"]
        assert method == "whole file"
    attrs, profiles, method = profile_file(tmp_path / "x.csv", max_rows=4)
    assert attrs["row_count"] == 10 and not attrs["row_count_exact"]
    assert method == "first 4 of 10 rows" and profiles[0]["rows"] == 4
    attrs, _, method = profile_file(tmp_path / "x.parquet", max_rows=4)
    assert attrs["row_count"] == 10 and attrs["row_count_exact"]


def test_sql_profile_query_and_merge():
    cols = [{"name": "StoreId", "data_type": "int"}, {"name": "Notes", "data_type": "nvarchar(max)"},
            {"name": "Odd]Name", "data_type": "varchar(10)"}]
    agg_sql, sample_sql = sql_profile_queries("dbo", "Sales", cols, 500)
    assert "FROM [dbo].[Sales] WITH (NOLOCK)" in agg_sql
    assert "COUNT_BIG(DISTINCT [StoreId])" in agg_sql
    assert "MIN([Notes])" not in agg_sql           # no ranges on (max) types
    assert "[Odd]]Name]" in agg_sql                 # brackets escaped
    assert sample_sql.startswith("SELECT TOP (500) *")
    agg = {"__rows": 100, "n0": 100, "d0": 40, "lo0": "1", "hi0": "40",
           "n1": 60, "d1": None, "lo1": None, "hi1": None, "n2": 0, "d2": 0, "lo2": None, "hi2": None}
    sample = pd.DataFrame({"StoreId": [1, 1, 2], "Notes": ["a", None, "b"], "Odd]Name": [None] * 3})
    p = combine_sql_profile(cols, agg, sample)
    assert p[0]["rows"] == 100 and p[0]["nulls"] == 0 and p[0]["distinct"] == 40 and p[0]["min"] == "1"
    assert p[0]["top"][0] == ["1", 2]
    assert p[1]["nulls"] == 40


def test_project_data_files_are_profiled_and_linked(catalogue, project):
    out = project / "outputs"
    out.mkdir()
    pd.DataFrame({"StoreId": [1, 2], "Units": [5.0, None]}).to_parquet(out / "features.parquet")
    pd.DataFrame({"x": [1]}).to_csv(out / "forecast_2026-01.csv", index=False)
    pd.DataFrame({"x": [1, 2]}).to_csv(out / "forecast_2026-02.csv", index=False)
    os.utime(out / "forecast_2026-01.csv", (1, 1))  # make the January file the older one
    (project / "src" / "export.py").write_text(
        "import pandas as pd\n"
        "def run(df, month):\n"
        "    df.to_csv(f'outputs/forecast_{month}.csv')\n")
    (out / "broken.parquet").write_text("not parquet")
    scan_project(catalogue, PythonSource(name="forecasting", path=project, default_database="warehouse/ForecastDB"))

    feat = "py://forecasting/outputs/features.parquet"
    asset = catalogue.get_asset(feat)
    assert asset["kind"] == "data_file" and asset["attrs"]["row_count"] == 2 and asset["attrs"]["format"] == "parquet"
    assert [c["name"] for c in catalogue.columns(feat)] == ["StoreId", "Units"]
    assert {p["column_name"]: p["null_count"] for p in catalogue.profile(feat)} == {"StoreId": 0, "Units": 1}
    assert "read_error" in catalogue.get_asset("py://forecasting/outputs/broken.parquet")["attrs"]

    # features.py writes outputs/features.parquet; the wildcard f-string links to the monthly series.
    writes = {l["dst_key"] for l in catalogue.links_from("py://forecasting/src/features.py") if l["relation"] == "writes"}
    assert feat in writes
    monthly = [l["dst_key"] for l in catalogue.links_from("py://forecasting/src/export.py")]
    assert monthly == ["py://forecasting/~series/outputs/forecast_{yyyy}{mm}.csv"]
    series = catalogue.get_asset(monthly[0])
    assert series["kind"] == "file_series" and series["attrs"]["frequency"] == "monthly"
    assert series["attrs"]["row_count"] == 2 and [f["rows"] for f in series["attrs"]["files"]] == [1, 2]

    # Lineage back from the file: written by features.py, which reads dbo.SalesDaily and dbo.Store.
    assert SALES in catalogue.upstream(feat)


def test_unchanged_files_reuse_their_profile(catalogue, project, monkeypatch):
    out = project / "outputs"
    out.mkdir()
    pd.DataFrame({"a": [1, 2]}).to_csv(out / "a.csv", index=False)
    src = PythonSource(name="forecasting", path=project)
    scan_project(catalogue, src)
    import pdcat.scan_files as sf
    monkeypatch.setattr(sf, "profile_file", lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-read")))
    scan_project(catalogue, src)
    assert [c["name"] for c in catalogue.columns("py://forecasting/outputs/a.csv")] == ["a"]


def test_folder_scan_and_remove(tmp_path):
    cat = Catalogue(tmp_path / "c.db")
    folder = tmp_path / "extracts"
    (folder / "sub").mkdir(parents=True)
    pd.DataFrame({"a": [1]}).to_csv(folder / "top.csv", index=False)
    pd.DataFrame({"a": [1]}).to_csv(folder / "sub" / "deep.csv", index=False)
    cat.add_folder("extracts", str(folder), recursive=False)
    assert scan_folder(cat, "extracts", folder, recursive=False) == {"files": 1, "series": 0, "errors": 0}
    assert scan_folder(cat, "extracts", folder, recursive=True)["files"] == 2
    assert cat.get_asset("files://extracts/sub/deep.csv")["attrs"]["row_count"] == 1
    assert [r["name"] for r in cat.folders()] == ["extracts"]
    cat.add_note("files://extracts/top.csv", "keep me")
    cat.remove_folder("extracts")
    assert cat.folders() == [] and cat.get_asset("files://extracts/top.csv") is None
    assert len(cat.notes("files://extracts/top.csv")) == 1


def test_issue_and_example_notes(catalogue):
    issue = catalogue.add_note(f"{SALES}#Units", "Negative units on 2026-03-02 for store 14.", "dq", note_type="issue")
    catalogue.add_note(SALES, "StoreId 1-40\nUnits 0-1200", note_type="example")
    assert [n["id"] for n in catalogue.open_issues()] == [issue]
    assert catalogue.search("negative")[0]["key"] == SALES
    catalogue.set_issue_status(issue, "resolved")
    assert catalogue.open_issues() == []
    notes = {n["note_type"]: n for n in export_data(catalogue)["notes"]}
    assert notes["issue"]["status"] == "resolved" and notes["example"]["body"].startswith("StoreId")


def test_profiles_are_exported(catalogue):
    catalogue.save_profile(SALES, [{"name": "Units", "rows": 10, "nulls": 1, "min": 0, "max": 9, "top": [["1", 3]]}],
                           "test")
    p = export_data(catalogue)["profiles"]
    assert p[0]["column_name"] == "Units" and p[0]["max_value"] == "9" and p[0]["top_values"] == [["1", 3]]


def test_old_catalogue_is_migrated(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, asset_key TEXT NOT NULL, body TEXT NOT NULL, "
                 "tags TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    conn.execute("INSERT INTO notes (asset_key, body, created_at, updated_at) VALUES ('k', 'old', 't', 't')")
    conn.commit()
    conn.close()
    cat = Catalogue(path)
    assert cat.notes("k")[0]["note_type"] == "note"
