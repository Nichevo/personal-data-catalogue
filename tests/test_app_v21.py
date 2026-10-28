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
