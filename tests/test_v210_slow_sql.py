"""Version 2.10: Slow SQL. New slow-pattern checks, worst-first ranking by row counts, and the Slow SQL page."""
from pathlib import Path

import pytest

from pdcat import sqlchecks, sqllibrary
from pdcat.store import Catalogue

SALES = "sql://dw/salesdb/dbo.sales"
STORES = "sql://dw/salesdb/dbo.stores"


@pytest.fixture
def cat(tmp_path: Path):
    c = Catalogue(tmp_path / "c.db")
    for key, name, rows in ((SALES, "Sales", 5_000_000), (STORES, "Stores", 200)):
        c.upsert_asset(key, "table", f"dbo.{name}", "dw",
                       attrs={"object": name.lower(), "schema": "dbo", "database": "salesdb", "server": "srv",
                              "row_count": rows})
    c.set_columns(SALES, [{"name": "StoreCode", "data_type": "varchar(10)", "ordinal": 1},
                          {"name": "Qty", "data_type": "int", "ordinal": 2}])
    c.conn.commit()
    yield c
    c.close()


def _rules(sql: str) -> set[str]:
    return {r for r, _ in sqlchecks.check_sql(sql)}


@pytest.mark.parametrize("sql, rule", [
    ("SELECT a, (SELECT MAX(x) FROM dbo.b WHERE b.id = a.id) AS m FROM dbo.a", "select_subquery"),
    ("SELECT dbo.fnClean(name) FROM dbo.t", "scalar_udf"),
    ("SELECT a FROM dbo.t WHERE x = 1 AND (a = 1 OR b = 2)", "or_across_columns"),
    ("SELECT a FROM dbo.t t JOIN dbo.u u ON u.a = t.a OR u.b = t.b", "or_across_columns"),
    ("SELECT a FROM dbo.t WHERE EXISTS (SELECT 1 FROM dbo.u WHERE u.id = t.id OR u.id2 = t.id)",
     "or_across_columns"),
    ("CREATE VIEW dbo.v AS SELECT TOP 100 PERCENT a FROM dbo.t ORDER BY a", "order_in_view"),
    ("SELECT x FROM (SELECT a FROM dbo.t ORDER BY a) s", "order_in_view"),
    ("SELECT a FROM dbo.t t JOIN dbo.u u ON t.a + t.b = u.c", "join_on_expression"),
    ("IF (SELECT COUNT(*) FROM dbo.t WHERE a = 1) > 0 PRINT 'yes'", "count_for_exists"),
    ("SELECT a FROM dbo.t WHERE a IN (" + ", ".join(str(i) for i in range(60)) + ")", "long_in_list"),
    ("SELECT a FROM dbo.t WITH (INDEX(ix_a)) WHERE a = 1", "query_hints"),
    ("SELECT a FROM dbo.t OPTION (MAXDOP 1)", "query_hints"),
    ("SELECT a FROM dbo.t t INNER HASH JOIN dbo.u u ON u.id = t.id", "query_hints"),
])
def test_each_new_slow_check(sql, rule):
    assert rule in _rules(sql)


@pytest.mark.parametrize("sql", [
    "SELECT a FROM dbo.t WHERE a = 1 OR a = 2",                       # OR on one column is fine
    "SELECT ROW_NUMBER() OVER (ORDER BY a) AS n FROM dbo.t",          # ORDER BY in a window
    "SELECT a FROM dbo.t WHERE a IN (SELECT b FROM dbo.u)",
    "SELECT x FROM dbo.t CROSS APPLY dbo.fnSplit(x, ',') s",          # table function in APPLY
    "SELECT a FROM dbo.t t JOIN dbo.u u ON u.id = t.id AND u.d >= t.d - 1",
    "SELECT a FROM dbo.t GROUP BY a HAVING COUNT(*) > 0",
    "SELECT TOP 5 a FROM (SELECT TOP 10 a FROM dbo.t ORDER BY a) s ORDER BY a",
    "SELECT a FROM dbo.t WHERE note = 'x OR y = 1'",
])
def test_usual_sql_is_not_flagged_slow(sql):
    found = {r for r in _rules(sql) if sqlchecks.RULES[r][0] == "slow"} - {"select_star"}
    assert not found


def test_every_rule_has_a_severity_title_reason_and_link():
    for rule, (sev, title, why, article) in sqlchecks.RULES.items():
        assert sev in sqlchecks.SEVERITY_ORDER and title and why and article.startswith("https://"), rule


