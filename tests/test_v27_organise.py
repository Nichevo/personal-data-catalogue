"""Version 2.7: long suggestion lists organised into groups with repeats merged, and file names apart from
their folders in the Power BI and search tables."""
from pathlib import Path

import pytest

from pdcat import organise, pathbits, suggestions
from pdcat.scan_files import scan_folder
from pdcat.triage import triage

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).parents[1] / "src" / "pdcat" / "app.py")


def test_split_and_shorten_paths():
    assert pathbits.split_name("Finance/Monthly/Sales.pbix", "report") == ("Sales.pbix", "Finance/Monthly")
    assert pathbits.split_name("Sales.pbix", "report") == ("Sales.pbix", "")
    assert pathbits.split_name("Sales / Units", "measure") == ("Sales / Units", "")  # a measure's name isn't a path
    assert pathbits.split_name(r"A\B\c.csv", "data_file") == ("c.csv", "A/B")
    assert pathbits.short_folder("C:/Users/x/Team/Finance/Monthly") == "…/Finance/Monthly"
    assert pathbits.short_folder("Finance/Monthly") == "Finance/Monthly"
    assert pathbits.parent_folder("C:/Data/Finance/Sales.pbix") == "C:/Data/Finance"


def _deep_report(tmp_path):
    from tests.test_v21_reports import write_report
    folder = tmp_path / "share"
    deep = folder / "Finance" / "Monthly" / "Archive"
    deep.mkdir(parents=True)
    write_report(deep / "Sales.pbit")
    return folder


def test_power_bi_lists_show_the_file_name_and_its_folder_apart(catalogue, tmp_path):
    from pdcat import pbiobjects
    from pdcat.measures import across_reports  # noqa: F401  (imports cleanly with the new columns)
    from pdcat.searchview import rows_for
    scan_folder(catalogue, "reports", _deep_report(tmp_path))
    [rep] = rows_for(catalogue, [dict(r) for r in catalogue.search("", kinds=("report",))])
    assert rep["name"] == "Sales.pbit"
    assert rep["folder"] == "…/Monthly/Archive"
    assert rep["_full_folder"].endswith("Finance/Monthly/Archive")
    measures = [r for r in rows_for(catalogue, [dict(r) for r in catalogue.search("units")]) if r["kind"] == "measure"]
    assert measures and all(m["in"] == "Sales.pbit" and m["folder"] == "…/Monthly/Archive" for m in measures)
    tables = pbiobjects.all_objects(catalogue, "tables")
    assert {t["in"] for t in tables} == {"Sales.pbit"}
    assert all(t["folder"] == "…/Monthly/Archive" and "where" not in t for t in tables)


def _sap(i: int, key: str, col: str, field: str = "WERKS") -> dict:
    return {"id": f"sap:{i}", "subject": key, "title": f"t{i}.{col} is SAP {field}", "score": 1,
            "detail": "Suggested because the column name is a usual English name for it.",
            "payload": {"key": key, "column": col, "sap_name": field}}


def test_sap_suggestions_grouped_and_repeats_merged(catalogue):
    from pdcat.ui.bulk import group_rows, merge_repeats
    found = [_sap(i, f"sql://w/db/dbo.t{i}", "Plant") for i in range(40)]
    found += [_sap(100 + i, f"sql://w/db/dbo.t{i}", "Customer", "KUNNR") for i in range(5)]
    suggestions.sync(catalogue, "sap", found)
    rows = organise.organise(catalogue, "sap", triage(catalogue, "sap", suggestions.open_suggestions(catalogue, "sap")))
    by_col = dict(group_rows(rows, "Column name"))
    assert len(by_col["Plant → WERKS"]) == 40 and len(by_col["Customer → KUNNR"]) == 5
    areas = dict(group_rows(rows, "SAP area"))
    assert "Organisation (plants, company, sales org)" in areas and "Sales and delivery (SD)" in areas
    merged = merge_repeats(rows)
    assert len(merged) == 2
    plant = next(m for m in merged if m["payload"]["column"] == "Plant")
    assert len(plant["members"]) == 40


def test_metric_views():
    assert organise.metric_kind("Forecast accuracy") == "Forecast accuracy and error"
    assert organise.metric_kind("Fill rate") == "Service and delivery"
    assert organise.first_word("SalesVsLY") == "Sales"
    assert organise.first_word("forecast_bias") == "Forecast"


@pytest.fixture
def app(catalogue, monkeypatch):
    monkeypatch.setenv("PDCAT_DB", str(catalogue.path))
    return AppTest.from_file(APP, default_timeout=20)


def test_big_list_opens_on_groups_and_a_whole_group_can_be_accepted(app, catalogue):
    from tests.test_app import goto
    found = [_sap(i, f"sql://w/db/dbo.t{i}", "Plant") for i in range(210)]
    found += [_sap(1000 + i, f"sql://w/db/dbo.t{i}", "Customer", "KUNNR") for i in range(20)]
    suggestions.sync(catalogue, "sap", found)
    app.run()
    goto(app, "Suggestions")
    assert not app.exception
    md = " ".join(m.value for m in app.markdown)
    assert "Plant → WERKS" in md and "Customer → KUNNR" in md
    [b for b in app.button if b.label == "Accept 20"][0].click().run()
    [b for b in app.button if b.label == "Yes, accept 20"][0].click().run()
    assert not app.exception
    left = suggestions.open_suggestions(catalogue, "sap", limit=1000)
    assert len(left) == 210 and all(s["payload"]["column"] == "Plant" for s in left)
    # Open the remaining group: the 210 repeats are one row
    [b for b in app.button if b.label == "Open"][0].click().run()
    assert not app.exception
    assert any("The same suggestion on 210 items" in c.value for c in app.caption)
    [b for b in app.button if b.label.startswith("Yes, accept 210")][0].click().run()
    assert not app.exception
    assert not suggestions.open_suggestions(catalogue, "sap", limit=1000)
