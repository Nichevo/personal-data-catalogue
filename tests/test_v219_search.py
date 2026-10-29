"""Version 2.19: search matches parts of names and inside names, understands abbreviations, puts exact names
first, and the Search page remembers what you searched."""
import pytest

from pdcat import searchwords
from pdcat.store import Catalogue

NAMES = ["dbo.StockonHand", "dbo.OfficeStockonHand", "dbo.vw_MaterialStockOnHand", "dbo.STOCK_LEVELS",
         "dbo.Stock", "dbo.Overstock", "dbo.SOH_Daily", "dbo.Sales"]


@pytest.fixture
def cat(tmp_path):
    c = Catalogue(tmp_path / "c.db")
    for n in NAMES:
        c.upsert_asset(f"sql://s/db/{n.lower()}", "table", n, "sql")
    c.upsert_asset("sql://s/db/dbo.x", "table", "dbo.X", "sql", description="Stock on hand by plant")
    c.rebuild_search()
    yield c
    c.close()


def names(cat, text, **kw):
    return [r["name"] for r in cat.search(text, **kw)]


def test_split_and_squash():
    assert searchwords.split_name("dbo.vw_MaterialStockOnHand") == "dbo vw Material Stock On Hand"
    assert searchwords.split_name("OfficeStockonHand") == "Office Stockon Hand"
    assert searchwords.split_name("SAPMaterialID") == "SAP Material ID"
    assert searchwords.squash("Stock on_hand") == searchwords.squash("StockOnHand") == "stockonhand"


def test_a_word_finds_names_that_start_with_it_or_have_it_inside(cat):
    found = names(cat, "Stock")
    assert set(found) >= {"dbo.StockonHand", "dbo.OfficeStockonHand", "dbo.vw_MaterialStockOnHand",
                          "dbo.STOCK_LEVELS", "dbo.Stock", "dbo.Overstock"}
    assert "dbo.Sales" not in found


def test_exact_names_first_then_names_that_start_with_it(cat):
    found = names(cat, "stock")
    assert found[0] == "dbo.Stock"
    assert found.index("dbo.StockonHand") < found.index("dbo.OfficeStockonHand") < found.index("dbo.Overstock")
    assert names(cat, "StockonHand")[0] == "dbo.StockonHand"


def test_parts_and_inside_matches(cat):
    assert "dbo.OfficeStockonHand" in names(cat, "hand")
    assert "dbo.OfficeStockonHand" in names(cat, "onhand")
    assert set(names(cat, "stock on hand")) >= {"dbo.StockonHand", "dbo.OfficeStockonHand",
                                                "dbo.vw_MaterialStockOnHand"}


def test_short_words_only_match_the_start_of_words(cat):
    # "on" would be inside half the names; it matches the start of a word or name part only.
    assert names(cat, "on") == ["dbo.vw_MaterialStockOnHand", "dbo.X"]


def test_abbreviations_work_both_ways(cat):
    soh = names(cat, "SOH")
    assert {"dbo.StockonHand", "dbo.OfficeStockonHand", "dbo.SOH_Daily", "dbo.X"} <= set(soh)
    assert "dbo.SOH_Daily" in names(cat, "stock on hand")


def test_your_own_abbreviations(cat):
    assert "dbo.Overstock" not in names(cat, "XS")
    cat.set_abbreviations(searchwords.parse("XS = Overstock\nnot a line\nSOH: Stock on hand"))
    assert cat.abbreviations() == {"XS": "Overstock", "SOH": "Stock on hand"}
    assert "dbo.Overstock" in names(cat, "XS")


def test_columns_match_inside_names_and_abbreviations(catalogue):
    rows = catalogue.search_columns("oreid")  # StoreId
    assert any(r["column_name"] == "StoreId" for r in rows)
    key = "sql://warehouse/forecastdb/dbo.salesdaily"
    assert [c["name"] for c in catalogue.matching_columns(key, "id")] == ["StoreId"]


def test_older_catalogues_are_reindexed_once_on_opening(tmp_path):
    path = tmp_path / "old.db"
    with Catalogue(path) as c:
        c.upsert_asset("sql://s/db/dbo.officestockonhand", "table", "dbo.OfficeStockonHand", "sql")
        c.rebuild_search()
        # How 2.18 and older indexed it, without the name's parts.
        c.conn.execute("UPDATE search_idx SET name = 'dbo.OfficeStockonHand dbo OfficeStockonHand'")
        c.conn.execute("DELETE FROM settings WHERE key = 'search_index_version'")
        c.conn.commit()
    with Catalogue(path) as c:
        assert "Office Stockon Hand" in c.conn.execute("SELECT name FROM search_idx").fetchone()[0]
        assert c.setting("search_index_version") == 2
        revision = c.revision()
    with Catalogue(path) as c:
        assert c.revision() == revision  # not again


def test_find_list_filter_uses_the_same_matching(cat):
    from pdcat.ui.browse_pages import filter_rows
    rows = [{"key": "a", "name": "OfficeStockonHand", "in": "Report"}, {"key": "b", "name": "Sales", "in": "Report"},
            {"key": "c", "name": "Stock", "in": "Report"}]
    assert [r["name"] for r in filter_rows(cat, rows, "stock")] == ["Stock", "OfficeStockonHand"]
    assert [r["name"] for r in filter_rows(cat, rows, "SOH")] == ["OfficeStockonHand"]


# ---- the app -------------------------------------------------------------------

def test_search_page_remembers_the_words(catalogue, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from test_app import APP, goto, sidebar_search
    monkeypatch.setenv("PDCAT_DB", str(catalogue.path))
    app = AppTest.from_file(APP, default_timeout=10)
    app.run()
    sidebar_search(app, "daily")
    app.selectbox(key="search-view").set_value("Cards").run()
    goto(app, "Menu guide")
    goto(app, "Search")
    assert not app.exception
    assert app.text_input(key="search-text").value == "daily"
    assert app.selectbox(key="search-view").value == "Cards"
    goto(app, "Tables and datasets")
    app.text_input(key="browse-text-tables-and-datasets").input("salesd").run()
    goto(app, "Python")
    goto(app, "Tables and datasets")
    assert app.text_input(key="browse-text-tables-and-datasets").value == "salesd"
    assert not app.exception


def test_abbreviations_page(catalogue, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from test_app import APP, goto
    monkeypatch.setenv("PDCAT_DB", str(catalogue.path))
    app = AppTest.from_file(APP, default_timeout=10)
    goto(app.run(), "Abbreviations")
    assert not app.exception
    assert "SOH = Stock on hand" in app.text_area[0].value
    app.text_area[0].set_value("SD = Sales daily")
    [b for b in app.button if b.label == "Save"][0].click().run()
    assert not app.exception
    with Catalogue(catalogue.path) as c:
        assert c.abbreviations() == {"SD": "Sales daily"}
