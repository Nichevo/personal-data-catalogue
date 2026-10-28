"""Version 2.1: the SQL query library, SQL using a table, field diagrams and SQL checks."""
from pathlib import Path

import pytest

from pdcat import sqlchecks, sqllibrary
from pdcat.config import PythonSource
from pdcat.scan_python import scan_project
from pdcat.store import Catalogue

SALES = "sql://dw/salesdb/dbo.sales"
STORES = "sql://dw/salesdb/dbo.stores"


@pytest.fixture
def cat(tmp_path: Path):
    c = Catalogue(tmp_path / "c.db")
    for key, name in ((SALES, "Sales"), (STORES, "Stores")):
        c.upsert_asset(key, "table", f"dbo.{name}", "dw",
                       attrs={"object": name.lower(), "schema": "dbo", "database": "salesdb", "server": "srv"})
    c.conn.commit()
    yield c
    c.close()


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "load.py").write_text(
        'import pandas as pd\n'
        '# Weekly sales by store\n'
        'WEEKLY = """SELECT s.StoreId, st.Region, s.Units AS qty\n'
        '  FROM dbo.Sales s JOIN dbo.Stores st ON st.StoreId = s.StoreId"""\n'
        'def run(con, region):\n'
        '    pd.read_sql(WEEKLY, con)\n'
        '    return pd.read_sql(f"SELECT * FROM dbo.Sales WHERE Region = \'{region}\'", con)\n')
    (root / "copy.py").write_text(
        'sales_sql = "select s.StoreId, st.Region, s.Units as qty from dbo.Sales s join dbo.Stores st on st.StoreId = s.StoreId"\n')
    (root / "cleanup.sql").write_text("-- clear the staging table\nDELETE FROM dbo.Sales\n")
    return root


def _weekly(cat):
    return next(q for q in sqllibrary.queries(cat) if q["places"] == 2)


def test_queries_are_collected_once_with_every_place(cat, tmp_path: Path):
    scan_project(cat, PythonSource(name="proj", path=_project(tmp_path)))
    cat.save_snippet("Stores list", "SELECT StoreId FROM dbo.Stores")
    assert sqllibrary.build_library(cat) == 4
    lib = {q["name"]: q for q in sqllibrary.queries(cat)}
    weekly = _weekly(cat)  # same SQL in load.py and copy.py, different case and spacing
    assert weekly["name"] in ("WEEKLY", "sales_sql")
    q = sqllibrary.get(cat, weekly["uid"])
    keys = {t["name"]: t["key"] for t in q["tables"]}
    assert keys == {"dbo.sales": SALES, "dbo.stores": STORES}
    assert q["analysis"]["joins"] and q["analysis"]["renames"][0]["alias"].lower() == "qty"
    assert "cleanup" in lib and "Stores list" in lib
    built = next(q for q in sqllibrary.queries(cat) if "WHERE Region" in q["sql"])
    assert sqllibrary.get(cat, built["uid"])["places"][0]["built_from_variables"]


def test_your_query_name_sticks_and_can_be_undone(cat, tmp_path: Path):
    scan_project(cat, PythonSource(name="proj", path=_project(tmp_path)))
    sqllibrary.build_library(cat)
    weekly = _weekly(cat)
    uid = weekly["uid"]
    sqllibrary.rename(cat, uid, "Weekly store sales")
    sqllibrary.build_library(cat)
    assert sqllibrary.get(cat, uid)["name"] == "Weekly store sales"
    from pdcat import edits
    [e] = edits.history(cat, f"query://{uid}")
    edits.undo(cat, e["id"])
    sqllibrary.build_library(cat)
    assert sqllibrary.get(cat, uid)["name"] == weekly["name"]


