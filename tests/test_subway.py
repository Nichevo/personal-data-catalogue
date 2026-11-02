"""The subway map: the data flow drawn as coloured lines through the scripts that use them."""
import pytest

from pdcat.codemap import Graph
from pdcat.subway import layout, subway_svg


def etl() -> Graph:
    g = Graph()
    for k, kind in (("load.py", "code"), ("clean.py", "code"), ("report.pbix", "report"),
                    ("raw.csv", "file"), ("stage", "table"), ("final", "table"), ("log", "table")):
        g.nodes[k] = {"label": k, "kind": kind, "group": None, "key": k}
    for e in (("raw.csv", "load.py", "reads"), ("load.py", "stage", "writes"), ("stage", "clean.py", "reads"),
              ("clean.py", "final", "writes"), ("final", "report.pbix", "reads"),
              ("log", "clean.py", "reads"), ("clean.py", "log", "writes")):
        g.edges[e]
    return g


def test_stations_follow_run_order_and_lines_follow_the_data():
    lay = layout(etl())
    assert lay["order"] == ["load.py", "clean.py", "report.pbix"]
    lines = lay["lines"]
    assert lines["stage"].stops == ["load.py", "clean.py"] and lines["stage"].born
    assert lines["raw.csv"].stops == ["load.py"] and not lines["raw.csv"].born
    assert lines["final"].stops == ["clean.py", "report.pbix"]
    assert lines["log"].loops == {"clean.py"}  # read and written by the same script
    ys = lay["station_y"]
    assert ys["load.py"] < ys["clean.py"] < ys["report.pbix"]
    # Lines running side by side never share a track.
    for a in lines.values():
        for b in lines.values():
            if a is not b and a.track == b.track:
                assert a.bottom < b.top or b.bottom < a.top


def test_svg_links_to_items_and_survives_loops_between_scripts():
    g = etl()
    g.edges[("report.pbix", "load.py", "runs")]  # a loop: the report "runs" the first script
    svg = subway_svg(g)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert 'href="?asset=clean.py"' in svg and "stage" in svg


def test_about_page_and_subway_data_flow_render(catalogue, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from test_app import APP, goto
    monkeypatch.setenv("PDCAT_DB", str(catalogue.path))
    app = AppTest.from_file(APP, default_timeout=20)
    app.run()
    goto(app, "About")
    assert not app.exception
    assert app.title[0].value == "About"
    assert any(e.label.startswith("Version ") for e in app.expander)
    goto(app, "Data flow")
    app.radio(key="flow-view").set_value("subway").run()
    assert not app.exception
    app.checkbox(key="flow-versions").check().run()
    assert not app.exception


def test_about_detailed_map_names_real_functions(catalogue, monkeypatch):
    import importlib

    from pdcat.subway import subway_svg
    from pdcat.ui.about_page import DETAIL_ROUTES, DETAIL_STATIONS, detailed_map
    for _, where in DETAIL_STATIONS.values():
        module, func = where.rsplit(".", 1)
        assert hasattr(importlib.import_module(f"pdcat.{module}"), func), where
    names = set(detailed_map().nodes)
    assert all(a in names and b in names for a, b, _ in DETAIL_ROUTES)
    assert subway_svg(detailed_map()).startswith("<svg")
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from test_app import APP, goto
    monkeypatch.setenv("PDCAT_DB", str(catalogue.path))
    app = AppTest.from_file(APP, default_timeout=20)
    app.run()
    goto(app, "About")
    app.button_group(key="about-level").set_value("Detailed").run()
    assert not app.exception


def test_function_subway_marks_loops_and_recursion():
    import ast

    from pdcat.codemap import Graph
    from pdcat.scan_python import _function_info
    from pdcat.subway import function_subway, loop_note, subway_svg
    info = _function_info(ast.parse(
        "def walk(node):\n"
        "    for child in children(node):\n"
        "        walk(child)\n"
        "        clean(child)\n"
        "def run(rows):\n"
        "    return [score(r) for r in rows]\n"
        "def once():\n"
        "    load()\n"
        "class Tree:\n"
        "    def size(self):\n"
        "        return 1 + sum(c.size() for c in self.kids) + self.size_of_root()\n"
        "    def depth(self):\n"
        "        return self.depth()\n"))
    assert info["walk"]["recursive"] and info["walk"]["loops"] == 1
    assert info["walk"]["loop_calls"] == ["walk", "clean"]  # children() in the for header runs once
    assert info["run"]["loop_calls"] == ["score"] and not info["run"]["recursive"]
    assert info["once"]["loops"] == 0 and not info["once"]["recursive"]
    assert info["Tree.depth"]["recursive"] and not info["Tree.size"]["recursive"]
    assert loop_note({"kind": "function", **info["walk"]}) == "↻ calls itself · ↻ in a loop: walk, clean"
    assert loop_note({"kind": "table", **info["walk"]}) == ""
    g = Graph()
    for fn in ("walk", "once"):
        g.nodes[f"s::{fn}"] = {"label": fn, "kind": "function", "group": "s", **info[fn]}
    g.edges[("s::walk", "s::once", "call")]
    svg = subway_svg(function_subway(g, "s"))
    assert "↻ in a loop" in svg and "Calls itself" in svg


def test_helpers_are_told_apart_from_process_steps_and_folded_in():
    from pdcat.codemap import Graph
    from pdcat.subway import function_subway, helper_functions, subway_svg, without_helpers
    g = Graph()
    for fn in ("main", "load", "train", "save", "_clean_name", "get_config", "fmt", "log_step"):
        g.nodes[f"s::{fn}"] = {"label": fn, "kind": "function", "group": "s"}
    g.nodes["t:sales"] = {"label": "dbo.Sales", "kind": "table", "group": None}
    g.nodes["f:out"] = {"label": "out.csv", "kind": "file", "group": None}
    for a, b in [("main", "load"), ("main", "train"), ("main", "save"), ("load", "_clean_name"),
                 ("train", "get_config"), ("save", "fmt"), ("load", "log_step"), ("train", "log_step"),
                 ("save", "log_step")]:
        g.edges[(f"s::{a}", f"s::{b}", "call")]
    g.edges[("s::load", "t:sales", "reads")]
    g.edges[("s::get_config", "f:out", "reads")]     # a helper that does read something: train takes it over
    g.edges[("s::save", "f:out", "writes")]
    h = helper_functions(g)
    assert set(h) == {"s::_clean_name", "s::fmt", "s::log_step"}
    assert "s::get_config" not in h                   # it reads a file itself, so it's a step
    folded = without_helpers(g, h)
    assert "s::log_step" not in folded.nodes and folded.nodes["s::load"]["helpers"] == ["_clean_name", "log_step"]
    assert ("s::main", "s::load", "call") in folded.edges
    svg = subway_svg(function_subway(folded, "s"))
    assert "+2 helpers" in svg and "log_step" in svg
