"""Version 2.1: starter knowledge packs and SAP links."""
from pathlib import Path

import pytest

from pdcat import packs, sapterms, suggestions
from pdcat.store import Catalogue


@pytest.fixture
def cat(tmp_path: Path):
    c = Catalogue(tmp_path / "c.db")
    yield c
    c.close()


def test_every_sql_check_has_an_article():
    from pdcat.sqlchecks import RULES
    covered = {r for _, _, rules, _ in packs.SQLSERVER_NOTES for r in rules}
    assert set(RULES) <= covered


def test_packs_load_and_never_overwrite_your_edits(cat, monkeypatch):
    from pdcat.knowledge import get_knowledge, list_metrics, save_knowledge
    for name in packs.PACKS:
        done = packs.load(cat, name)
        assert done["added"] > 0
    assert all(n > 0 for n in packs.status(cat).values())
    wape = next(m for m in list_metrics(cat) if m["name"] == "WAPE")
    assert "general" in wape["tags"]
    uid = "general-sql-isolation"
    save_knowledge(cat, "NOLOCK at our place", "We allow it on the staging tables.", section="How-to", uid=uid)
    monkeypatch.setattr(packs, "SQLSERVER_NOTES", [(s, t, r, b + " (new)") for s, t, r, b in packs.SQLSERVER_NOTES])
    done = packs.load(cat, "sqlserver")
    assert done["kept"] == 1 and done["updated"] == len(packs.SQLSERVER_NOTES) - 1
    assert get_knowledge(cat, uid)["title"] == "NOLOCK at our place"
    assert get_knowledge(cat, "general-sql-dates")["body"].endswith("(new)")
    assert packs.article_for_rule(cat, "nolock") == uid
    assert any(r["key"] == f"kb://{uid}" for r in cat.search("staging tables"))


def test_sap_names_are_suggested_from_columns_and_tables(cat):
    cat.upsert_asset("sql://dw/db/dbo.stg_marc", "table", "dbo.stg_MARC", "dw", attrs={"object": "stg_marc"})
    cat.set_columns("sql://dw/db/dbo.stg_marc", [{"name": n, "ordinal": i} for i, n in enumerate(
        ["MATNR", "PlantID", "StorageLocation", "Notes"])])
    cat.conn.commit()
    sapterms.suggest_sap(cat)
    found = {(s["payload"].get("column"), s["payload"]["sap_name"]): s for s in suggestions.open_suggestions(cat, "sap")}
    assert set(found) == {("MATNR", "MATNR"), ("PlantID", "WERKS"), ("StorageLocation", "LGORT"), (None, "MARC")}
    for s in found.values():
        suggestions.accept(cat, s["id"])
    from pdcat.curation import column_info, item_info
    assert column_info(cat, "sql://dw/db/dbo.stg_marc")["PlantID"]["sap_name"] == "WERKS"
    assert item_info(cat, "sql://dw/db/dbo.stg_marc")["sap_name"] == "MARC"
    assert {r["SAP"] for r in sapterms.sap_items(cat)} == {"MATNR", "WERKS", "LGORT", "MARC"}
    sapterms.suggest_sap(cat)
    assert suggestions.open_suggestions(cat, "sap") == []
    assert any(r["key"] == "sql://dw/db/dbo.stg_marc" for r in cat.search("werks"))


def test_apo_items_are_flagged_with_the_ibp_equivalent(cat):
    cat.upsert_asset("sql://dw/db/dbo.apo_dp_history", "table", "dbo.APO_DP_History", "dw", attrs={"object": "x"})
    cat.set_columns("sql://dw/db/dbo.apo_dp_history", [{"name": "9AMATNR", "ordinal": 1}, {"name": "9ALOCNO",
                                                                                           "ordinal": 2}])
    cat.upsert_asset("sql://dw/db/dbo.sales", "table", "dbo.Sales", "dw", attrs={"object": "sales"})
    cat.conn.commit()
    flags = dict(sapterms.apo_flags(cat, "sql://dw/db/dbo.apo_dp_history"))
    assert flags["APO product characteristic (9AMATNR)"] == "IBP product ID (PRDID)"
    assert "APO Demand Planning" in flags
    assert [r["key"] for r in sapterms.apo_items(cat)] == ["sql://dw/db/dbo.apo_dp_history"]
    assert sapterms.apo_flags(cat, "sql://dw/db/dbo.sales") == []


def test_main_and_backup_tables_are_suggested(cat):
    from pdcat import groups, tablepairs
    for src, rows in (("onprem", 1000), ("azure", 5000)):
        key = f"sql://{src}/salesdb/dbo.sales"
        cat.upsert_asset(key, "table", "dbo.Sales", src, attrs={"object": "sales", "schema": "dbo", "row_count": rows})
        cols = ["SaleDate", "StoreId", "Units"] + (["ArchivedAt"] if src == "azure" else [])
        cat.set_columns(key, [{"name": c, "ordinal": i} for i, c in enumerate(cols)])
    cat.add_link("py://p/a.py", "reads", "table:dbo.sales", "p", dst_key="sql://onprem/salesdb/dbo.sales")
    cat.conn.commit()
    tablepairs.find_backups(cat)
    [s] = suggestions.open_suggestions(cat, "backup")
    assert s["payload"]["main_key"] == "sql://onprem/salesdb/dbo.sales" and "ArchivedAt" in s["detail"]
    suggestions.accept(cat, s["id"])
    [g] = groups.all_groups(cat, "backup")
    assert g["members"] == 2 and groups.main_of(cat, "sql://azure/salesdb/dbo.sales", "backup") == \
        "sql://onprem/salesdb/dbo.sales"
    tablepairs.find_backups(cat)
    assert suggestions.open_suggestions(cat, "backup") == []
