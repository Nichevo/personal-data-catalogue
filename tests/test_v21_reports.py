"""Version 2.1: Power BI measures as a table (used / not used in this file), wireframes and screenshots."""
import copy
import json
import zipfile
from pathlib import Path

import pytest

from pdcat.measures import dax_refs, measure_rows, wireframe_svg
from pdcat.scan_files import scan_folder
from tests.test_office import TMSL, layout


def write_report(path: Path) -> None:
    model = copy.deepcopy(TMSL)
    model["model"]["tables"][0]["measures"] += [
        {"name": "Units LY", "expression": "CALCULATE([Total Units], SAMEPERIODLASTYEAR('Date'[Date]))"},
        {"name": "Units vs LY", "expression": "[Total Units] - [Units LY]", "displayFolder": "Growth"},
        {"name": "Old test", "expression": "COUNTROWS(Sales) // [Total Units]"}]
    lay = layout()
    lay["sections"][0]["width"], lay["sections"][0]["height"] = 1280, 720
    vc = lay["sections"][0]["visualContainers"][0]
    vc.update({"x": 10, "y": 20, "width": 400, "height": 300})
    cfg = json.loads(vc["config"])
    cfg["singleVisual"]["prototypeQuery"]["Select"].append(
        {"Measure": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": "Units vs LY"}, "Name": "x"})
    vc["config"] = json.dumps(cfg)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Report/Layout", json.dumps(lay).encode("utf-16-le"))
        z.writestr("DataModelSchema", json.dumps(model).encode("utf-16-le"))


@pytest.fixture
def report(catalogue, tmp_path):
    folder = tmp_path / "reports"
    folder.mkdir()
    write_report(folder / "Sales.pbit")
    scan_folder(catalogue, "reports", folder)
    return "files://reports/Sales.pbit"


def test_dax_refs():
    names = {"total units": "Sales[Total Units]", "units ly": "Sales[Units LY]"}
    m, c = dax_refs("CALCULATE([Total Units], 'Date'[Date] > 1) + Sales[Units LY] // [nope]", names)
    assert m == ["Sales[Total Units]", "Sales[Units LY]"] and c == ["Date[Date]"]


def test_measures_used_through_every_step(catalogue, report):
    rows = {r["measure"]: r for r in measure_rows(catalogue, report)}
    assert rows["Units vs LY"]["why"] == "shown in a visual"
    assert rows["Units LY"]["used"] and rows["Units LY"]["why"].startswith("used by")
    assert rows["Total Units"]["used"]
    assert not rows["Old test"]["used"] and rows["Old test"]["why"] == "not used in this file"
    assert rows["Units vs LY"]["folder"] == "Growth"
    assert rows["Units vs LY"]["uses measures"] == "Sales[Total Units], Sales[Units LY]"


def test_wireframe_draws_visuals(catalogue, report):
    page = catalogue.get_asset(report)["attrs"]["pages"][0]
    assert page["visuals"][0]["pos"] == [10, 20, 400, 300]
    svg = wireframe_svg(page, 0)
    assert "<rect" in svg and "Units by region" in svg and "clustered bar chart" in svg


def test_screenshots_stay_outside_the_catalogue(catalogue, report):
    from pdcat import screenshots
    name = screenshots.add(catalogue, report, "Overview", b"\x89PNG fake", "image/png")
    [s] = screenshots.for_page(catalogue, report, "Overview")
    assert s["path"].is_file() and not str(s["path"]).startswith(str(catalogue.path) + "/")
    screenshots.remove(catalogue, report, "Overview", name)
    assert screenshots.for_page(catalogue, report, "Overview") == [] and not s["path"].exists()


def test_report_page_shows_measures_table_and_wireframe(catalogue, report, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("PDCAT_DB", str(catalogue.path))
    app = AppTest.from_file(str(Path(__file__).parents[1] / "src" / "pdcat" / "app.py"), default_timeout=15)
    app.query_params["asset"] = report
    app.run()
    assert not app.exception
    assert any(s.value.startswith("Measures (4)") for s in app.subheader)
    assert any("<svg" in m.value for m in app.markdown)
    app.toggle(key=f"munused-{report}").set_value(True).run()
    assert not app.exception
