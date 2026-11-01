import textwrap

import pytest

from pdcat.codemap import build_graph, reached_data, to_dot, to_mermaid, uncatalogued_libraries
from pdcat.config import PythonSource
from pdcat.scan_python import module_names, scan_project

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"


def write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))


@pytest.fixture
def libs(tmp_path, catalogue, project):
    common = tmp_path / "common-utils"
    write(common, "src/common/__init__.py", "")
    write(common, "src/common/io.py", '''
        """Loading and saving shared by the forecasting projects."""
        import pandas as pd

        def load_sales(engine):
            return pd.read_sql("SELECT * FROM dbo.SalesDaily", engine)

        def save_out(df):
            df.to_csv("out/everything.csv")
    ''')
    write(common, "src/common/clean.py", '''
        from .io import load_sales
        from deep.util import stash

        def tidy(df):
            return stash(df)
    ''')
    deep = tmp_path / "deep-lib"
    write(deep, "deep/__init__.py", "")
    write(deep, "deep/util.py", '''
        def stash(df):
            df.to_sql("Deep", None, schema="ml")
    ''')
    secret = tmp_path / "vendored"
    write(secret, "secretlib/__init__.py", "")
    write(project, "src/model.py", f'''
        import sys
        sys.path.append(r"{secret}")
        import common.clean as cc
        from common.io import load_sales
        import secretlib
        import numpy
        import notinstalledlib_xyz

        def run(engine):
            return cc.tidy(load_sales(engine))
    ''')
    scan_project(catalogue, PythonSource(name="common", path=common, role="library"))
    scan_project(catalogue, PythonSource(name="deep", path=deep, role="library"))
    scan_project(catalogue, PythonSource(name="forecasting", path=project, default_database="warehouse/ForecastDB"))
    return catalogue


def test_module_names(tmp_path):
    write(tmp_path, "src/pkg/__init__.py", "")
    write(tmp_path, "src/pkg/sub/__init__.py", "")
    write(tmp_path, "scripts/run.py", "")
    assert module_names("src/pkg/sub/mod.py", tmp_path) == ["pkg.sub.mod"]
    assert module_names("src/pkg/__init__.py", tmp_path) == ["pkg"]
    assert module_names("scripts/run.py", tmp_path) == ["run", "scripts.run"]


def test_imports_link_across_projects_with_function_names(libs):
    links = {(l["dst_key"], l["evidence"]) for l in libs.links_from("py://forecasting/src/model.py")
             if l["relation"] == "imports"}
    assert ("py://common/src/common/io.py", "load_sales") in links
    assert ("py://common/src/common/clean.py", "tidy") in links
    # relative import inside the library, and a chain into a second library
    lib_links = {l["dst_key"] for l in libs.links_from("py://common/src/common/clean.py")}
    assert {"py://common/src/common/io.py", "py://deep/deep/util.py"} <= lib_links
    used_by = {l["src_key"] for l in libs.links_to("py://common/src/common/io.py")}
    assert "py://forecasting/src/model.py" in used_by


def test_function_refs_recorded(libs):
    attrs = libs.get_asset("py://common/src/common/io.py")["attrs"]
    assert attrs["function_refs"] == {"load_sales": [["reads", "table:dbo.salesdaily"]],
                                      "save_out": [["writes", "file:out/everything.csv"]]}


def test_data_reached_through_library_chain(libs):
    rows = reached_data(libs, "py://forecasting")
    reached = {(r["relation"], r["data_key"]) for r in rows}
    assert ("reads", SALES) in reached                       # load_sales reads dbo.SalesDaily
    assert ("writes", "table:ml.deep") in reached            # project → common.clean → deep.util
    assert not any("everything.csv" in r["data_key"] for r in rows)  # save_out is never called
    via = next(r["via"] for r in rows if r["data_key"] == "table:ml.deep")
    assert via == "src/model.py → src/common/clean.py (tidy) → deep/util.py (stash)"


def test_uncatalogued_libraries(libs):
    found = {m["name"]: m for m in uncatalogued_libraries(libs)}
    assert found["secretlib"]["how"] == "sys.path in your code" and found["secretlib"]["path"].endswith("vendored")
    assert found["notinstalledlib_xyz"]["how"] == "not installed here"
    assert "numpy" not in found and "pandas" not in found and "sys" not in found
    assert "common" not in found and "deep" not in found


