"""Version 2.13: the Power BI map."""
import json
import textwrap
import zipfile

import pytest
from test_office import layout, mashup, write_pbit

from pdcat.codemap import to_dot, to_mermaid
from pdcat.config import PythonSource
from pdcat.dataflow import flow_elements
from pdcat.powerbimap import report_map, reports
from pdcat.scan_files import scan_folder
from pdcat.scan_python import scan_project

UNKNOWN = '''section Section1;

shared Mystery = let
    Source = Sql.Database("OTHER-SQL", "Elsewhere"),
    t = Source{[Schema="dbo",Item="NotScanned"]}[Data]
in
    t;
'''


def write_pbix(path, section):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Report/Layout", json.dumps(layout()).encode("utf-16-le"))
        z.writestr("DataMashup", mashup(section))
        z.writestr("DataModel", b"\x00compressed")


@pytest.fixture
def reported(tmp_path, catalogue):
    """Sales.pbit loads dbo.SalesDaily, which a script writes from an API; Sales_old.pbit is an older version;
    Mystery.pbix loads only a table nobody catalogued."""
    loader = tmp_path / "loader"
    loader.mkdir()
    (loader / "load.py").write_text(textwrap.dedent('''
        import requests
        import pandas as pd
        def run(engine):
            df = pd.DataFrame(requests.get("https://api.pos.example.com/v1/sales").json())
            df.to_sql("SalesDaily", engine, schema="dbo", if_exists="append")
    '''))
    scan_project(catalogue, PythonSource(name="loader", path=loader, default_database="warehouse/ForecastDB"))
    folder = tmp_path / "reports"
    folder.mkdir()
    write_pbit(folder / "Sales.pbit")
    write_pbit(folder / "Sales_old.pbit")
    write_pbix(folder / "Mystery.pbix", UNKNOWN)
    scan_folder(catalogue, "reports", folder)
    with catalogue.conn:
        catalogue.conn.execute("INSERT INTO item_groups (uid, type, name, main_key) VALUES "
                               "('g1', 'versions', 'Sales', 'files://reports/Sales.pbit')")
        catalogue.conn.executemany("INSERT INTO group_members (group_uid, asset_key) VALUES ('g1', ?)",
                                   [("files://reports/Sales.pbit",), ("files://reports/Sales_old.pbit",)])
    return catalogue


def test_report_rows(reported):
    rows = {r["name"]: r for r in reports(reported)}
    assert set(rows) == {"Sales.pbit", "Mystery.pbix"}  # the old version is folded in
    sales = rows["Sales.pbit"]
    assert sales["older versions"] == 1 and sales["pages"] == 1 and sales["visuals"] == 1
    assert sales["measures"] >= 1 and sales["catalogued"] >= 1 and not sales["flag"]
    assert rows["Mystery.pbix"]["flag"] == "loads nothing catalogued"
    assert rows["Mystery.pbix"]["outside sources"] == 1  # the OTHER-SQL server


def test_map_runs_from_api_to_report(reported):
    g = report_map(reported)
    sales = g.nodes["files://reports/Sales.pbit"]
    assert "+1 older" in sales["label"] and sales["weight"] >= 1
    assert g.nodes["files://reports/Mystery.pbix"]["flag"]
    assert "files://reports/Sales_old.pbit" not in g.nodes
    edges = set(g.edges)
    table = "sql://warehouse/forecastdb/dbo.salesdaily"
    assert (table, "files://reports/Sales.pbit", "reads") in edges
    assert ("py://loader/load.py", table, "writes") in edges
    assert ("ext:api:api.pos.example.com", "py://loader/load.py", "reads") in edges
    # only what feeds reports: scripts that only read are left out
    assert "py://forecasting/src/features.py" not in g.nodes
    elements = flow_elements(g)
    assert any(e["data"].get("flag") for e in elements)
    assert "\\n" in to_dot(g) and "<br/>" in to_mermaid(g)


def test_a_report_with_no_sources_is_still_shown(tmp_path, catalogue):
    folder = tmp_path / "bare"
    folder.mkdir()
    with zipfile.ZipFile(folder / "Empty.pbit", "w") as z:
        z.writestr("Report/Layout", json.dumps(layout()).encode("utf-16-le"))
    scan_folder(catalogue, "bare", folder)
    assert reports(catalogue)[0]["flag"] == "no sources found"
    assert "files://bare/Empty.pbit" in report_map(catalogue).nodes


def test_no_reports(catalogue):
    assert reports(catalogue) == []
    assert report_map(catalogue).nodes == {}


def test_power_bi_map_page_renders(reported, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from test_app import APP, goto
    monkeypatch.setenv("PDCAT_DB", str(reported.path))
    app = AppTest.from_file(APP, default_timeout=15)
    app.run()
    goto(app, "Power BI map")
    assert not app.exception
    assert any(s.value == "Reports" for s in app.subheader)
    app.checkbox(key="pbimap-wb").check().run()
    assert not app.exception
