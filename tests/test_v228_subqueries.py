"""2.28: subqueries, CTEs and derived tables drawn as boxes that open on a query's field diagram."""
from __future__ import annotations

import pytest

from pdcat import sqllibrary
from pdcat.sqlparser import analyse
from pdcat.sqlparts import find_parts

SQL = """-- a comment with a bracket (
WITH recent AS (SELECT s.Material, SUM(s.Units) AS Units FROM dbo.Sales s WHERE s.Week > 10 GROUP BY s.Material),
big AS (SELECT Material FROM recent WHERE Units > 100)
SELECT p.Material, p.Name, x.Plant,
  (SELECT MAX(o.Date) FROM dbo.Orders o WHERE o.Material = p.Material) AS LastOrder
FROM dbo.Product p
JOIN (SELECT m.Material, m.Plant FROM dbo.MARC m
      WHERE m.Plant IN (SELECT Plant FROM dbo.Plants WHERE Region = 'EU (x)')) x ON x.Material = p.Material
WHERE p.Material IN (SELECT Material FROM big)
  AND NOT EXISTS (SELECT 1 FROM dbo.Blocked b WHERE b.Material = p.Material)
"""
TABLES = ["dbo.sales", "dbo.orders", "dbo.product", "dbo.marc", "dbo.plants", "dbo.blocked"]


def _by_label(found):
    return {p["label"]: p for p in found["parts"]}


def test_finds_each_kind_of_subquery_nested():
    found = find_parts(SQL, TABLES)
    parts = _by_label(found)
    assert parts["CTE recent"]["tables"] == ["dbo.sales"] and parts["CTE recent"]["gives"] == ["Material", "Units"]
    assert parts["CTE big"]["uses"] == ["recent"] and parts["CTE big"]["tables"] == []
    assert parts["Subquery in SELECT"]["tables"] == ["dbo.orders"]
    x = parts["Derived table x"]
    assert x["tables"] == ["dbo.marc"] and x["gives"] == ["Material", "Plant"]
    assert x["links"] == [{"column": "Material", "other": "p", "other_column": "Material"}]
    inner = [p for p in found["parts"] if p["parent"] == x["id"]]
    assert [(p["label"], p["tables"]) for p in inner] == [("IN subquery", ["dbo.plants"])]
    assert parts["NOT EXISTS subquery"]["tables"] == ["dbo.blocked"]
    assert found["outer"] == {"tables": ["dbo.product"], "uses": []}
    assert len(found["parts"]) == 7  # brackets in comments and quotes are not read


def test_plain_sql_has_no_parts_and_bad_input_never_raises():
    assert find_parts("SELECT a FROM dbo.Sales WHERE b IN (1, 2)", ["dbo.sales"])["parts"] == []
    assert find_parts("SELECT ((( FROM", [])["parts"] == []
    assert find_parts(None, [])["parts"] == []  # type: ignore[arg-type]
    assert find_parts("SELECT * FROM ((SELECT a FROM dbo.T) UNION (SELECT a FROM dbo.U)) z", ["dbo.t", "dbo.u"])


def _query(sql):
    a = analyse(sql)
    tables = [{"name": t["name"], "alias": t.get("alias"), "relation": "reads", "key": f"table://{t['name']}",
               "columns": a["columns"].get(t["name"], [])} for t in a["tables"]]
    return {"sql": sql, "tables": tables, "analysis": a}


