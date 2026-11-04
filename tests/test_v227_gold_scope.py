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
