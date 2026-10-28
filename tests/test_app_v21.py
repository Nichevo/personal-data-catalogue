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
