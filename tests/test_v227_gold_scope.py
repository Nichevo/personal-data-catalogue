"""Version 2.27: choose which reports a gold standard is compared with, and mark Power BI reports old."""
from pathlib import Path

import pytest
from test_report_versions import write_pbit
from test_v26_gold import _measure, reports  # noqa: F401  (fixture)

from pdcat import curation, edits, gold, groups, review
from pdcat.scan_files import scan_folder
from pdcat.store import Catalogue

SALES, OLD, MINE = ("files://reports/Sales.pbit", "files://reports/archive copies/Sales 2023.pbit",
                    "files://reports/team/Sales mine.pbit")


@pytest.fixture
def copies(tmp_path: Path):
    folder = tmp_path / "reports"
    write_pbit(folder / "Sales.pbit")
    write_pbit(folder / "archive copies" / "Sales 2023.pbit", extra_measures=[("Old", "SUM(Sales[Units])")])
    write_pbit(folder / "team" / "Sales mine.pbit", extra_measures=[("Avg", "AVERAGE(Sales[Units])")])
    cat = Catalogue(tmp_path / "c.db")
    scan_folder(cat, "reports", folder)
    yield cat
    cat.close()


def _related(cat, key):
    return {x["item"]["key"]: x["verdict"] for x in gold.related(cat, key)["related"]}


def test_old_versions_are_left_out_by_default_and_all_brings_them_back(copies):
    cat = copies
    gold.mark(cat, SALES)
    assert set(_related(cat, SALES)) == {OLD, MINE}
    groups.create(cat, "versions", "Sales", {SALES: None, OLD: None}, main_key=SALES)
    r = gold.related(cat, SALES)
    assert {x["item"]["key"] for x in r["related"]} == {MINE} and r["left_out"] == {OLD}
    assert OLD not in {d["key"] for d in gold.differences(cat)}
    assert gold.against(cat, OLD) == []
    gold.set_scope(cat, SALES, "all")
    assert set(_related(cat, SALES)) == {OLD, MINE}
    assert gold.against(cat, OLD)[0]["why"] == "another version"


def test_a_report_marked_old_is_left_out_everywhere(copies):
    cat = copies
    gold.mark(cat, SALES)
    curation.set_relevance(cat, OLD, "old")
    assert set(_related(cat, SALES)) == {MINE}
    assert OLD in groups.older_versions(cat)
    curation.set_relevance(cat, OLD, "")
    assert set(_related(cat, SALES)) == {OLD, MINE}


def test_leave_out_and_pick_reports_and_undo(copies):
    cat = copies
    gold.mark(cat, SALES)
    gold.leave_out(cat, SALES, OLD)
    assert set(_related(cat, SALES)) == {MINE}
    review.refresh_tasks(cat)
    assert {t["asset_key"] for t in review.open_tasks(cat, rule="gold")} == {MINE}
    gold.set_scope(cat, SALES, "picked", picked=[OLD])
    assert set(_related(cat, SALES)) == {OLD}
    gold.leave_out(cat, SALES, OLD)
    assert _related(cat, SALES) == {} and gold.scope_of(gold.get(cat, SALES))["picked"] == []
    last = edits.history(cat, SALES)[0]
    edits.undo(cat, last["id"])
    assert set(_related(cat, SALES)) == {OLD}
    # everything=True lists what can be picked, whatever the scope
    assert {x["item"]["key"] for x in gold.related(cat, SALES, everything=True)["related"]} == {OLD, MINE}
    row = next(r for r in gold.overview(cat) if r["key"] == SALES)
    assert row["scope"] == "only 1 picked report(s)" and row["left_out"] == 1


def test_a_gold_measure_scope_is_about_the_other_measures_report(reports):  # noqa: F811
    cat = reports
    std = _measure(cat, "Sales", "Total Units")
    stock = _measure(cat, "Stock", "Total Units")
    gold.mark(cat, std)
    assert gold.against(cat, stock)[0]["verdict"] == "differs"
    gold.leave_out(cat, std, gold.compared_key(gold.load(cat, stock)))
    assert {x["item"]["where"] for x in gold.related(cat, std)["related"]} == {"Plan.pbit"}
    assert gold.against(cat, stock) == []


def test_a_bad_scope_falls_back_to_current(copies):
    cat = copies
    gold.mark(cat, SALES)
    with cat.conn:
        cat.conn.execute("UPDATE gold_standards SET scope = 'not json' WHERE key = ?", (SALES,))
    assert gold.scope_of(gold.get(cat, SALES))["mode"] == "current"
    with pytest.raises(ValueError):
        gold.set_scope(cat, SALES, "nonsense")


