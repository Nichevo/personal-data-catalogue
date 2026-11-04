"""Version 2.22: tables whose own name holds dots, such as [dbo].[FAX.FAXX.Sales], are matched to the code that uses
them."""
from pdcat.sqlrefs import extract_refs
from pdcat.store import Catalogue


def _table(cat: Catalogue, schema: str, obj: str, database: str = "Ops") -> str:
    key = f"sql://wh/{database}/{schema}.{obj}".lower()
    cat.upsert_asset(key, "table", f"{schema}.{obj}", "wh",
                     attrs={"server": "WH01", "database": database, "schema": schema, "object": obj})
    return key


def test_dotted_table_name_matches_however_it_is_written(tmp_path):
    cat = Catalogue(tmp_path / "c.db")
    key = _table(cat, "dbo", "FAX.FAXX.SSD_Sales")
    for sql in ("SELECT * FROM dbo.[FAX.FAXX.SSD_Sales]", "SELECT * FROM [dbo].[FAX.FAXX.SSD_Sales] s",
                "SELECT * FROM [FAX.FAXX.SSD_Sales]", "SELECT * FROM Ops.dbo.[FAX.FAXX.SSD_Sales]",
                "SELECT * FROM WH01.Ops.dbo.[FAX.FAXX.SSD_Sales]"):
        [ref] = extract_refs(sql)
        assert [c["key"] for c in cat.match_table(ref.name)] == [key], sql
    assert cat.match_table("other.fax.faxx.ssd_sales") == []  # wrong schema
    cat.close()


def test_plain_names_still_match_and_a_real_clash_stays_unresolved(tmp_path):
    cat = Catalogue(tmp_path / "c.db")
    plain = _table(cat, "faxx", "SSD_Sales", database="fax")
    assert [c["key"] for c in cat.match_table("fax.faxx.ssd_sales")] == [plain]
    assert [c["key"] for c in cat.match_table("ssd_sales")] == [plain]
    dotted = _table(cat, "dbo", "FAX.FAXX.SSD_Sales")
    # "fax.faxx.ssd_sales" could now be either table: don't guess.
    assert {c["key"] for c in cat.match_table("fax.faxx.ssd_sales")} == {plain, dotted}
    assert [c["key"] for c in cat.match_table("dbo.fax.faxx.ssd_sales")] == [dotted]
    cat.close()


def test_code_links_resolve_to_the_dotted_table(tmp_path):
    cat = Catalogue(tmp_path / "c.db")
    key = _table(cat, "dbo", "FAX.FAXX.SSD_Sales")
    cat.upsert_asset("py://p/load.py", "script", "load.py", "p")
    cat.add_link("py://p/load.py", "reads", "table:dbo.fax.faxx.ssd_sales", "p")
    cat.resolve_refs()
    assert cat.conn.execute("SELECT dst_key FROM links").fetchone()[0] == key
    cat.close()