def test_diagrams(libs):
    g = build_graph(libs, "forecasting", level="projects")
    names = {n["label"] for n in g.nodes.values()}
    assert {"forecasting", "common", "deep"} <= names
    assert g.nodes["src:common"]["kind"] == "library"
    dot = to_dot(g)
    assert dot.startswith("digraph") and "shape=component" in dot and "shape=cylinder" in dot
    mermaid = to_mermaid(g)
    assert mermaid.startswith("flowchart LR") and "[[\"common\"]]" in mermaid and '|"load_sales, tidy"|' in mermaid

    detail = build_graph(libs, "forecasting", level="modules", include_data=False)
    assert "py://deep/deep/util.py" in detail.nodes            # reached through the chain
    assert all(n["kind"] == "code" for n in detail.nodes.values())
    assert 'subgraph g' in to_mermaid(detail)


def test_removing_a_library_drops_its_links(libs):
    libs.add_library("deep", "x")
    libs.remove_library("deep")
    assert not any(l["dst_key"] and l["dst_key"].startswith("py://deep/")
                   for l in libs.links_from("py://common/src/common/clean.py"))


NESTED_PY = '''
"""Pipeline."""
import common.clean as cc
from common.io import load_sales as ls


def run(engine, days: int = 7, *, dry=False) -> "pd.DataFrame":
    """Load, tidy and return the sales.

    Longer explanation here.
    """
    def inner(df):
        return df.head(days)
    if dry:
        return None
    return inner(cc.tidy(ls(engine)))


class Job:
    def go(self):
        return self.helper()

    def helper(self):
        yield 1


if __name__ == "__main__":
    run(None)
'''


def test_function_info_and_import_map():
    from pdcat.scan_python import scan_python_source

    attrs = scan_python_source(textwrap.dedent(NESTED_PY)).attrs
    info = attrs["function_info"]
    assert set(info) == {"run", "run.inner", "Job.go", "Job.helper", "(script)"}
    run = info["run"]
    assert [p["name"] for p in run["params"]] == ["engine", "days", "dry"]
    assert run["params"][1] == {"name": "days", "annotation": "int", "default": "7"}
    assert run["returns_annotation"] == "'pd.DataFrame'"
    assert run["returns"] == ["None", "inner(cc.tidy(ls(engine)))"]
    assert set(run["calls"]) == {"inner", "cc.tidy", "ls"}            # inner's own calls aren't run's
    assert run["doc"].startswith("Load, tidy and return the sales.")
    assert info["run.inner"]["calls"] == ["df.head"]
    assert info["Job.helper"]["yields"] and info["(script)"]["calls"] == ["run"]
    assert attrs["import_map"] == {"cc": "common.clean", "ls": "common.io.load_sales"}


def test_calls_resolve_to_library_functions_and_callers(libs, project):
    from pdcat.codemap import CodeIndex

    write(project, "src/pipeline.py", NESTED_PY)
    scan_project(libs, PythonSource(name="forecasting", path=project, default_database="warehouse/ForecastDB"))
    idx = CodeIndex(libs)
    key = "py://forecasting/src/pipeline.py"
    calls = set(idx.calls(key, "run"))
    assert ("local", key, "run.inner") in calls
    assert ("lib", "py://common/src/common/clean.py", "tidy") in calls
    assert ("lib", "py://common/src/common/io.py", "load_sales") in calls
    assert idx.calls(key, "Job.go") == [("local", key, "Job.helper")]
    callers = idx.callers("py://common/src/common/io.py", "load_sales")
    assert (key, "run") in callers and ("py://forecasting/src/model.py", "run") in callers


def test_function_graph_zooms_in_and_out(libs, project):
    from pdcat.codemap import function_graph

    write(project, "src/pipeline.py", NESTED_PY)
    scan_project(libs, PythonSource(name="forecasting", path=project, default_database="warehouse/ForecastDB"))
    key = "py://forecasting/src/pipeline.py"
    whole = function_graph(libs, key)
    labels = {n["label"] for n in whole.nodes.values()}
    assert {"run", "run.inner", "Job.go", "Job.helper", "(script)", "tidy", "load_sales"} <= labels
    assert "stash" not in labels                                      # whole script stops at the first library call

    zoomed = function_graph(libs, key, "run", depth=3)
    assert zoomed.nodes[f"{key}::run"]["kind"] == "focus"
    assert "py://deep/deep/util.py::stash" in zoomed.nodes          # tidy -> stash, two files away
    assert ("table:ml.deep") in zoomed.nodes                           # and the table stash writes
    assert (f"{key}::(script)", f"{key}::run", "call") in zoomed.edges  # caller shown
    assert "Job.go" not in {n["label"] for n in zoomed.nodes.values()}
    shallow = function_graph(libs, key, "run", depth=1, include_data=False)
    assert "py://deep/deep/util.py::stash" not in shallow.nodes
    assert "(['run'])" not in to_mermaid(zoomed) and '(["run"])' in to_mermaid(zoomed)
    assert "fillcolor=\"#fde68a\"" in to_dot(zoomed)


