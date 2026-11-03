"""Version 2.6: gold standard reports, measures and SQL, and what differs from them."""
from pathlib import Path

import pytest
from test_report_versions import write_pbit
from test_v214_scans import _pbit

from pdcat import edits, export, gold, review, sqllibrary
from pdcat.scan_files import scan_folder
from pdcat.store import Catalogue


def _measure(cat, report_stem: str, name: str) -> str:
    for r in cat.conn.execute("SELECT key, parent, name FROM assets WHERE kind = 'measure'"):
        if r["parent"].endswith(f"/{report_stem}.pbit") and r["name"].endswith(f"[{name}]"):
            return r["key"]
    raise KeyError((report_stem, name))


@pytest.fixture
def reports(tmp_path: Path):
    folder = tmp_path / "reports"
    _pbit(folder / "Sales.pbit", [("Total Units", "SUM(Sales[Units])"), ("Per Day", "[Total Units] / 7"),
                                  ("Avg Units", "AVERAGE(Sales[Units])")])
    _pbit(folder / "Stock.pbit", [("Total Units", "SUMX(Sales, Sales[Units])"), ("Per Day", "[Total Units] / 7")])
    _pbit(folder / "Plan.pbit", [("Total Units", "sum( Sales[Units] ) // same, other spacing"),
                                 ("Average Units", "AVERAGE(Sales[Units])")])
    cat = Catalogue(tmp_path / "c.db")
    scan_folder(cat, "reports", folder)
    yield cat
    cat.close()


def test_same_named_measures_are_checked_against_a_gold_measure(reports):
    cat = reports
    std = _measure(cat, "Sales", "Total Units")
    gold.mark(cat, std, "finance agreed this one")
    r = gold.related(cat, std)
    verdicts = {x["item"]["where"]: x["verdict"] for x in r["related"]}
    assert verdicts == {"Stock.pbit": "differs", "Plan.pbit": "same"}
    stock = next(x for x in r["related"] if x["item"]["where"] == "Stock.pbit")
    assert "SUMX" in stock["result"]["text_diff"] and stock["result"]["summary"] == "calculated differently"
    # the other report's page says so too
    [a] = gold.against(cat, _measure(cat, "Stock", "Total Units"))
    assert a["verdict"] == "differs" and a["gold_key"] == std


def test_same_dax_but_a_measure_underneath_differs(reports):
    cat = reports
    std = _measure(cat, "Sales", "Per Day")
    gold.mark(cat, std)
    [x] = gold.related(cat, std)["related"]
    assert x["item"]["where"] == "Stock.pbit" and x["verdict"] == "differs"
    assert "[Total Units] differs underneath" in x["result"]["summary"]


def test_same_calculation_under_another_name_is_a_name_difference(reports):
    cat = reports
    std = _measure(cat, "Sales", "Avg Units")
    gold.mark(cat, std)
    [x] = gold.related(cat, std)["related"]
    assert x["item"]["name"] == "Average Units" and x["why"] == "same DAX" and x["verdict"] == "name"


def test_a_gold_report_makes_its_measures_gold(reports):
    cat = reports
    sales = next(r[0] for r in cat.conn.execute("SELECT key FROM assets WHERE kind = 'report' AND name = 'Sales.pbit'"))
    gold.mark(cat, sales)
    r = gold.related(cat, sales)
    differ = {(x["item"]["where"], x["item"]["name"]) for x in r["measures"] if x["verdict"] == "differs"}
    assert differ == {("Stock.pbit", "Total Units"), ("Stock.pbit", "Per Day")}
    # a measure in the gold report says it counts as gold
    assert gold.gold_for(cat, _measure(cat, "Sales", "Total Units"))["key"] == sales
    # a measure marked by hand wins over the gold report's
    gold.mark(cat, _measure(cat, "Stock", "Total Units"))
    [a] = gold.against(cat, _measure(cat, "Sales", "Total Units"))
    assert a["gold_key"] == _measure(cat, "Stock", "Total Units") and a["via"] == "marked"


def test_ok_to_differ_holds_until_the_item_changes(reports, tmp_path):
    cat = reports
    std = _measure(cat, "Sales", "Total Units")
    stock = _measure(cat, "Stock", "Total Units")
    gold.mark(cat, std)
    gold.ok_to_differ(cat, std, stock, "stock counts differently on purpose")
    assert {x["item"]["key"]: x["verdict"] for x in gold.related(cat, std)["related"]}[stock] == "ok"
    _pbit(tmp_path / "reports" / "Stock.pbit", [("Total Units", "SUMX(Sales, Sales[Units] * 2)")])
    scan_folder(cat, "reports", tmp_path / "reports")
    assert {x["item"]["key"]: x["verdict"] for x in gold.related(cat, std)["related"]}[stock] == "differs"


def test_a_change_to_the_gold_standard_itself_is_flagged_until_accepted(reports, tmp_path):
    cat = reports
    std = _measure(cat, "Sales", "Total Units")
    gold.mark(cat, std)
    assert gold.related(cat, std)["changed"] is None
    _pbit(tmp_path / "reports" / "Sales.pbit", [("Total Units", "SUM(Sales[Units]) + 0")])
    scan_folder(cat, "reports", tmp_path / "reports")
    changed = gold.related(cat, std)["changed"]
    assert changed and "+ 0" in changed["text_diff"]
    gold.accept_change(cat, std)
    assert gold.related(cat, std)["changed"] is None


