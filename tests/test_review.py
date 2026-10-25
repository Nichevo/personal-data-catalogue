import copy
import os

import pandas as pd

from pdcat.config import PythonSource
from pdcat.review import hotspots, open_tasks, refresh_tasks, set_task_status
from pdcat.scan_python import scan_project
from pdcat.scan_sqlserver import ingest

from conftest import FAKE_DB, SRC

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"
STORE = "sql://warehouse/forecastdb/dbo.store"
OUTPUT = "sql://warehouse/forecastdb/ml.forecastoutput"


def by_rule(cat, rule):
    return {t["asset_key"]: t for t in open_tasks(cat, limit=1000) if t["rule"] == rule}


def test_undocumented_and_used_items_come_first(catalogue):
    refresh_tasks(catalogue)
    undocumented = by_rule(catalogue, "undocumented")
    assert SALES not in undocumented          # has a description
    assert STORE in undocumented and OUTPUT in undocumented
    tasks = [t["asset_key"] for t in open_tasks(catalogue) if t["rule"] == "undocumented"]
    assert tasks.index(OUTPUT) < tasks.index("sql://warehouse/forecastdb/ml.vforecastlatest")  # used by code first
    assert by_rule(catalogue, "unmatched")["table:staging.mystery"]["detail"].startswith("Used by 1 file")
    assert all(t["used_by"] for t in open_tasks(catalogue, only_used=True))


def test_status_sticks_and_fixed_problems_close(catalogue):
    refresh_tasks(catalogue)
    tid = by_rule(catalogue, "undocumented")[STORE]["id"]
    set_task_status(catalogue, tid, "snoozed")
    refresh_tasks(catalogue)
    assert STORE not in by_rule(catalogue, "undocumented")

    # Documenting the other table closes its task automatically.
    catalogue.add_note(OUTPUT, "Final weekly forecast.")
    result = refresh_tasks(catalogue)
    assert result["closed"] >= 1 and OUTPUT not in by_rule(catalogue, "undocumented")

    # Removing the note brings the task back.
    catalogue.delete_note(catalogue.notes(OUTPUT)[0]["id"])
    refresh_tasks(catalogue)
    assert OUTPUT in by_rule(catalogue, "undocumented")


def test_new_changed_and_missing_objects(catalogue):
    catalogue.add_note(STORE, "Store master")
    changed = copy.deepcopy(FAKE_DB)
    changed["objects"] = [o for o in changed["objects"] if o["object_name"] != "Store"]
    changed["objects"][0]["modify_date"] = "2026-10-01 09:00:00"
    changed["objects"].append({"schema_name": "dbo", "object_name": "Promo", "object_type": "U ", "row_count": 5,
                               "description": None, "definition": None})
    FAKE_DB["objects"][0].setdefault("modify_date", "2026-01-01 00:00:00")
    with catalogue.scan(SRC.name):
        ingest(catalogue, SRC, "ForecastDB", FAKE_DB)
    with catalogue.scan(SRC.name):
        ingest(catalogue, SRC, "ForecastDB", changed)
    refresh_tasks(catalogue)
    assert "sql://warehouse/forecastdb/dbo.promo" in by_rule(catalogue, "new")
    assert SALES in by_rule(catalogue, "changed")
    assert "1 note" in by_rule(catalogue, "missing")[STORE]["detail"]
    del FAKE_DB["objects"][0]["modify_date"]


def test_odd_values_and_drift_from_source(catalogue, project):
    catalogue.save_profile(SALES, [
        {"name": "StoreId", "data_type": "int", "rows": 100, "nulls": 0, "distinct": 40, "min": 1, "max": 40},
        {"name": "Units", "data_type": "decimal(18,2)", "rows": 100, "nulls": 0, "distinct": 50, "min": -5, "max": 90},
        {"name": "Region", "data_type": "varchar(10)", "rows": 100, "nulls": 80, "distinct": 3},
        {"name": "Flag", "data_type": "bit", "rows": 100, "nulls": 0, "distinct": 1, "min": "1", "max": "1"},
        {"name": "Spare", "data_type": "int", "rows": 100, "nulls": 100, "distinct": 0},
        {"name": "SaleDate", "data_type": "date", "rows": 100, "nulls": 0, "distinct": 9, "min": "2025-01-01",
         "max": "2099-01-01"},
    ], "test")
    out = project / "outputs"
    out.mkdir()
    pd.DataFrame({"StoreId": [1, 2, 99], "Units": [1.0, None, None]}).to_csv(out / "features.csv", index=False)
    (project / "src" / "extract.py").write_text(
        "import pandas as pd\n"
        "def run(eng):\n"
        "    df = pd.read_sql('SELECT * FROM dbo.SalesDaily', eng)\n"
        "    df.to_csv('outputs/features.csv')\n")
    scan_project(catalogue, PythonSource(name="forecasting", path=project, default_database="warehouse/ForecastDB"))
    refresh_tasks(catalogue)

    odd = by_rule(catalogue, "odd_values")[SALES]["detail"]
    for phrase in ("Units has negative values", "Region is 80% empty", "Flag has only one value",
                   "Spare is always empty", "SaleDate has future dates"):
        assert phrase in odd
    drift = by_rule(catalogue, "drift")["py://forecasting/outputs/features.csv"]["detail"]
    assert "StoreId goes above the source (max 99 vs 40)" in drift
    assert "Units is 67% empty vs 0% in the source" in drift


def test_stale_file_when_source_changed_after_it(catalogue, project):
    out = project / "outputs"
    out.mkdir()
    pd.DataFrame({"a": [1]}).to_csv(out / "old.csv", index=False)
    os.utime(out / "old.csv", (1_600_000_000, 1_600_000_000))
    (project / "src" / "make_old.py").write_text(
        "import pandas as pd\n"
        "def run(eng):\n"
        "    pd.read_sql('SELECT * FROM dbo.SalesDaily', eng).to_csv('outputs/old.csv')\n"
        "    pd.read_csv('outputs/old.csv')\n")
    scan_project(catalogue, PythonSource(name="forecasting", path=project, default_database="warehouse/ForecastDB"))
    refresh_tasks(catalogue)
    assert "days ago" in by_rule(catalogue, "stale")["py://forecasting/outputs/old.csv"]["detail"]


def test_hotspots_rank_areas_and_items(catalogue):
    catalogue.add_note(f"{OUTPUT}#ForecastUnits", "Duplicates for run 7", note_type="issue")
    catalogue.add_note(OUTPUT, "Another issue", note_type="issue")
    refresh_tasks(catalogue)
    areas, items = hotspots(catalogue)
    assert areas.iloc[0]["area"] == "warehouse / ForecastDB / ml"
    assert areas.iloc[0]["open issues"] == 2
    assert items.iloc[0]["key"] == OUTPUT and items.iloc[0]["score"] >= 10