def test_functions_loaded_by_a_module_manager(libs, project, tmp_path):
    """No import lines: a home-built loader is fed a dict of modules and the functions to load from each."""
    from pdcat.codemap import CodeIndex

    shared = tmp_path / "shared-code"
    write(shared, "tools/Management.py", '''
        def function1(df):
            return df

        def function2():
            return 2
    ''')
    write(project, "src/managed.py", '''
        modules_to_load = {
            'Management': {'local_folder': r'C:\\code\\local', 'sys_folder': r'\\\\server\\code',
                           'functions': ['function1', 'function2']},
            'NotScanned': {'local_folder': 'x', 'functions': ['elsewhere']},
        }
        ModuleManager(modules_to_load)

        def main(df):
            out = function1(df)
            elsewhere()
            return Management.function2(), out
    ''')
    scan_project(libs, PythonSource(name="shared", path=shared, role="library"))
    scan_project(libs, PythonSource(name="forecasting", path=project, default_database="warehouse/ForecastDB"))
    key = "py://forecasting/src/managed.py"
    lib = "py://shared/tools/Management.py"
    links = {(l["dst_key"], l["evidence"]) for l in libs.links_from(key) if l["relation"] == "imports"}
    assert (lib, "function1, function2") in links
    calls = set(CodeIndex(libs).calls(key, "main"))
    assert ("lib", lib, "function1") in calls and ("lib", lib, "function2") in calls
    assert ("other", "elsewhere") in calls          # its module wasn't scanned
    assert (key, "main") in CodeIndex(libs).callers(lib, "function1")


def test_function_subway_routes_follow_calls(libs, project):
    from pdcat.codemap import function_graph
    from pdcat.subway import function_subway, layout, subway_svg

    write(project, "src/pipeline.py", NESTED_PY)
    scan_project(libs, PythonSource(name="forecasting", path=project, default_database="warehouse/ForecastDB"))
    key = "py://forecasting/src/pipeline.py"
    g = function_subway(function_graph(libs, key, "run", depth=3), key)
    lay = layout(g)
    order, ys = lay["order"], lay["station_y"]
    # A function sits above the ones it calls, all the way down into the libraries.
    script, run, stash = f"{key}::(script)", f"{key}::run", "py://deep/deep/util.py::stash"
    assert ys[script] < ys[run] < ys[stash]
    route = lay["lines"][f"route:{script}"]
    assert route.kind == "route" and route.stops[0] == script and stash in route.stops
    assert lay["lines"]["table:ml.deep"].stops == [stash]              # the table stash writes is a line too
    assert set(order) == {n for n, d in g.nodes.items() if d["kind"] in ("function", "focus", "libfn", "external")}
    svg = subway_svg(g)
    assert 'href="?asset=py://deep/deep/util.py"' in svg               # another file's function opens it
    assert f'href="?asset={key}"' not in svg                           # this file's own functions don't
    assert "#fff7e0" in svg                                            # the zoomed-into function stands out


def test_function_subway_handles_circular_calls():
    from pdcat.codemap import Graph
    from pdcat.subway import function_subway, layout

    g = Graph()
    for fn in ("a", "b", "lonely"):
        g.nodes[f"f.py::{fn}"] = {"label": fn, "kind": "function", "group": "f.py"}
    g.edges[("f.py::a", "f.py::b", "call")]
    g.edges[("f.py::b", "f.py::a", "call")]
    lay = layout(function_subway(g, "f.py"))
    assert set(lay["order"]) == {"f.py::a", "f.py::b", "f.py::lonely"}  # nothing dropped, no endless loop
    assert {ln.id for ln in lay["lines"].values()} == {"route:f.py::a", "route:f.py::lonely"}
