"""Version 2.16: model tables and Power Query queries inside a report or workbook open pages of their own."""
from pathlib import Path

import pytest

from pdcat import followups, pbiobjects
from pdcat.scan_files import scan_folder
from tests.test_office import SALES, write_pbit, write_pbix, write_xlsx

REPORT = "files://reports/Sales.pbit"


@pytest.fixture
def report(catalogue, tmp_path):
    folder = tmp_path / "reports"
    folder.mkdir()
    write_pbit(folder / "Sales.pbit")
    write_xlsx(folder / "Sales.xlsm")
    scan_folder(catalogue, "reports", folder)
    catalogue.resolve_refs()
    return REPORT


def test_keys_round_trip():
    key = pbiobjects.table_key("files://r/A.pbix", "Sales Orders")
    assert pbiobjects.split_key(key) == ("table", "files://r/A.pbix", "Sales Orders")
    assert pbiobjects.split_key(pbiobjects.query_key("files://r/B.xlsx", "Q::1")) == ("query", "files://r/B.xlsx", "Q::1")
    assert pbiobjects.split_key("files://r/A.pbix") is None
    assert pbiobjects.display_name(key) == "Sales Orders (in A.pbix)"


def test_table_view(catalogue, report):
    v = pbiobjects.table_view(catalogue, pbiobjects.table_key(report, "Sales"))
    assert not v["missing"] and not v["calculated"]
    assert [c["name"] for c in v["columns"]] == ["Units", "Big"] and v["columns"][1]["expression"]
    assert [m["name"] for m in v["measures"]] == ["Total Units"]
    assert catalogue.get_asset(v["measures"][0]["key"])["kind"] == "measure"
    [rel] = v["relationships"]
    assert (rel["column"], rel["other"], rel["side"]) == ("StoreId", "Store", "many")
    assert rel["other_key"] == pbiobjects.table_key(report, "Store")
    assert v["query"]["key"] == pbiobjects.query_key(report, "Sales")
    [step] = v["lineage"]
    assert any(s["key"] == SALES and s["catalogued"] for s in step["sources"])
    store = pbiobjects.table_view(catalogue, rel["other_key"])
    assert store["relationships"][0]["side"] == "one" and store["query"] is None
    assert store["used_on"][0] == {"page": "Overview", "visual": "Units by region", "fields": ["Region"]}
    assert pbiobjects.table_view(catalogue, pbiobjects.table_key(report, "Gone"))["missing"]
    assert pbiobjects.table_view(catalogue, pbiobjects.table_key("files://none.pbix", "Sales"))["missing"]


def test_query_view_in_report_and_workbook(catalogue, report, tmp_path):
    v = pbiobjects.query_view(catalogue, pbiobjects.query_key(report, "Sales"))
    assert "SalesDaily" in v["expression"] and v["loads"][0]["key"] == pbiobjects.table_key(report, "Sales")
    assert any(s["key"] == SALES for s in v["sources"])
    param = pbiobjects.query_view(catalogue, pbiobjects.query_key(report, "ServerName"))
    assert param["parameter"] == "DEMO-SQL01" and [u["name"] for u in param["used_by"]] == ["Sales"]
    # A workbook's queries: one builds on another, and the lineage follows it back.
    book = "files://reports/Sales.xlsm"
    store = pbiobjects.query_view(catalogue, pbiobjects.query_key(book, "Store list"))
    assert [u["name"] for u in store["uses"]] == ["Sales"]
    assert [s["query"] for s in store["lineage"]] == ["Store list", "Sales"]
    sales = pbiobjects.query_view(catalogue, pbiobjects.query_key(book, "Sales"))
    assert [u["name"] for u in sales["used_by"]] == ["Store list"]


def test_pbix_without_model_still_opens_its_queries(catalogue, tmp_path):
    folder = tmp_path / "pbix"
    folder.mkdir()
    write_pbix(folder / "Sales.pbix")
    scan_folder(catalogue, "pbix", folder)
    key = pbiobjects.query_key("files://pbix/Sales.pbix", "Store list")
    v = pbiobjects.query_view(catalogue, key)
    assert v["sql"] and v["uses"][0]["name"] == "Sales"


def test_followed_tables_show_a_readable_name(catalogue, report):
    key = pbiobjects.table_key(report, "Sales")
    followups.follow(catalogue, key)
    [f] = followups.follow_ups(catalogue)
    assert f["name"] == "Sales (in Sales.pbit)" and followups.kind_of(key, f["kind"]) == "pbi_table"


def _app(catalogue, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("PDCAT_DB", str(catalogue.path))
    return AppTest.from_file(str(Path(__file__).parents[1] / "src" / "pdcat" / "app.py"), default_timeout=15)


def test_pages_open(catalogue, report, monkeypatch):
    app = _app(catalogue, monkeypatch)
    for key, heading in ((pbiobjects.table_key(report, "Sales"), "Relationships (1)"),
                         (pbiobjects.table_key(report, "Store"), "Shown on pages (1)"),
                         (pbiobjects.query_key(report, "Sales"), "Code (M)"),
                         (pbiobjects.query_key(report, "ServerName"), "Used by (1)"),
                         (pbiobjects.query_key("files://reports/Sales.xlsm", "Store list"), "Lineage"),
                         (pbiobjects.table_key(report, "Gone"), "Notes")):
        app.query_params["asset"] = key
        app.run()
        assert not app.exception, key
        assert any(s.value.startswith(heading) for s in app.subheader), (key, [s.value for s in app.subheader])


def test_report_page_has_clickable_tables_and_queries(catalogue, report, monkeypatch):
    app = _app(catalogue, monkeypatch)
    app.query_params["asset"] = report
    app.run()
    assert not app.exception
    assert any(s.value.startswith("Model tables (2)") for s in app.subheader)
    labels = [b.label for b in app.button]
    assert any("ServerName = DEMO-SQL01" in lab for lab in labels)
    app.button(key=f"qparam-{report}-0").click().run()
    assert not app.exception
    assert app.query_params["asset"] == [pbiobjects.query_key(report, "ServerName")] or \
        app.query_params["asset"] == pbiobjects.query_key(report, "ServerName")


def test_search_shows_a_measures_report_and_its_dax(catalogue, tmp_path):
    from pdcat.searchview import rows_for
    from tests.test_v21_reports import write_report
    folder = tmp_path / "reports"
    folder.mkdir()
    write_report(folder / "Sales.pbit")
    scan_folder(catalogue, "reports", folder)
    rows = {r["name"]: r for r in rows_for(catalogue, [dict(r) for r in catalogue.search("units")])
            if r["kind"] == "measure"}
    assert rows["Sales[Units vs LY]"]["where"] == "reports › Sales.pbit"
    assert rows["Sales[Units vs LY]"]["description"] == "[Total Units] - [Units LY]"
    assert rows["Sales[Total Units]"]["description"] == "Units sold, net of returns"  # its own description first
    assert rows["Sales[Total Units]"]["_folder"] == f"🗂️ reports/{folder}"
