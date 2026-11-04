"""Version 2.29: a busy item's Connections tab is one table with filters, row actions and CSV."""
from pathlib import Path

import pytest

from pdcat.connections import connection_rows, filter_rows, place
from pdcat.links import links_for, unimportant

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"


def _busy(catalogue, readers=14, writers=2):
    for i in range(readers):
        key = f"py://bulk/reader{i:02d}.py"
        catalogue.upsert_asset(key, "script", f"reader{i:02d}.py", "bulk")
        catalogue.add_link(key, "reads", SALES, "bulk", dst_key=SALES, evidence=f"line {i}")
    for i in range(writers):
        key = f"py://etl/loader{i}.py"
        catalogue.upsert_asset(key, "script", f"loader{i}.py", "etl")
        catalogue.add_link(key, "writes", SALES, "etl", dst_key=SALES)
    catalogue.conn.commit()


def test_rows_say_which_way_each_link_goes(catalogue):
    _busy(catalogue)
    asset = catalogue.get_asset(SALES)
    rows = connection_rows(catalogue, SALES, asset, links_for(catalogue, SALES, asset))
    words = {r["connection"] for r in rows}
    assert {"Read by", "Loaded by"} <= words
    loaders = [r for r in rows if r["connection"] == "Loaded by"]
    assert [r["name"] for r in loaders] == ["loader0.py", "loader1.py"]
    assert rows[0]["connection"] == "Loaded by"  # what loads it comes first
    assert all(r["kind"] == "script" for r in loaders) and loaders[0]["source"] == "etl"
    assert loaders[0]["key"] == "py://etl/loader0.py"


def test_rows_for_what_a_script_uses(catalogue):
    key = "py://bulk/reader00.py"
    _busy(catalogue, readers=1, writers=0)
    asset = catalogue.get_asset(key)
    rows = connection_rows(catalogue, key, asset, links_for(catalogue, key, asset))
    [r] = [r for r in rows if r["key"] == SALES]
    assert r["connection"] == "Reads" and r["kind"] == "table"


def test_filters_combine(catalogue):
    _busy(catalogue)
    asset = catalogue.get_asset(SALES)
    rows = connection_rows(catalogue, SALES, asset, links_for(catalogue, SALES, asset))
    assert len(filter_rows(rows, connections=["Loaded by"])) == 2
    assert len(filter_rows(rows, connections=["Read by"], words="reader1")) == 4  # reader01, 10..13
    assert len(filter_rows(rows, words="line 13")) == 1  # evidence is searched too
    assert len(filter_rows(rows, places=[place(rows[0])])) == 2
    assert filter_rows(rows, kinds=["table"]) == []


def _app(catalogue, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("PDCAT_DB", str(catalogue.path))
    return AppTest.from_file(str(Path(__file__).parents[1] / "src" / "pdcat" / "app.py"), default_timeout=20)


def _table(app):
    return [d for d in app.dataframe if (d.key or "").startswith("conn-table-")][0]


def test_busy_item_shows_one_filterable_table(catalogue, monkeypatch):
    from tests.test_app import open_tab
    _busy(catalogue)
    app = _app(catalogue, monkeypatch)
    app.query_params["asset"] = SALES
    app.run()
    open_tab(app, "connections")
    assert not app.exception
    assert len(_table(app).value) >= 16
    assert not [b for b in app.button if (b.key or "").startswith("readby-")]  # no long list of buttons
    app.text_input(key=f"conn-find-{SALES}").input("loader").run()
    assert not app.exception
    assert list(_table(app).value["name"]) == ["loader0.py", "loader1.py"]
    app.text_input(key=f"conn-find-{SALES}").input("").run()
    app.multiselect(key=f"conn-kind-{SALES}").select("script").run()
    assert not app.exception
    assert set(_table(app).value["type"]) == {"script"}


def test_row_actions_mark_not_important_and_open(catalogue, monkeypatch):
    from pdcat.ui import connections_table as ct

    _busy(catalogue)
    asset = catalogue.get_asset(SALES)
    rows = connection_rows(catalogue, SALES, asset, links_for(catalogue, SALES, asset))
    picked = [r for r in rows if r["name"] in ("reader00.py", "reader01.py")]
    # The table's tick boxes can't be clicked in tests, so the selection is given directly.
    monkeypatch.setattr(ct, "selected_rows", lambda event, n: [rows.index(r) for r in picked])
    app = _app(catalogue, monkeypatch)
    app.query_params["asset"] = SALES
    app.run()
    from tests.test_app import open_tab
    open_tab(app, "connections")
    assert not app.exception
    [b for b in app.button if b.label == "Not important (2)"][0].click().run()
    assert not app.exception
    assert {(s, r) for s, r, _ in unimportant(catalogue)} == {("py://bulk/reader00.py", "reads"),
                                                               ("py://bulk/reader01.py", "reads")}


def test_renamed_columns_and_sql_using_this_table_are_tables(catalogue, monkeypatch):
    from pdcat import sqllibrary
    from tests.test_app import open_tab
    store = "sql://warehouse/forecastdb/dbo.store"
    catalogue.save_snippet("Daily sales", "SELECT s.StoreId, s.Units FROM dbo.SalesDaily s "
                                          "JOIN dbo.Store st ON st.StoreId = s.StoreId")
    assert sqllibrary.build_library(catalogue) >= 1
    with catalogue.conn:
        for i in range(3):
            catalogue.conn.execute(
                "INSERT INTO column_links (src_item, src_column, dst_item, dst_column, how, evidence, source) "
                "VALUES (?, ?, ?, ?, 'rename', 'load.py', 'test')", (SALES, f"Col{i}", store, f"Renamed{i}"))
    app = _app(catalogue, monkeypatch)
    app.query_params["asset"] = store
    app.run()
    open_tab(app, "connections")
    assert not app.exception
    [ren] = [d for d in app.dataframe if (d.key or "").startswith("ren-table-")]
    assert list(ren.value["column here"]) == ["Renamed0", "Renamed1", "Renamed2"]
    app.text_input(key=f"ren-find-{store}").input("renamed1").run()
    assert not app.exception
    [ren] = [d for d in app.dataframe if (d.key or "").startswith("ren-table-")]
    assert list(ren.value["column there"]) == ["Col1"]
    [ut] = [d for d in app.dataframe if (d.key or "").startswith("ut-table-")]
    assert "Daily sales" in list(ut.value["name"])


def test_reports_filter_by_register_columns(catalogue, tmp_path):
    from test_v23_register import CSV, _reports

    from pdcat import register as R
    from pdcat.scan_files import scan_folder
    _reports(catalogue)
    root = tmp_path / "registry"
    root.mkdir()
    (root / "reports.csv").write_text(CSV)
    catalogue.add_folder("registry", str(root))
    scan_folder(catalogue, "registry", root)
    R.set_register(catalogue, "files://registry/reports.csv")
    R.refresh(catalogue)
    facets = R.report_facets(catalogue)
    assert "Planning team" in facets["owner"] and "soh" in facets["tags"]  # tags split one by one
    assert R.reports_matching(catalogue, {"owner": ["Planning team"]}) == {"files://reports/Stock on hand.pbix"}
    assert R.reports_matching(catalogue, {"status": ["Live"], "tags": ["stock"]}) == \
        {"files://reports/Stock on hand.pbix"}
    assert len(R.reports_matching(catalogue, {"status": ["Live"]})) == 2
    assert R.reports_matching(catalogue, {"owner": [R.NOT_IN]}) == set()  # every scanned report has a row
