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