def test_sql_using_a_table_shows_how_and_puts_old_last(cat, tmp_path: Path):
    scan_project(cat, PythonSource(name="proj", path=_project(tmp_path)))
    sqllibrary.build_library(cat)
    rows = sqllibrary.using_table(cat, STORES)
    weekly = next(r for r in rows if r["uid"] == _weekly(cat)["uid"])
    assert weekly["relation"] == "reads" and "Region" in weekly["columns"]
    assert weekly["joins"][0]["other_key"] == SALES and weekly["joins"][0]["column"].lower() == "storeid"
    assert "changed recently" in weekly["active"]
    from pdcat.curation import set_relevance
    for item in ("py://proj/load.py", "py://proj/copy.py"):
        set_relevance(cat, item, "old")
    rows = sqllibrary.using_table(cat, STORES)
    assert rows[-1]["old"] and all(r["old"] for r in rows)


def test_queries_no_longer_found_are_kept_as_missing(cat, tmp_path: Path):
    root = _project(tmp_path)
    scan_project(cat, PythonSource(name="proj", path=root))
    sqllibrary.build_library(cat)
    (root / "cleanup.sql").write_text("-- nothing\n")
    scan_project(cat, PythonSource(name="proj", path=root))
    sqllibrary.build_library(cat)
    gone = [q for q in sqllibrary.queries(cat, include_missing=True) if q["status"] == "missing"]
    assert [q["name"] for q in gone] == ["cleanup"]


def test_field_diagram_has_tables_columns_and_join(cat, tmp_path: Path):
    scan_project(cat, PythonSource(name="proj", path=_project(tmp_path)))
    sqllibrary.build_library(cat)
    q = sqllibrary.get(cat, _weekly(cat)["uid"])
    els = sqllibrary.field_elements(q)
    nodes = {e["data"]["id"]: e["data"] for e in els if "source" not in e["data"]}
    assert SALES in nodes and "Units" in nodes[SALES]["label"]
    assert any(e["data"].get("relation") == "joins" for e in els)
    assert "Units → qty" in nodes["sqlout"]["label"] or "units → qty" in nodes["sqlout"]["label"].lower()


def test_sql_checks_go_to_review_and_can_be_marked_fine(cat, tmp_path: Path):
    scan_project(cat, PythonSource(name="proj", path=_project(tmp_path)))
    sqllibrary.build_library(cat)
    from pdcat.review import open_tasks, refresh_tasks
    refresh_tasks(cat)
    tasks = {t["asset_key"]: t for t in open_tasks(cat, limit=1000) if t["rule"] == "sql_check"}
    cleanup = next(q for q in sqllibrary.queries(cat) if q["name"] == "cleanup")
    built = next(q for q in sqllibrary.queries(cat) if "WHERE Region" in q["sql"])
    assert f"query://{cleanup['uid']}" in tasks and f"query://{built['uid']}" in tasks
    q = sqllibrary.get(cat, built["uid"])
    rules = {f["rule"] for f in sqlchecks.findings_for(cat, q)}
    assert {"built_from_variables", "select_star"} <= rules
    sqlchecks.mark_ok(cat, cleanup["uid"], "no_where_change", "dbo.sales")
    refresh_tasks(cat)
    assert f"query://{cleanup['uid']}" not in {t["asset_key"] for t in open_tasks(cat, limit=1000)
                                               if t["rule"] == "sql_check"}


@pytest.mark.parametrize("sql, rule", [
    ("SELECT * FROM t WITH (NOLOCK)", "nolock"),
    ("SELECT a FROM t WHERE b = NULL", "null_compare"),
    ("SELECT a.x FROM a LEFT JOIN b ON b.id = a.id WHERE b.kind = 'x'", "left_join_where"),
    ("SELECT TOP 5 a FROM t", "top_without_order"),
    ("SELECT a FROM t WHERE YEAR(d) = 2026", "function_on_column"),
    ("UPDATE dbo.t SET a = 1", "no_where_change"),
    ("SELECT a FROM srv.db.dbo.t", "linked_server"),
])
def test_each_check(sql, rule):
    assert rule in {r for r, _ in sqlchecks.check_sql(sql)}


def test_checks_ignore_text_inside_strings():
    assert sqlchecks.check_sql("SELECT a FROM t WHERE note = 'x = null with (nolock)'") == ()
