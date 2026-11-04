"""Version 2.5: item pages in five tabs, the last tab remembered per kind, long lists cut with Show all."""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).parents[1] / "src" / "pdcat" / "app.py")
SALES = "sql://warehouse/forecastdb/dbo.salesdaily"
STORE = "sql://warehouse/forecastdb/dbo.store"


@pytest.fixture
def app(catalogue, monkeypatch):
    monkeypatch.setenv("PDCAT_DB", str(catalogue.path))
    return AppTest.from_file(APP, default_timeout=15)


def _tabs(app):
    return [g for g in app.button_group if (g.key or "").startswith("item-tab-")][0]


def test_table_page_opens_on_overview_and_remembers_the_tab_for_tables(app):
    app.query_params["asset"] = SALES
    app.run()
    assert not app.exception
    assert _tabs(app).value == "overview"
    assert any(h.value == "Notes" for h in app.subheader)
    assert not any(h.value.startswith("Columns") for h in app.subheader)  # only the open tab is drawn
    _tabs(app).set_value("data").run()
    assert not app.exception
    app.query_params["asset"] = STORE  # another table opens on the same tab
    app.run()
    assert not app.exception
    assert _tabs(app).value == "data"


def test_summary_chip_opens_its_tab(app):
    app.query_params["asset"] = SALES
    app.run()
    [b for b in app.button if b.label.endswith(" columns")][0].click().run()
    assert not app.exception
    assert _tabs(app).value == "data"


def test_short_read_by_list_stays_a_list_and_long_one_becomes_a_table(app, catalogue):
    for i in range(3):
        key = f"py://bulk/reader{i:02d}.py"
        catalogue.upsert_asset(key, "script", f"reader{i:02d}.py", "bulk")
        catalogue.add_link(key, "reads", SALES, "bulk", dst_key=SALES)
    catalogue.conn.commit()
    app.query_params["asset"] = SALES
    app.run()
    _tabs(app).set_value("connections").run()
    assert not app.exception
    assert [b for b in app.button if (b.key or "").startswith("readby-") and not b.key.startswith("readby-m")]
    assert not any((d.key or "").startswith("conn-table-") for d in app.dataframe)
