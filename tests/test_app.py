"""Smoke tests that drive the Streamlit page headlessly."""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).parents[1] / "src" / "pdcat" / "app.py")
SALES = "sql://warehouse/forecastdb/dbo.salesdaily"


@pytest.fixture
def app(catalogue, monkeypatch):
    monkeypatch.setenv("PDCAT_DB", str(catalogue.path))
    return AppTest.from_file(APP, default_timeout=10)


def test_search_page_lists_results(app):
    app.run()
    assert not app.exception
    app.text_input[0].input("sales").run()
    labels = [b.label for b in app.button]
    assert any("dbo.SalesDaily" in label for label in labels)


def test_detail_page_add_note(app, catalogue):
    app.query_params["asset"] = SALES
    app.run()
    assert not app.exception
    assert app.title[0].value.endswith("dbo.SalesDaily")
    area = [t for t in app.text_area if t.label == "Add a note"][0]
    area.input("Late on Mondays.")
    [b for b in app.button if b.label == "Add note"][0].click().run()
    assert not app.exception
    assert [n["body"] for n in catalogue.notes(SALES)] == ["Late on Mondays."]


def test_other_views_render(app):
    app.run()
    for view in ["Needs notes", "Recent notes", "Unmatched tables"]:
        app.sidebar.radio[0].set_value(view).run()
        assert not app.exception, view
