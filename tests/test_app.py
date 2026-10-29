"""Smoke tests that drive the Streamlit page headlessly."""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).parents[1] / "src" / "pdcat" / "app.py")
SALES = "sql://warehouse/forecastdb/dbo.salesdaily"


def sidebar_search(app, text: str):
    """Type in the sidebar search box and press its button."""
    app.sidebar.text_input(key="sidebar-search").input(text)
    [b for b in app.sidebar.button if b.label == "🔎"][0].click().run()
    return app


def goto(app, page: str):
    """Pick a page from the sidebar: its section first, then the page."""
    from pdcat.ui.main import ALIASES, sections
    page, view = ALIASES.get(page, (page, None))
    section = next(name for name, pages in sections().items() if page in pages)
    app.sidebar.selectbox(key="nav-section").set_value(section).run()
    keys = [r.key for r in app.sidebar.radio]
    key = f"nav-item-{section}" if f"nav-item-{section}" in keys else f"nav-{section}"
    app.sidebar.radio(key=key).set_value(page).run()
    if view:
        app.radio(key=f"sub-{page}").set_value(view).run()
    return app


@pytest.fixture
def app(catalogue, monkeypatch):
    monkeypatch.setenv("PDCAT_DB", str(catalogue.path))
    return AppTest.from_file(APP, default_timeout=10)


def test_search_page_lists_results(app):
    app.run()
    assert not app.exception
    assert app.title[0].value == "Home"
    sidebar_search(app, "sales")
    assert not app.exception
    assert app.title[0].value == "Search"
    assert any("dbo.SalesDaily" in str(v) for d in app.dataframe for v in d.value["name"])
    app.selectbox(key="search-view").set_value("Cards").run()
    assert any("dbo.SalesDaily" in b.label for b in app.button)
    app.selectbox(key="search-view").set_value("Table").run()
    for by in ("Script that creates it", "Family", "Folder or database", "Business project"):
        app.selectbox(key="search-group").set_value(by).run()
        assert not app.exception, by
    app.radio(key="search-quick").set_value("Code").run()
    assert not app.exception


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
    app.text_input(key="snippet-name").input("Store regions") if any(
        t.key == "snippet-name" for t in app.text_input) else [t for t in app.text_input if t.key != "sidebar-search"][0].input("Store regions")
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
    [t for t in app.text_input if t.label == "Folder path"][0].input(f'"{folder}"').run()
    assert any("single project" in m.value for m in app.markdown)
    [t for t in app.text_input if t.label == "Short name"][0].input("extracts")
    [b for b in app.button if b.label == "Add and scan"][0].click().run()
    assert not app.exception
    assert catalogue.get_asset("files://extracts/sales.csv")["attrs"]["row_count"] == 2
    assert any(m.value.startswith("Last scan: extracts: Scanned 1 file(s)") for m in app.success)

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


def test_item_pages_for_keys_packages_and_knowledge(app, catalogue):
    from pdcat import keys as K
    from pdcat.knowledge import save_knowledge
    K.mark_key(catalogue, "StoreId", SALES, "StoreId")
    save_knowledge(catalogue, "Freight is always sea freight", "Air is separate.", triggers="sales")
    for key in ["key://StoreId", "pkg://pandas", SALES]:
        app.query_params["asset"] = key
        app.run()
        assert not app.exception, (key, app.exception)
    assert any("Freight is always sea freight" in b.label for b in app.button)  # related knowledge on the table


def test_knowledge_item_pages(app, catalogue):
    from pdcat import bprojects as B
    from pdcat import mlfeatures as M
    from pdcat.knowledge import save_metric, save_question
    uid = B.save_project(catalogue, "Sales forecasting", purpose="Weekly forecast")
    B.set_member(catalogue, uid, SALES)
    fuid = M.save_feature(catalogue, "sales_lag_7", project="forecasting", sources=[f"{SALES}#Units"],
                          known_at_prediction="yes")
    q = save_question(catalogue, "Weekly sales by region?", sql="SELECT * FROM dbo.SalesDaily")
    m = save_metric(catalogue, "Forecast accuracy", definition="1 - WAPE", sources=[SALES])
    for key in [f"bp://{uid}", f"feature://{fuid}", f"q://{q}", f"metric://{m}", SALES]:
        app.query_params["asset"] = key
        app.run()
        assert not app.exception, (key, app.exception)
    assert any("Sales forecasting" in b.label for b in app.button)  # the table shows its business project


def test_report_workbook_and_measure_pages(app, catalogue, tmp_path):
    from test_office import write_pbit, write_xlsx

    from pdcat.scan_files import scan_folder
    folder = tmp_path / "reports"
    folder.mkdir()
    write_pbit(folder / "Sales.pbit")
    write_xlsx(folder / "Sales.xlsm")
    scan_folder(catalogue, "reports", folder)
    measure = [c["key"] for c in catalogue.children("files://reports/Sales.pbit") if c["kind"] == "measure"][0]
    for key in ["files://reports/Sales.pbit", "files://reports/Sales.xlsm", measure, SALES]:
        app.query_params["asset"] = key
        app.run()
        assert not app.exception, (key, app.exception)
    app.query_params["asset"] = measure
    app.run()
    [b for b in app.button if b.label == "Save"][0].click().run()  # add it to the metric glossary
    assert not app.exception
    from pdcat.knowledge import list_metrics
    assert list_metrics(catalogue)[0]["from_measure"] == measure


def test_menu_leaves_an_item_page(app):
    app.query_params["asset"] = SALES
    app.run()
    goto(app, "Code map")
    assert not app.exception
    assert "asset" not in app.query_params and app.title[0].value == "Code map"


