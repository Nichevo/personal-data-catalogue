"""Fixes after testing 2.1: paths and table names built from variables, pasted SQL on the tables it uses,
joins written in WHERE, and suggestions that say what accepting them does."""
from pathlib import Path

import pytest

from pdcat.scan_python import scan_python_source
from pdcat.sqlparser import _with_regex, analyse
from pdcat.sqlrefs import extract_refs


def writes(src: str) -> list[str]:
    return [ref for rel, ref, _ in scan_python_source(src).refs if rel == "writes"]


def tables(src: str) -> list[str]:
    return [ref for _, ref, _ in scan_python_source(src).refs if ref.startswith("table:")]


@pytest.mark.parametrize("src, expected", [
    ("sDataFolder = 'C:/data/'\nsfilename = 'out.csv'\ndf.to_csv(sDataFolder + sfilename, index=False)",
     "file:C:/data/out.csv"),
    ("import datetime\nsDataFolder = 'C:/data/'\nsfilename = 'fc_' + datetime.date.today().strftime('%Y%m%d') + '.csv'"
     "\ndf.to_csv(sDataFolder + sfilename, index=False)", "file:C:/data/fc_{}.csv"),
    ("def save(df, sDataFolder, sfilename):\n    df.to_csv(sDataFolder + sfilename, index=False)\n"
     "save(df, 'C:/data/', 'out.csv')", "file:C:/data/out.csv"),
    ("import os\nsDataFolder = os.getenv('DATA')\nsfilename = 'out.csv'\ndf.to_csv(sDataFolder + sfilename)",
     "file:{}/out.csv"),
    ("sDataFolder = 'C:/data/'\nsfilename = ''\nsfilename = 'out.csv'\ndf.to_csv(sDataFolder + sfilename)",
     "file:C:/data/out.csv"),
    ("sDataFolder = 'C:/data/'\nsfilename = 'out'\nsfilename += '.csv'\ndf.to_csv(sDataFolder + sfilename)",
     "file:C:/data/out.csv"),
    ("sDataFolder = 'C:/data/'\nfor r in regions:\n    sfilename = r + '_fc.csv'\n"
     "    df.to_csv(sDataFolder + sfilename)", "file:C:/data/{}_fc.csv"),
])
def test_output_file_built_from_variables(src, expected):
    assert writes(src) == [expected]


def test_a_path_that_could_be_any_file_is_left_to_ask():
    found = scan_python_source("df.to_csv(folder + name + '.csv')")
    assert not [r for r in found.refs if r[0] == "writes"]
    assert found.attrs.get("unknown_files")


@pytest.mark.parametrize("src, expected", [
    ("sSchema = 'dbo'\nsTable = 'Sales'\nsql = 'SELECT * FROM ' + sSchema + '.' + sTable\npd.read_sql(sql, conn)",
     "table:dbo.sales"),
    ("def load(conn):\n    sSchema = 'fc'\n    sTable = 'Forecast'\n"
     "    return pd.read_sql(f'SELECT a FROM {sSchema}.{sTable} WHERE x = 1', conn)", "table:fc.forecast"),
    ("q = 'SELECT * FROM {}.{}'.format('dbo', 'Orders')\ncur.execute(q)", "table:dbo.orders"),
    ("t = 'Stock'\ncur.execute('SELECT * FROM dbo.%s' % t)", "table:dbo.stock"),
    ("import os\ns = os.getenv('S')\ncur.execute(f'SELECT * FROM {s}.Demand')", "table:demand"),
    ("def get(conn, sTable, sSchema='dbo'):\n    return pd.read_sql('SELECT * FROM ' + sSchema + '.' + sTable, conn)\n"
     "get(c, 'Plants')", "table:dbo.plants"),
    ("tbl = 'Items'\nsql = 'SELECT * '\nsql += 'FROM dbo.' + tbl\ncur.execute(sql)", "table:dbo.items"),
])
def test_sql_table_and_schema_from_variables(src, expected):
    assert expected in tables(src)


def test_dynamic_names_in_sql():
    assert [r.name for r in extract_refs("SELECT * FROM {}.Sales", dynamic=True)] == ["sales"]
    assert [r.name for r in extract_refs("SELECT * FROM dbo.Snap_{}", dynamic=True)] == ["dbo.snap_{}"]
    assert extract_refs("SELECT * FROM {}.{}", dynamic=True) == []
    assert extract_refs("SELECT * FROM {}.Sales") == []  # pasted SQL and .sql files stay strict


@pytest.mark.parametrize("reader", [analyse, _with_regex])
@pytest.mark.parametrize("sql", [
    "SELECT * FROM dbo.Sales s, dbo.Product p WHERE s.ProductID = p.ProductID",
    "SELECT a FROM dbo.Sales s JOIN dbo.Product p ON LTRIM(s.ProductID) = p.ProductID",
])
def test_joins_in_where_and_around_functions(reader, sql):
    joins = reader(sql)["joins"]
    assert {(j["left_table"], j["right_table"]) for j in joins} == {("dbo.sales", "dbo.product")}


def test_join_to_a_subquery():
    joins = analyse("SELECT a FROM sales s LEFT JOIN (SELECT * FROM product) p ON s.id = p.id")["joins"]
    assert [(j["left_table"], j["right_table"]) for j in joins] == [("sales", "product")]


def test_pasted_sql_on_unscanned_tables(catalogue):
    from pdcat import sqllibrary
    key = catalogue.save_snippet("Stock by plant", "SELECT s.a FROM inv.Stock s JOIN inv.Plant p ON s.PlantId = p.PlantId")
    sqllibrary.build_library(catalogue)
    q = sqllibrary.get(catalogue, sqllibrary.uid_for(catalogue.get_asset(key)["attrs"]["sql"]))
    edges = [e for e in sqllibrary.field_elements(q, catalogue) if "source" in e["data"]]
    assert len(edges) == 1
    assert catalogue.conn.execute("SELECT 1 FROM links WHERE src_key = ? AND dst_ref = 'table:inv.stock'",
                                  (key,)).fetchone()


def test_suggestions_say_what_accept_does():
    from pdcat import suggestions
    for kind in suggestions.KIND_LABELS:
        what, does = suggestions.help_for(kind)
        assert what and does and does != "applies it.", kind


APP = str(Path(__file__).parents[1] / "src" / "pdcat" / "app.py")


@pytest.fixture
def app(catalogue, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("PDCAT_DB", str(catalogue.path))
    return AppTest.from_file(APP, default_timeout=15)


def test_unscanned_table_page_lists_the_sql_using_it(app, catalogue):
    catalogue.save_snippet("Stock by plant", "SELECT s.a FROM inv.Stock s JOIN inv.Plant p ON s.PlantId = p.PlantId")
    app.query_params["asset"] = "table:inv.stock"
    app.run()
    assert not app.exception
    assert any("Used by" in s.value for s in app.subheader)
    assert any("Stock by plant" in b.label for b in app.button)
    assert any("inv.Plant" in c.value for c in app.code)


def test_pasted_sql_page_has_tables_and_diagram(app, catalogue):
    key = catalogue.save_snippet("Stock by plant", "SELECT s.a FROM inv.Stock s JOIN inv.Plant p ON s.PlantId = p.PlantId")
    app.query_params["asset"] = key
    app.run()
    assert not app.exception
    assert any("Tables it uses" in s.value for s in app.subheader)
    assert any("inv.stock" in b.label for b in app.button)
    assert any("Diagram" in m.value for m in app.markdown)