def test_differences_go_on_the_review_list_and_marks_export_and_undo(reports):
    cat = reports
    std = _measure(cat, "Sales", "Total Units")
    gold.mark(cat, std, "the one")
    review.refresh_tasks(cat)
    tasks = review.open_tasks(cat, rule="gold")
    assert [t["asset_key"] for t in tasks] == [_measure(cat, "Stock", "Total Units")]
    assert "differs from the gold standard Total Units" in tasks[0]["title"]
    data = export.export_data(cat)
    assert [g["key"] for g in data["gold_standards"]] == [std] and data["gold_standards"][0]["note"] == "the one"
    [e] = [e for e in edits.history(cat, std) if e["what"] == "gold standard"]
    edits.undo(cat, e["id"])
    assert gold.get(cat, std) is None


def test_report_copies_are_compared_with_a_gold_report(tmp_path):
    folder = tmp_path / "reports"
    write_pbit(folder / "Sales.pbit")
    write_pbit(folder / "team" / "Sales mine.pbit", extra_measures=[("Avg", "AVERAGE(Sales[Units])")])
    cat = Catalogue(tmp_path / "c.db")
    scan_folder(cat, "reports", folder)
    sales = "files://reports/Sales.pbit"
    gold.mark(cat, sales)
    [x] = gold.related(cat, sales)["related"]
    assert x["item"]["key"] == "files://reports/team/Sales mine.pbit" and x["verdict"] == "differs"
    measures = next(s for s in x["result"]["sections"] if s["title"] == "Measures")
    assert measures["extra"] == ["Sales[Avg]"]
    assert gold.against(cat, "files://reports/team/Sales mine.pbit")[0]["verdict"] == "differs"


def test_sql_alike_is_compared_with_a_gold_query(tmp_path):
    cat = Catalogue(tmp_path / "c.db")
    cat.save_snippet("Weekly sales", "SELECT s.StoreId, s.Units FROM dbo.Sales s JOIN dbo.Stores st "
                                     "ON st.StoreId = s.StoreId WHERE s.Region = 'North' AND s.Units > 0")
    cat.save_snippet("Weekly sales copy", "SELECT s.StoreId, s.Units FROM dbo.Sales s JOIN dbo.Stores st "
                                          "ON st.StoreId = s.StoreId WHERE s.Units > 0")
    cat.save_snippet("Unrelated", "SELECT ProductId, Name FROM dbo.Products")
    sqllibrary.build_library(cat)
    by_name = {q["name"]: q["uid"] for q in sqllibrary.queries(cat)}
    std = f"query://{by_name['Weekly sales']}"
    gold.mark(cat, std)
    [x] = gold.related(cat, std)["related"]
    assert x["item"]["name"] == "Weekly sales copy" and x["verdict"] == "differs"
    f = next(s for s in x["result"]["sections"] if s["title"] == "Filters")
    assert f["missing"] == ["s.region = 'north'"]
    assert "region" in x["result"]["text_diff"].lower()
    assert gold.against(cat, f"query://{by_name['Unrelated']}") == []


def test_filters_reads_where_conditions():
    assert gold.filters("SELECT a FROM t WHERE x=1 AND (y > 2) GROUP BY a") == ["x = 1", "y > 2"]


def test_only_reports_measures_and_sql_can_be_gold(tmp_path):
    cat = Catalogue(tmp_path / "c.db")
    cat.upsert_asset("sql://s/db/dbo.t", "table", "dbo.t", "s")
    with pytest.raises(ValueError):
        gold.mark(cat, "sql://s/db/dbo.t")


# ---- in the app ----------------------------------------------------------------------------------

@pytest.fixture
def app(reports, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("PDCAT_DB", str(reports.path))
    return AppTest.from_file(str(Path(__file__).parents[1] / "src" / "pdcat" / "app.py"), default_timeout=20)


def test_mark_a_measure_as_gold_on_its_page_and_see_the_other_differ(app, reports):
    from test_app import goto
    std = _measure(reports, "Sales", "Total Units")
    app.query_params["asset"] = std
    app.run()
    assert not app.exception
    [mark] = [b for b in app.button if b.label == "Mark as gold standard"]
    mark.click().run()
    assert not app.exception
    assert gold.get(reports, std)
    assert any("Gold standard" in s.value for s in app.success)
    assert any("Stock.pbit" in e.label for e in app.expander)
    app.query_params["asset"] = _measure(reports, "Stock", "Total Units")
    app.run()
    assert not app.exception
    assert any("Differs from the gold standard" in w.value for w in app.warning)
    app.query_params.pop("asset")
    app.run()
    goto(app, "Gold standards")
    assert not app.exception
    assert any("Stock.pbit" in e.label for e in app.expander)


def test_gold_button_on_measures_across_reports_and_sql_page(app, reports):
    from test_app import goto
    app.run()
    goto(app, "Measures across reports")
    assert not app.exception
    [b] = [b for b in app.button if b.label == "⭐ Make this the gold standard"][:1]
    b.click().run()
    assert not app.exception
    assert len(gold.all_golds(reports)) == 1
    reports.save_snippet("Weekly", "SELECT a, b FROM dbo.Sales WHERE a = 1")
    sqllibrary.build_library(reports)
    uid = sqllibrary.queries(reports)[0]["uid"]
    app.query_params["asset"] = f"query://{uid}"
    app.run()
    assert not app.exception
    assert any(b.label == "Mark as gold standard" for b in app.button)
