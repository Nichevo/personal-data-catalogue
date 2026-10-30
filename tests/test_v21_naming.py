"""Version 2.1: naming rules pasted as text, the audit, exceptions, and habits worked out from names."""
from pathlib import Path

import pytest

from pdcat import naming, suggestions
from pdcat.store import Catalogue

DB = "sql://dw/salesdb"


@pytest.fixture
def cat(tmp_path: Path):
    c = Catalogue(tmp_path / "c.db")
    c.upsert_asset(DB, "database", "salesdb", "dw")
    for name in ("vw_Sales", "vw_Stores", "vw_Stock", "vw_Orders", "vw_Prices", "SalesCopy"):
        c.upsert_asset(f"{DB}/dbo.{name.lower()}", "view", f"dbo.{name}", "dw", parent=DB,
                       attrs={"object": name.lower(), "schema": "dbo"})
    c.upsert_asset(f"{DB}/stage.load_tmp", "table", "stage.load_tmp", "dw", parent=DB,
                   attrs={"object": "load_tmp", "schema": "stage"})
    c.set_columns(f"{DB}/dbo.vw_sales", [
        {"name": n, "data_type": t, "ordinal": i} for i, (n, t) in enumerate(
            [("StoreID", "int"), ("SaleDate", "date"), ("ShipDate", "date"), ("OrderDate", "date"),
             ("DueDate", "date"), ("load_dt", "datetime"), ("Units", "int"), ("Order", "int")])])
    c.conn.commit()
    yield c
    c.close()


def test_pasted_rules_become_checks_to_confirm(cat):
    text = "Views must start with vw_\nDate columns end with Date\nNo reserved words for column names\n" \
           "Everything goes in the dbo schema\nAsk the DBA first."
    added = naming.save_text(cat, DB, text)
    assert naming.pasted_text(cat, DB) == text
    assert {(a["applies_to"], a["check_type"]) for a in added} >= {
        ("view", "prefix"), ("column", "type_suffix"), ("column", "no_reserved"), ("table", "schema"),
        ("table", "text")}
    assert naming.audit(cat, DB) == []  # nothing is checked until you confirm
    for r in naming.rules(cat, DB, all_states=True):
        if r["state"] == "proposed":
            naming.update_rule(cat, r["uid"], state="active")
    results = {r["label"]: r for r in naming.audit(cat, DB)}
    assert [i["name"] for i in results["Views start with `vw_`"]["failed"]] == ["SalesCopy"]
    assert [i["name"] for i in results["Date columns end with `Date`"]["failed"]] == ["load_dt"]
    assert [i["name"] for i in results["No reserved words as columns"]["failed"]] == ["Order"]
    assert [i["name"] for i in results["Tables are in the `dbo` schema"]["failed"]] == ["load_tmp"]
    # pasting again doesn't add the same checks twice
    assert naming.save_text(cat, DB, text) == []


def test_exceptions_and_csv(cat):
    uid = naming.add_rule(cat, DB, "view", "prefix", "vw_")
    naming.add_exception(cat, uid, f"{DB}/dbo.salescopy")
    [r] = naming.audit(cat, DB)
    assert r["failed"] == [] and [i["name"] for i in r["exceptions"]] == ["SalesCopy"]
    assert "agreed exception" in naming.audit_csv(cat, DB)


def test_new_objects_breaking_a_rule_go_to_review(cat):
    naming.add_rule(cat, DB, "view", "prefix", "vw_")
    from pdcat.review import open_tasks, refresh_tasks
    from pdcat.reviewanswers import set_rule
    set_rule(cat, "naming", True)   # off by default since 2.3 (a noisy check)
    refresh_tasks(cat)
    assert not [t for t in open_tasks(cat, limit=1000) if t["rule"] == "naming"]  # existing ones aren't nagged
    cat.upsert_asset(f"{DB}/dbo.newsales", "view", "dbo.NewSales", "dw", parent=DB, attrs={"object": "newsales"})
    cat.conn.execute("UPDATE assets SET first_seen = '2999-01-01' WHERE key = ?", (f"{DB}/dbo.newsales",))
    cat.conn.commit()
    refresh_tasks(cat)
    [t] = [t for t in open_tasks(cat, limit=1000) if t["rule"] == "naming"]
    assert t["asset_key"] == f"{DB}/dbo.newsales" and "NewSales" in t["detail"]


def test_habits_are_suggested_and_become_rules(cat):
    naming.derive_all(cat)
    found = {(s["payload"]["applies_to"], s["payload"]["check_type"], s["payload"]["value"]): s
             for s in suggestions.open_suggestions(cat, "naming")}
    s = found[("view", "prefix", "vw_")]
    assert "5 of 6" in s["detail"] and "SalesCopy" in s["detail"]
    assert ("column", "type_suffix", "date:Date") in found
    suggestions.accept(cat, s["id"])
    assert [r["check_type"] for r in naming.rules(cat, DB)] == ["prefix"]
    naming.derive_all(cat)
    assert ("view", "prefix", "vw_") not in {(x["payload"]["applies_to"], x["payload"]["check_type"],
                                              x["payload"]["value"]) for x in suggestions.open_suggestions(cat, "naming")}


def test_case_checks_ignore_the_prefix():
    rule = {"check_type": "case", "value": "pascal"}
    assert naming.passes(rule, {"name": "vw_SalesDaily"}, ["vw_"])
    assert not naming.passes(rule, {"name": "vw_sales_daily"}, ["vw_"])
    assert naming.passes({"check_type": "id_suffix", "value": "ID"}, {"name": "Units"}) is None
    assert not naming.passes({"check_type": "id_suffix", "value": "ID"}, {"name": "StoreId"})
