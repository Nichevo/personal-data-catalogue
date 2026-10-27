"""Smoke tests that drive the Streamlit page headlessly."""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).parents[1] / "src" / "pdcat" / "app.py")
SALES = "sql://warehouse/forecastdb/dbo.salesdaily"


def goto(app, page: str):
    """Pick a page from the sidebar: its section first, then the page."""
    from pdcat.ui.main import sections
    section = next(name for name, pages in sections().items() if page in pages)
    app.sidebar.selectbox(key="nav-section").set_value(section).run()
    app.sidebar.radio(key=f"nav-{section}").set_value(page).run()
    return app


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
    from pdcat.ui.main import sections
    for pages in sections().values():
        for view in pages:
            goto(app, view)
            assert not app.exception, (view, app.exception)


def test_add_sql_page_saves_and_opens_snippet(app, catalogue):
    app.run()
    goto(app, "Add SQL")
    assert not app.exception
    app.text_input[0].input("Store regions")
    [t for t in app.text_area if t.label == "SQL"][0].input("SELECT Region FROM dbo.Store")
    [b for b in app.button if b.label == "Save SQL"][0].click().run()
    assert not app.exception
    key = app.query_params["asset"]
    key = key[0] if isinstance(key, list) else key
    assert catalogue.get_asset(key)["name"] == "Store regions"
    assert any("dbo.Store" in b.label for b in app.button)  # detail page shows the linked table


def test_data_folder_page_adds_and_scans(app, catalogue, tmp_path):
    import pandas as pd

    folder = tmp_path / "extracts"
    folder.mkdir()
    pd.DataFrame({"StoreId": [1, 2], "Units": [3.0, 4.0]}).to_csv(folder / "sales.csv", index=False)
    app.run()
    goto(app, "Data folders")
    [t for t in app.text_input if t.label == "Folder path"][0].input(f'"{folder}"')
    [t for t in app.text_input if t.label == "Short name"][0].input("extracts")
    [b for b in app.button if b.label == "Add and scan"][0].click().run()
    assert not app.exception
    assert catalogue.get_asset("files://extracts/sales.csv")["attrs"]["row_count"] == 2

    # The file page shows its profile and can be compared with the SQL table.
    catalogue.save_profile(SALES, [{"name": "StoreId", "rows": 1200, "nulls": 0, "min": 1, "max": 40},
                                   {"name": "Units", "rows": 1200, "nulls": 5, "min": 0, "max": 99}], "test")
    app.query_params["asset"] = "files://extracts/sales.csv"
    app.run()
    assert not app.exception
    assert any("Compare with" in e.label for e in app.expander)
    assert len(app.dataframe) >= 2  # profile + comparison


def test_issue_note_shows_in_data_issues(app, catalogue):
    app.query_params["asset"] = SALES
    app.run()
    type_radio = [r for r in app.radio if r.label == "Type"][-1]
    type_radio.set_value("issue")
    [t for t in app.text_area if t.label == "Add a note"][0].input("Duplicate rows for 2026-03-01.")
    [b for b in app.button if b.label == "Add note"][0].click().run()
    assert not app.exception
    assert catalogue.open_issues()[0]["body"] == "Duplicate rows for 2026-03-01."
    [b for b in app.button if b.label == "Mark resolved"][0].click().run()
    assert not app.exception and catalogue.open_issues() == []


def test_review_page_comment_marks_done(app, catalogue):
    from pdcat.review import open_tasks, refresh_tasks

    refresh_tasks(catalogue)
    before = len(open_tasks(catalogue, limit=1000))
    app.run()
    goto(app, "To review")
    assert not app.exception
    assert len(app.dataframe) == 1  # the compact table is the default
    app.toggle[0].set_value(True).run()  # cards
    [t for t in app.text_area if t.label == "Your comment"][0].input("Checked: this is the store master.")
    [b for b in app.button if b.label == "Save"][0].click().run()
    assert not app.exception
    assert len(open_tasks(catalogue, limit=1000)) == before - 1
    assert any(n["body"] == "Checked: this is the store master." for n in catalogue.recent_notes())
    [b for b in app.button if b.label == "💤 Snooze 30 days"][0].click().run()
    assert len(open_tasks(catalogue, limit=1000)) == before - 2


def test_hotspots_page_renders(app):
    app.run()
    goto(app, "Issue hotspots")
    assert not app.exception
    assert len(app.dataframe) == 1


def test_code_map_page_renders(app, catalogue):
    app.run()
    goto(app, "Code map")
    assert not app.exception
    app.selectbox[0].set_value("forecasting").run()
    assert not app.exception
    assert any(b.label.startswith("⬇️ Mermaid") for b in app.get("download_button"))


def test_script_page_zooms_into_a_function(app, catalogue):
    key = "py://forecasting/src/features.py"
    app.query_params["asset"] = key
    app.run()
    assert not app.exception
    zoom = [s for s in app.selectbox if s.label == "Zoom"][0]
    assert zoom.value == "(whole script)"
    assert any(d.value.shape[0] == 2 for d in app.dataframe)          # load and save
    zoom.set_value("load").run()
    assert not app.exception
    assert app.query_params["fn"] in ("load", ["load"])
    assert any("def load(engine)" in c.value for c in app.code)
    assert any("dbo.SalesDaily" in b.label for b in app.button)        # the table load reads
    [b for b in app.button if b.label.startswith("🔭 Zoom out")][0].click().run()
    assert not app.exception
    assert [s for s in app.selectbox if s.label == "Zoom"][0].value == "(whole script)"


def test_script_page_opens_at_function_from_link(app):
    app.query_params["asset"] = "py://forecasting/src/features.py"
    app.query_params["fn"] = "save"
    app.run()
    assert not app.exception
    assert [s for s in app.selectbox if s.label == "Zoom"][0].value == "save"


def test_project_page_shows_environment(app, catalogue, project):
    from pdcat.config import PythonSource
    from pdcat.scan_python import scan_project

    (project / "requirements.txt").write_text("pandas>=2\nopenpyxl\n")
    scan_project(catalogue, PythonSource(name="forecasting", path=project))
    app.query_params["asset"] = "py://forecasting"
    app.run()
    assert not app.exception
    assert any(s.value == "Environment and libraries" for s in app.subheader)
    assert any(t.label.startswith("Declared, not imported (1)") for t in app.tabs)
