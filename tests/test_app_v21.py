"""Version 2.1 pages, driven headlessly."""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from pdcat import groups, suggestions  # noqa: E402

APP = str(Path(__file__).parents[1] / "src" / "pdcat" / "app.py")
SALES = "sql://warehouse/forecastdb/dbo.salesdaily"
STORE = "sql://warehouse/forecastdb/dbo.store"


@pytest.fixture
def app(catalogue, monkeypatch):
    monkeypatch.setenv("PDCAT_DB", str(catalogue.path))
    return AppTest.from_file(APP, default_timeout=15)


def test_item_page_marks_a_link_not_important(app, catalogue):
    app.query_params["asset"] = STORE
    app.run()
    assert not app.exception
    [b for b in app.button if b.label == "Not important"][0].click().run()
    assert not app.exception
    from pdcat.links import unimportant
    assert unimportant(catalogue)
    assert any("not important" in b.label for b in app.button)


def test_item_page_not_relevant_and_follow_up(app, catalogue):
    from pdcat import curation, followups
    app.query_params["asset"] = STORE
    app.run()
    [b for b in app.button if b.label == "🙈 Not relevant"][0].click().run()
    assert curation.item_info(catalogue, STORE)["relevance"] == "not relevant"
    [b for b in app.button if b.label == "Follow up"][0].click().run()
    assert followups.following(catalogue, STORE)
    assert not app.exception


def test_group_page_compares_members(app, catalogue):
    uid = groups.create(catalogue, "backup", "Sales", {SALES: "main", STORE: "backup"}, main_key=SALES)
    app.query_params["asset"] = f"group://{uid}"
    app.run()
    assert not app.exception
    assert app.title[0].value.endswith("Sales")
    assert len(app.dataframe) >= 1


def test_suggestions_page_accepts_ticked(app, catalogue):
    from tests.test_app import goto
    suggestions.sync(catalogue, "family", [{"id": "family:x", "subject": SALES, "title": "SOH family", "payload": {
        "type": "family", "name": "SOH", "members": {SALES: "D", STORE: "W"}}}])
    app.run()
    goto(app, "Suggestions")
    assert not app.exception
    app.checkbox(key="sp-family:x").check().run()
    [b for b in app.button if b.label.startswith("Accept")][0].click().run()
    assert not app.exception
    assert groups.for_item(catalogue, STORE)


def test_sql_queries_page_and_query_page(app, catalogue):
    from pdcat import sqllibrary
    from tests.test_app import goto
    catalogue.save_snippet("Daily sales", "SELECT s.StoreId, s.Units FROM dbo.SalesDaily s "
                                          "JOIN dbo.Store st ON st.StoreId = s.StoreId WITH (NOLOCK)")
    assert sqllibrary.build_library(catalogue) >= 1
    app.run()
    goto(app, "SQL queries")
    assert not app.exception
    assert len(app.dataframe) == 1
    uid = next(q["uid"] for q in sqllibrary.queries(catalogue) if q["name"] == "Daily sales")
    app.query_params["asset"] = f"query://{uid}"
    app.run()
    assert not app.exception
    assert app.title[0].value.endswith("Daily sales")
    assert any("Reads with NOLOCK" in m.value for m in app.markdown)
    [b for b in app.button if b.label == "Fine here"][0].click().run()
    assert not app.exception
    from pdcat.sqlchecks import ok_marks
    assert ok_marks(catalogue, uid)
    app.query_params["asset"] = STORE
    app.run()
    assert not app.exception
    assert any("SQL using this table" in e.label for e in app.expander)


def test_database_page_naming_rules(app, catalogue):
    from pdcat import naming
    db = "sql://warehouse/forecastdb"
    assert catalogue.get_asset(db)
    app.query_params["asset"] = db
    app.run()
    assert not app.exception
    app.text_area(key=f"nr-text-{db}").input("Tables are PascalCase")
    [b for b in app.button if b.label == "Read the rules"][0].click().run()
    assert not app.exception
    [r] = [r for r in naming.rules(catalogue, db, all_states=True) if r["state"] == "proposed"]
    [b for b in app.button if b.label == "✓"][0].click().run()
    assert not app.exception
    assert naming.rules(catalogue, db)[0]["uid"] == r["uid"]
    assert any(d for d in app.dataframe)