def test_catalogue_checks_big_table_and_hidden_conversion(cat):
    cat.save_snippet("whole sales", "SELECT StoreCode, Qty FROM dbo.Sales")
    cat.save_snippet("by code", "SELECT Qty FROM dbo.Sales s WHERE s.StoreCode = 1234")
    cat.save_snippet("small", "SELECT * FROM dbo.Stores")
    sqllibrary.build_library(cat)
    by_name = {q["name"]: sqllibrary.get(cat, q["uid"]) for q in sqllibrary.queries(cat)}
    rules = {n: {f["rule"]: f for f in sqlchecks.findings_for(cat, q)} for n, q in by_name.items()}
    assert "big_table_no_filter" in rules["whole sales"]
    assert "5,000,000" in rules["whole sales"]["big_table_no_filter"]["about"]
    assert rules["by code"]["implicit_conversion"]["about"].startswith("StoreCode")
    assert "big_table_no_filter" not in rules["small"]
    cat.set_setting("slow_sql_big_rows", 100)
    assert "big_table_no_filter" in {f["rule"] for f in sqlchecks.findings_for(cat, by_name["small"])}


def test_views_on_views(cat):
    for i, reads in enumerate(("dbo.Sales", "dbo.v1", "dbo.v2"), start=1):
        cat.upsert_asset(f"sql://dw/salesdb/dbo.v{i}", "view", f"dbo.v{i}", "dw",
                         attrs={"object": f"v{i}", "schema": "dbo", "database": "salesdb",
                                "definition": f"CREATE VIEW dbo.v{i} AS SELECT StoreCode FROM {reads} WHERE Qty > 0"})
    cat.conn.commit()
    cat.save_snippet("top", "SELECT StoreCode FROM dbo.v3 WHERE StoreCode = 'A'")
    sqllibrary.build_library(cat)
    q = next(sqllibrary.get(cat, q["uid"]) for q in sqllibrary.queries(cat) if q["name"] == "top")
    nested = [f for f in sqlchecks.findings_for(cat, q) if f["rule"] == "nested_views"]
    assert nested and nested[0]["about"] == "dbo.v3 → dbo.v2 → dbo.v1"
    [v3] = sqllibrary.queries_in(cat, "sql://dw/salesdb/dbo.v3")
    assert "nested_views" in {f["rule"] for f in sqlchecks.findings_for(cat, sqllibrary.get(cat, v3["uid"]))}
    [v2] = sqllibrary.queries_in(cat, "sql://dw/salesdb/dbo.v2")
    assert "nested_views" not in {f["rule"] for f in sqlchecks.findings_for(cat, sqllibrary.get(cat, v2["uid"]))}


def test_slow_sql_is_worst_first_and_can_be_accepted(cat):
    cat.save_snippet("small star", "SELECT * FROM dbo.Stores WHERE StoreCode = 'A'")
    cat.save_snippet("big star", "SELECT * FROM dbo.Sales WHERE Qty > 0")
    sqllibrary.build_library(cat)
    result = sqlchecks.slow_sql(cat)
    names = [q["name"] for q in result["queries"]]
    assert names.index("big star") < names.index("small star")
    assert result["queries"][0]["rows"] == 5_000_000 and result["queries"][0]["biggest"] == "dbo.Sales"
    assert len(result["by_rule"]["select_star"]["queries"]) == 2
    rows = sqlchecks.slow_csv_rows(result)
    assert {"query", "check", "biggest table", "rows"} <= set(rows[0])
    # slow findings stay off To review; accepting a slow check everywhere clears it from the page
    assert "select_star" not in sqlchecks.findings_by_rule(cat)
    assert sqlchecks.accept_rule_everywhere(cat, "select_star") == 2
    assert "select_star" not in sqlchecks.slow_sql(cat)["by_rule"]


def test_slow_sql_page_and_query_filter_draw(cat):
    from streamlit.testing.v1 import AppTest
    cat.save_snippet("big star", "SELECT * FROM dbo.Sales")
    sqllibrary.build_library(cat)
    db = str(cat.path)

    def app(db: str, page: str) -> None:
        from pdcat.store import Catalogue
        from pdcat.ui import slow_sql_page, sql_pages
        c = Catalogue(db)
        (slow_sql_page.slow_sql_page if page == "slow" else sql_pages.queries_page)(c)

    for page in ("slow", "list"):
        at = AppTest.from_function(app, args=(db, page)).run(timeout=60)
        assert not at.exception, at.exception
    at = AppTest.from_function(app, args=(db, "slow")).run(timeout=60)
    at.radio(key="slow-view").set_value("By check").run(timeout=60)
    assert not at.exception
    assert any("SELECT *" in e.label for e in at.expander)
