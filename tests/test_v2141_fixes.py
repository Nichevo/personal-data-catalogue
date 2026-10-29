"""Version 2.14.1: blank values from pbixray no longer stop a Power BI scan, the Power BI map has filters,
and a folder scan stats each file once."""
import math
import sys
import textwrap

import pytest
from test_office import write_pbix

from pdcat import scan_powerbi
from pdcat.measures import across_reports, dax_refs
from pdcat.powerbimap import pick_reports
from pdcat.reportversions import facets, record_revision
from pdcat.scan_files import scan_folder
from pdcat.store import Catalogue
from pdcat.textclean import as_text


def test_as_text_turns_blanks_into_empty_strings():
    import pandas as pd
    assert as_text(None) == "" and as_text(float("nan")) == "" and as_text(pd.NA) == "" and as_text(pd.NaT) == ""
    assert as_text("SUM(x)") == "SUM(x)" and as_text(3) == "3" and as_text(2.5) == "2.5"


def _fake_pbixray(tmp_path, monkeypatch, body):
    pkg = tmp_path / "fakes" / "pbixray"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(textwrap.dedent(body))
    monkeypatch.syspath_prepend(str(tmp_path / "fakes"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "fakes"))
    sys.modules.pop("pbixray", None)


NAN_MODEL = """
    import numpy as np
    import pandas as pd
    class PBIXRay:
        def __init__(self, path):
            self.schema = pd.DataFrame([{"TableName": "Sales", "ColumnName": "Units", "PandasDataType": "int64"}])
            self.dax_measures = pd.DataFrame([
                {"TableName": "Sales", "Name": "Total", "Expression": "SUM(Sales[Units])", "Description": np.nan,
                 "DisplayFolder": np.nan},
                {"TableName": "Sales", "Name": "Empty", "Expression": np.nan, "Description": np.nan,
                 "DisplayFolder": np.nan}])
            self.power_query = pd.DataFrame([{"TableName": "Sales", "Expression": np.nan}])
            self.relationships = pd.DataFrame([{"FromTableName": "Sales", "FromColumnName": "StoreId",
                                                "ToTableName": "Store", "ToColumnName": "StoreId", "IsActive": np.nan}])
"""


def test_blank_cells_from_pbixray_become_empty_text_and_the_scan_goes_through(tmp_path, monkeypatch):
    _fake_pbixray(tmp_path, monkeypatch, NAN_MODEL)
    folder = tmp_path / "reports"
    folder.mkdir()
    write_pbix(folder / "Sales.pbix")
    model, problem = scan_powerbi._model_in_own_process(folder / "Sales.pbix")
    assert problem is None
    assert [m["expression"] for m in model["measures"]] == ["SUM(Sales[Units])", ""]
    assert model["measures"][0]["description"] == "" and model["measures"][0]["folder"] == ""
    assert model["relationships"][0]["active"] is True
    assert all(isinstance(q["expression"], str) for q in model["queries"])
    cat = Catalogue(tmp_path / "c.db")
    counts = scan_folder(cat, "reports", folder)
    assert counts["reports"] == 1 and counts["errors"] == 0
    scan_folder(cat, "reports", folder)  # a rescan compares with the stored version: still fine
    assert across_reports(cat) == {"same_dax": [], "same_name": []}


def test_blanks_stored_by_an_earlier_version_do_not_break_comparisons(tmp_path):
    nan = float("nan")
    attrs = {"pages": [], "measures": [{"table": "Sales", "name": "Total", "expression": nan}],
             "tables": [], "queries": [{"name": "Sales", "expression": nan, "tables": []}]}
    assert facets(attrs, "report")["measures"] == {"Sales[Total]": ""}
    assert facets({"sheets": [], "queries": [{"name": "Q", "expression": nan}],
                   "connections": [{"name": "C", "command": nan, "type": "ODBC"}], "pivots": []},
                  "workbook")["queries"] == {"Q": ""}
    assert dax_refs(nan, {}) == ([], [])
    cat = Catalogue(tmp_path / "c.db")
    cat.upsert_asset("files://r/A.pbix", "report", "A.pbix", "r", attrs={"pages": [], "measures": [], "tables": [],
                                                                        "queries": []})
    cat.upsert_asset("files://r/B.pbix", "report", "B.pbix", "r", attrs={"pages": [], "measures": [], "tables": [],
                                                                        "queries": []})
    for key, exp in (("files://r/A.pbix", nan), ("files://r/B.pbix", "SUM(Sales[Units])")):
        cat.upsert_asset(f"{key}/measure/Sales/Total", "measure", "Sales[Total]", "r", parent=key,
                         attrs={"table": "Sales", "name": "Total", "expression": exp})
    assert across_reports(cat) == {"same_dax": [], "same_name": []}  # the blank one is simply left out
    record_revision(cat, "files://r/A.pbix", {"measures": [{"table": "Sales", "name": "Total", "expression": nan}]},
                    {"measures": [{"table": "Sales", "name": "Total", "expression": "SUM(Sales[Units])"}]}, "report")


def test_power_bi_map_reports_can_be_filtered():
    rows = [{"name": "Sales weekly", "folder": "reports", "flag": "", "size": 30},
            {"name": "Stock", "folder": "reports", "flag": "loads nothing catalogued", "size": 20},
            {"name": "Sales daily", "folder": "archive", "flag": "", "size": 10}]
    assert [r["name"] for r in pick_reports(rows, text="sales")] == ["Sales weekly", "Sales daily"]
    assert [r["name"] for r in pick_reports(rows, text="sales archive")] == ["Sales daily"]
    assert [r["name"] for r in pick_reports(rows, flagged_only=True)] == ["Stock"]
    assert [r["name"] for r in pick_reports(rows, limit=2)] == ["Sales weekly", "Stock"]
    assert pick_reports(rows, text="nothing") == []


def test_power_bi_map_page_filters(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from test_app import APP, goto
    from test_office import write_pbit
    folder = tmp_path / "reports"
    folder.mkdir()
    write_pbit(folder / "Sales.pbit")
    write_pbit(folder / "Stock.pbit")
    cat = Catalogue(tmp_path / "c.db")
    scan_folder(cat, "reports", folder)
    monkeypatch.setenv("PDCAT_DB", str(cat.path))
    app = AppTest.from_file(APP, default_timeout=15)
    app.run()
    goto(app, "Power BI map")
    assert not app.exception
    app.text_input(key="pbimap-text").input("stock").run()
    assert not app.exception
    assert any("Showing 1 of 2 reports" in c.value for c in app.caption)
    app.text_input(key="pbimap-text").input("nothing here").run()
    assert not app.exception
    assert any("None of the 2 reports match" in i.value for i in app.info)


def test_each_file_is_stat_ed_once_per_scan(tmp_path, monkeypatch):
    from pathlib import Path

    import pandas as pd
    folder = tmp_path / "data"
    folder.mkdir()
    for i in range(3):
        pd.DataFrame({"a": [1]}).to_csv(folder / f"f{i}.csv", index=False)
    calls = []
    real = Path.stat

    def counting(self, *a, **k):
        if self.suffix == ".csv":
            calls.append(self)
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "stat", counting)
    cat = Catalogue(tmp_path / "c.db")
    scan_folder(cat, "data", folder)
    first = len(calls)
    calls.clear()
    scan_folder(cat, "data", folder)
    assert len(calls) == 3  # a rescan of unchanged files: the one pass in stat_files, nothing more
    assert first <= 3 * 3  # the first scan still stats inside the readers, but far fewer times than before
    assert not math.isnan(cat.scan_log()[0]["summary"]["seconds"])
