"""Pages keep working when pyarrow can't load (tables fall back to plain HTML)."""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from pdcat.ui import arrowsafe  # noqa: E402

APP = str(Path(__file__).parents[1] / "src" / "pdcat" / "app.py")
SALES = "sql://warehouse/forecastdb/dbo.salesdaily"


@pytest.fixture
def app(catalogue, monkeypatch):
    monkeypatch.setenv("PDCAT_DB", str(catalogue.path))
    monkeypatch.setenv("PDCAT_NO_ARROW", "1")
    arrowsafe.uninstall()  # earlier app tests leave Streamlit's guarded functions in place
    yield AppTest.from_file(APP, default_timeout=30)
    arrowsafe.uninstall()


def test_every_page_renders_without_arrow(app):
    from test_app import goto

    from pdcat.ui.main import sections
    app.run()
    assert not app.exception
    assert not app.dataframe  # no Arrow tables were sent
    for pages in sections().values():
        for page in pages:
            goto(app, page)
            assert not app.exception, (page, app.exception)
            assert not app.dataframe, page


def test_search_row_opens_from_the_drop_down(app):
    app.run()
    app.sidebar.text_input(key="sidebar-search").input("sales").run()
    assert not app.exception and app.title[0].value == "Search"
    assert any("dbo.SalesDaily" in m.value for m in app.markdown)
    box = [s for s in app.selectbox if s.key and s.key.startswith("arrowsafe-search-table-")][0]
    i = next(o for o in box.options if "SalesDaily" in o)
    box.set_value(box.options.index(i) - 1).run()
    assert not app.exception
    assert app.title[0].value.endswith("dbo.SalesDaily")


def test_item_page_without_arrow(app):
    app.query_params["asset"] = SALES
    app.run()
    assert not app.exception
    assert any("can't load on this PC" in c.value for c in app.caption)


def test_a_pyarrow_failure_while_drawing_falls_back(catalogue, monkeypatch):
    import streamlit as st
    monkeypatch.setenv("PDCAT_DB", str(catalogue.path))
    monkeypatch.delenv("PDCAT_NO_ARROW", raising=False)
    arrowsafe.uninstall()
    from streamlit.delta_generator import DeltaGenerator

    def broken(self, *a, **k):
        raise ImportError("DLL load failed while importing lib: The specified module could not be found.")
    monkeypatch.setattr(DeltaGenerator, "dataframe", broken)
    try:
        assert arrowsafe.install() is False
        app = AppTest.from_file(APP, default_timeout=30)
        app.run()
        app.sidebar.text_input(key="sidebar-search").input("sales").run()
        assert not app.exception
        assert any("dbo.SalesDaily" in m.value for m in app.markdown)
        assert arrowsafe._RUNTIME_ERROR
        assert st.dataframe.__func__ is DeltaGenerator.dataframe
    finally:
        arrowsafe.uninstall()
        monkeypatch.undo()
        st.dataframe = st._main.dataframe