def test_knowledge_packs_and_sap_page(app, catalogue):
    from tests.test_app import goto
    from pdcat import packs
    app.run()
    goto(app, "Knowledge")
    assert not app.exception
    [b for b in app.button if b.key == "pack-sqlserver"][0].click().run()
    assert not app.exception
    assert packs.status(catalogue)["sqlserver"] == len(packs.SQLSERVER_NOTES)
    goto(app, "SAP links")
    assert not app.exception
    assert app.title[0].value == "SAP links"


def test_item_page_rule_checks(app, catalogue):
    from pdcat import rulechecks
    app.query_params["asset"] = SALES
    app.run()
    assert not app.exception
    app.text_input(key=f"rc-text-{SALES}").input("Units is never negative")
    [b for b in app.button if b.label == "Add the check"][0].click().run()
    assert not app.exception
    [c] = rulechecks.checks_for(catalogue, SALES)
    assert c["rule"]["check"] == "min"
    [b for b in app.button if b.label == "Remove"][0].click().run()
    assert not rulechecks.checks_for(catalogue, SALES)


def test_ml_model_features_page_links(app, catalogue):
    from test_app import goto

    from pdcat import mlfeatures as M
    M.save_feature(catalogue, "sales_lag_7", project="forecasting", sources=[f"{SALES}#Units"],
                   built_by="py://forecasting/src/features.py::build", models=["model://forecasting/lgbm.txt"])
    app.run()
    goto(app, "ML model features")
    assert not app.exception
    assert app.title[0].value == "ML model features"
    labels = [b.label for b in app.button]
    assert "🧬 sales_lag_7" in labels
    assert any(lb.startswith("🐍 features.py › build()") for lb in labels)
    assert any(lb.startswith("🗄️ ") and "Units" in lb for lb in labels)
    [b for b in app.button if b.label.startswith("🗄️ ")][0].click().run()
    assert not app.exception
    assert app.query_params.get("asset") in (SALES, [SALES])


def test_home_page_follow_ups_quick_questions_and_combined_pages(app, catalogue):
    from test_app import goto

    from pdcat import followups
    from pdcat.ui.main import ALIASES
    followups.follow(catalogue, STORE, "check the regions")
    sid = suggestions.add(catalogue, "description", SALES, "dbo.SalesDaily: “daily sales”",
                          payload={"key": SALES, "text": "daily sales"})
    app.run()
    assert not app.exception and app.title[0].value == "Home"
    assert any("dbo.Store" in b.label for b in app.button)
    [b for b in app.button if b.key == f"qq-yes-description:{sid}" or b.key == f"qq-yes-suggestion:{sid}"][0].click().run()
    assert not app.exception
    assert suggestions.state(catalogue, sid) == "accepted"
    [b for b in app.button if b.key == "home-fu-done-0"][0].click().run()
    assert not followups.follow_ups(catalogue)
    for old in ALIASES:
        goto(app, old)
        assert not app.exception, old


def test_folder_page_contains_table(app, catalogue, tmp_path):
    from pdcat.scan_files import scan_folder
    root = tmp_path / "share"
    root.mkdir()
    for m in ("01", "02", "03"):
        (root / f"sales_2024{m}.csv").write_text("a,b\n1,2\n")
    (root / "notes.csv").write_text("a\n1\n")
    scan_folder(catalogue, "share", root)
    app.query_params["asset"] = "files://share"
    app.run()
    assert not app.exception
    df = app.dataframe(key="contains-files://share").value
    assert len(df) == 2 and "dated series" in set(df["type"])
    app.text_input(key="contains-filter-files://share").input("notes").run()
    assert len(app.dataframe(key="contains-files://share").value) == 1