def test_menu_reopens_the_page_you_came_from(app):
    app.run()
    assert app.title[0].value == "Home"
    app.query_params["asset"] = SALES
    app.run()
    assert app.sidebar.radio(key="nav-item-🏠 Home").value is None  # no page ticked on an item page
    app.sidebar.radio(key="nav-item-🏠 Home").set_value("Home").run()
    assert not app.exception
    assert "asset" not in app.query_params and app.title[0].value == "Home"
    # the item just viewed is in Recently viewed
    assert any("dbo.SalesDaily" in b.label for b in app.sidebar.button)


def test_long_lists_show_a_page_at_a_time(app, catalogue, monkeypatch):
    import pdcat.ui.common as common
    monkeypatch.setattr(common, "RESULT_PAGE", 1)
    goto(app.run(), "Needs notes")
    more = [b for b in app.button if b.label.startswith("Show 1 more")]
    assert more and not app.exception
    shown = len(app.button)
    more[0].click().run()
    assert len(app.button) > shown


def test_copilot_preview_is_built_only_when_asked(app, monkeypatch):
    from pdcat import aipack
    calls = []
    real = aipack.build
    monkeypatch.setattr(aipack, "build", lambda *a, **k: calls.append(1) or real(*a, **k))
    goto(app.run(), "Copilot pack")
    assert not calls and not app.exception
    app.toggle(key="copilot-preview").set_value(True).run()
    assert calls and not app.exception


def test_sidebar_search_twice_with_the_same_words(app):
    app.run()
    sidebar_search(app, "sales")
    assert app.title[0].value == "Search"
    assert app.sidebar.text_input(key="sidebar-search").value == ""  # emptied, ready for the next search
    goto(app, "Menu guide")
    sidebar_search(app, "sales")
    assert not app.exception and app.title[0].value == "Search"
    assert app.text_input(key="search-text").value == "sales"


def test_report_versions_show_over_time_and_on_the_group_page(app, catalogue, tmp_path):
    from test_report_versions import write_pbit

    from pdcat import groups, suggestions
    from pdcat.scan_files import scan_folder
    from pdcat.versions import find_versions
    folder = tmp_path / "reports"
    write_pbit(folder / "Sales.pbit", when=1_700_000_000)
    write_pbit(folder / "Sales_v2.pbit", when=1_700_100_000, extra_measures=[("Avg", "AVERAGE(Sales[Units])")])
    scan_folder(catalogue, "reports", folder)
    find_versions(catalogue)
    [s] = suggestions.open_suggestions(catalogue, "versions")
    suggestions.accept(catalogue, s["id"])
    [g] = groups.for_item(catalogue, "files://reports/Sales_v2.pbit")
    for key in [f"group://{g['uid']}", "files://reports/Sales.pbit", "files://reports/Sales_v2.pbit"]:
        app.query_params["asset"] = key
        app.run()
        assert not app.exception, (key, app.exception)
    assert any(s.value == "Over time" for s in app.subheader)
    assert any("Current version" in m.value for m in app.markdown)


def test_scan_log_page_lists_scans(app, catalogue, tmp_path):
    import pandas as pd

    from pdcat.scan_files import scan_folder
    folder = tmp_path / "extracts"
    folder.mkdir()
    pd.DataFrame({"a": [1]}).to_csv(folder / "x.csv", index=False)
    scan_folder(catalogue, "extracts", folder)
    app.run()
    goto(app, "Scan log")
    assert not app.exception
    assert any("extracts" in df.value.to_string() for df in app.dataframe)


def test_whats_new_page_renders_with_notes_and_recent_views(app, catalogue):
    catalogue.add_note(SALES, "Daily sales, one row per store per day", "me", "note")
    app.query_params["asset"] = SALES
    app.run()  # a visit, so "Recently viewed" has something to show in the sidebar
    app.query_params.clear()
    goto(app, "Recent notes")
    assert not app.exception
    assert any("one row per store" in m.value for m in app.markdown)
    goto(app, "Recent changes")
    assert not app.exception


def test_review_table_offers_to_open_the_ticked_item(app):
    app.run()
    goto(app, "To review")
    assert not app.exception
    [open_] = [b for b in app.button if b.label == "↗ Open"]
    assert open_.disabled  # nothing ticked yet


def test_measures_across_reports_page(app, catalogue, tmp_path):
    from test_v214_scans import _pbit

    from pdcat.scan_files import scan_folder
    folder = tmp_path / "reports"
    _pbit(folder / "Sales.pbit", [("Total Units", "SUM(Sales[Units])")])
    _pbit(folder / "Stock.pbit", [("Total Units", "SUMX(Sales, Sales[Units])")])
    scan_folder(catalogue, "reports", folder)
    app.run()
    goto(app, "Measures across reports")
    assert not app.exception
    assert any("Total Units" in e.label for e in app.expander)


def test_database_diagram_page_lets_you_pick_tables_to_show_fields_for(app):
    app.run()
    goto(app, "Database diagrams")
    assert not app.exception
    [picker] = [m for m in app.multiselect if m.key and m.key.startswith("erd-fields-")]
    picker.set_value([SALES]).run()
    assert not app.exception


def test_settings_page_saves_scan_pace(app, catalogue):
    app.run()
    goto(app, "Settings")
    assert not app.exception
    [save] = [b for b in app.button if b.label == "Save scan settings"]
    save.click().run()
    assert not app.exception
    assert catalogue.setting("sample_over_files") == 2000 and catalogue.setting("recent_scan_minutes") == 60