@pytest.fixture
def copies_app(copies, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("PDCAT_DB", str(copies.path))
    return AppTest.from_file(str(Path(__file__).parents[1] / "src" / "pdcat" / "app.py"), default_timeout=20)


def test_compare_with_box_and_buttons_on_the_report_page(copies_app, copies):
    app = copies_app
    gold.mark(copies, SALES)
    app.query_params["asset"] = SALES
    app.run()
    assert not app.exception
    assert any(e.label.startswith("🎯 Compare with") for e in app.expander)
    [skip] = [b for b in app.button if b.label == "🚫 Don't compare this report"][:1]
    skip.click().run()
    assert not app.exception
    assert len(gold.scope_of(gold.get(copies, SALES))["skip"]) == 1
    [old] = [b for b in app.button if b.label == "🗄️ Mark report old"][:1]
    old.click().run()
    assert not app.exception
    assert _related(copies, SALES) == {}


def test_scripts_and_tables_marked_old_are_left_out_like_older_versions(tmp_path):
    from pdcat.codemap import hidden_from_map
    cat = Catalogue(tmp_path / "c.db")
    cat.upsert_asset("sql://s/db/dbo.sales_old", "table", "dbo.sales_old", "s")
    cat.upsert_asset("files://code/load_old.py", "script", "load_old.py", "code")
    cat.reindex_search("sql://s/db/dbo.sales_old")
    cat.reindex_search("files://code/load_old.py")
    for key in ("sql://s/db/dbo.sales_old", "files://code/load_old.py"):
        curation.set_relevance(cat, key, "old")
        assert key in groups.older_versions(cat)
        assert key in hidden_from_map(cat) and key not in hidden_from_map(cat, show_old=True)
    assert not [r for r in cat.search("sales_old")]
    assert [r["key"] for r in cat.search("sales_old", old_versions=True)] == ["sql://s/db/dbo.sales_old"]
    assert "files://code/load_old.py" not in {r["key"] for r in cat.list_assets(kind="script")}
    assert "files://code/load_old.py" in {r["key"] for r in cat.list_assets(kind="script", old_versions=True)}
    cat.close()


def test_diff_shows_the_gold_standard_green_and_the_other_red():
    from pdcat.ui.gold_pages import GOLD_LINE, OTHER_LINE, diff_html
    html = diff_html("--- gold standard: a\n+++ b\n@@ -1 +1 @@\n-SUM(x) <b>\n+SUMX(x)")
    gold_at, other_at = html.index("SUM(x) &lt;b&gt;"), html.index("SUMX(x)")
    assert html.rfind(GOLD_LINE, 0, gold_at) > html.rfind(OTHER_LINE, 0, gold_at)
    assert html.rfind(OTHER_LINE, 0, other_at) > html.rfind(GOLD_LINE, 0, other_at)


def _pbit_on(path, server: str, measures=(("Total Units", "SUM(Sales[Units])"),)):
    import copy
    import json
    import zipfile

    from test_office import TMSL, layout
    model = copy.deepcopy(TMSL)
    model["model"]["tables"][0]["measures"] = [{"name": n, "expression": e} for n, e in measures]
    model["model"]["expressions"][0]["expression"] = f'"{server}" meta [IsParameterQuery=true, Type="Text"]'
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Report/Layout", json.dumps(layout()).encode("utf-16-le"))
        z.writestr("DataModelSchema", json.dumps(model).encode("utf-16-le"))


def test_data_source_differences_are_flagged_for_measures_and_reports(tmp_path):
    folder = tmp_path / "reports"
    _pbit_on(folder / "Sales.pbit", "PROD-SQL01", [("Total Units", "SUM(Sales[Units])"), ("Stores", "COUNTROWS(Store)")])
    _pbit_on(folder / "Sales test.pbit", "TEST-SQL01",
             [("Total Units", "SUM(Sales[Units])"), ("Stores", "COUNTROWS(Store)")])
    _pbit_on(folder / "Sales same.pbit", "PROD-SQL01",
             [("Total Units", "SUM(Sales[Units])"), ("Stores", "COUNTROWS(Store)")])
    cat = Catalogue(tmp_path / "c.db")
    scan_folder(cat, "reports", folder)
    sales = "files://reports/Sales.pbit"
    assert "server PROD-SQL01" in gold.data_sources(gold.load(cat, sales)["attrs"])["sales"]
    # a gold measure: same DAX, but its table loads from another server
    std = next(r[0] for r in cat.conn.execute("SELECT key FROM assets WHERE kind = 'measure' AND parent = ? "
                                              "AND name LIKE '%Total Units%'", (sales,)))
    gold.mark(cat, std)
    got = {x["item"]["where"]: x for x in gold.related(cat, std)["related"]}
    assert got["Sales same.pbit"]["verdict"] == "same"
    test = got["Sales test.pbit"]
    assert test["verdict"] == "differs" and test["result"]["summary"] == "sales loads from somewhere else"
    assert "-server PROD-SQL01" in test["result"]["text_diff"] and "+server TEST-SQL01" in test["result"]["text_diff"]
    # a measure on a table whose source is the same everywhere (Store has no query) is not flagged
    gold.unmark(cat, std)
    stores = next(r[0] for r in cat.conn.execute("SELECT key FROM assets WHERE kind = 'measure' AND parent = ? "
                                                 "AND name LIKE '%Stores%'", (sales,)))
    gold.mark(cat, stores)
    assert {x["verdict"] for x in gold.related(cat, stores)["related"]} == {"same"}
    # a gold report: the data sources section
    gold.mark(cat, sales)
    rel = {x["item"]["name"]: x for x in gold.related(cat, sales)["related"]}
    titles = [s["title"] for s in rel["Sales test.pbit"]["result"]["sections"]]
    assert "Data sources" in titles and rel["Sales same.pbit"]["verdict"] == "same"
    cat.close()