def test_diagram_nests_tables_in_closed_subquery_boxes():
    els = sqllibrary.field_elements(_query(SQL))
    nodes = {e["data"]["id"]: e["data"] for e in els if "source" not in e["data"]}
    edges = [e["data"] for e in els if "source" in e["data"]]
    groups = {d["label"]: d for d in nodes.values() if d["id"].startswith("grp:sq:")}
    assert groups["Outer query"]["open"] is True
    assert all(d.get("closed") for k, d in groups.items() if k != "Outer query")
    assert nodes["table://dbo.product"]["parent"] == groups["Outer query"]["id"]
    assert nodes["table://dbo.marc"]["parent"] == groups["Derived table x"]["id"]
    assert groups["IN subquery"]["parent"] in (groups["Derived table x"]["id"], groups["Outer query"]["id"])
    assert "parent" not in groups["CTE recent"]  # CTEs sit beside the query that uses them
    assert not any(n in nodes for n in ("table://recent", "table://big", "sqlname:recent"))  # CTE names aren't tables
    assert any(e["relation"] == "uses" and e["label"] == "uses recent" for e in edges)
    assert any(e["target"] == groups["Derived table x"]["id"] and e["source"] == "table://dbo.product" for e in edges)
    ids = set(nodes)
    assert all(e["source"] in ids and e["target"] in ids for e in edges)


def test_table_read_in_two_places_is_drawn_in_each():
    sql = "SELECT a.x FROM dbo.Sales a WHERE a.x IN (SELECT b.x FROM dbo.Sales b WHERE b.y = 1)"
    nodes = [e["data"] for e in sqllibrary.field_elements(_query(sql)) if "source" not in e["data"]]
    sales = [d for d in nodes if d.get("key") == "table://dbo.sales"]
    assert len(sales) == 2 and len({d["parent"] for d in sales}) == 2


def test_query_without_subqueries_draws_as_before():
    sql = "SELECT s.Units FROM dbo.Sales s JOIN dbo.Stores st ON st.StoreId = s.StoreId"
    els = sqllibrary.field_elements(_query(sql))
    assert not any(e["data"]["id"].startswith("grp:") or "parent" in e["data"] for e in els)
    assert any(e["data"].get("relation") == "joins" for e in els)


def test_subquery_on_its_own_carries_the_ctes_it_needs():
    from pdcat.sqlparts import standalone
    parts = _by_label(find_parts(SQL, TABLES))
    big_in = next(p for p in find_parts(SQL, TABLES)["parts"] if p["uses"] == ["big"])
    one = standalone(SQL, TABLES, big_in["id"])
    assert one["ctes"] == ["recent", "big"]
    assert one["sql"].startswith("WITH recent AS (") and one["sql"].rstrip().endswith("SELECT Material FROM big")
    assert analyse(one["sql"])["tables"][0]["name"] == "dbo.sales"  # reads as SQL of its own
    correlated = standalone(SQL, TABLES, parts["NOT EXISTS subquery"]["id"])
    assert correlated["outer"] == ["p"] and correlated["ctes"] == []
    assert standalone(SQL, TABLES, "p99") is None


def test_subquery_page_opens_and_saves_as_its_own_sql(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from test_app import APP

    from pdcat.store import Catalogue
    cat = Catalogue(tmp_path / "c.db")
    cat.save_snippet("weekly", SQL)
    sqllibrary.build_library(cat)
    q = sqllibrary.queries(cat)[0]
    pid = _by_label(find_parts(SQL, [t["name"] for t in q["tables"]]))["Derived table x"]["id"]
    monkeypatch.setenv("PDCAT_DB", str(cat.path))
    app = AppTest.from_file(APP, default_timeout=30)
    app.query_params["asset"] = f"query://{q['uid']}"
    app.run()
    assert not app.exception
    assert any(b.label == "Open on its own" for b in app.button)
    app.query_params["asset"] = f"query://{q['uid']}/sub/{pid}"
    app.run()
    assert not app.exception
    assert any("Derived table x" in t.value for t in app.title)
    assert any("dbo.MARC" in c.value and "Plants" in c.value for c in app.code)
    app.button(key=f"sqsave-{q['uid']}-{pid}").click().run()
    assert not app.exception
    names = {r["name"] for r in cat.conn.execute("SELECT name FROM assets WHERE kind = 'sql_snippet'")}
    assert any(n.startswith("Derived table x of ") for n in names), names
    assert len(sqllibrary.queries(cat)) == 2  # the saved subquery is in the library as its own query
