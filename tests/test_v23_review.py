"""Version 2.3: To review with one item per table, answers that stick, and suggested descriptions."""
from __future__ import annotations

from conftest import FAKE_DB, SRC

from pdcat import describedrafts as DD
from pdcat import reviewanswers as RA
from pdcat.review import counts_by_rule, open_tasks, refresh_tasks, set_task_status
from pdcat.scan_sqlserver import ingest

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"
STORE = "sql://warehouse/forecastdb/dbo.store"


def by_rule(cat, rule):
    return {t["asset_key"]: t for t in open_tasks(cat, limit=10_000) if t["rule"] == rule}


def _profile_with_hints(cat, extra=()):
    cat.save_profile(SALES, [*extra,
        {"name": "StoreId", "data_type": "int", "rows": 100, "nulls": 0, "distinct": 40, "min": 1, "max": 40},
        {"name": "SaleDate", "data_type": "int", "rows": 100, "nulls": 0, "distinct": 90, "min": 20250101,
         "max": 20251231, "hints": [{"code": "int_yyyymmdd", "text": "whole numbers that look like dates (YYYYMMDD)"}]},
        {"name": "Units", "data_type": "varchar(20)", "rows": 100, "nulls": 0, "distinct": 50, "min": "1", "max": "9",
         "hints": [{"code": "text_numbers", "text": "text that holds numbers"}]},
    ], "test")


def test_data_type_hints_come_as_one_item_per_table_and_answers_stick(catalogue):
    _profile_with_hints(catalogue)
    refresh_tasks(catalogue)
    tasks = by_rule(catalogue, "data_type")
    assert list(tasks) == [SALES]
    t = tasks[SALES]
    assert "2 column(s)" in t["title"] and "SaleDate" in t["detail"] and "Units" in t["detail"]
    # YYYYMMDD integers are how dates are kept here: never raised again, anywhere
    RA.mark_hint_standard(catalogue, "int_yyyymmdd")
    refresh_tasks(catalogue)
    t = by_rule(catalogue, "data_type")[SALES]
    assert "1 column(s)" in t["title"] and "SaleDate" not in t["detail"]
    # by design for the one column left: the item closes
    RA.dismiss_hints(catalogue, SALES, [("Units", "text_numbers")])
    refresh_tasks(catalogue)
    assert SALES not in by_rule(catalogue, "data_type")
    assert "int_yyyymmdd" in RA.hints_ok(catalogue)
    RA.mark_hint_standard(catalogue, "int_yyyymmdd", standard=False)
    refresh_tasks(catalogue)
    assert "SaleDate" in by_rule(catalogue, "data_type")[SALES]["detail"]


def test_old_per_column_answers_carry_over_to_the_grouped_item(catalogue):
    _profile_with_hints(catalogue)
    ts = "2026-10-01T00:00:00"
    # items the 2.2 review list made, one per column, both dismissed by design
    for col, code in (("SaleDate", "int_yyyymmdd"), ("Units", "text_numbers")):
        catalogue.conn.execute(
            "INSERT INTO review_tasks (id, rule, asset_key, title, detail, priority, status, created_at, updated_at) "
            "VALUES (?, 'data_type', ?, 'x', '', 1, 'dismissed', ?, ?)", (f"data_type:{SALES}:{col}:{code}", SALES, ts, ts))
    catalogue.conn.commit()
    refresh_tasks(catalogue)
    assert SALES not in by_rule(catalogue, "data_type")
    row = catalogue.conn.execute("SELECT id, status FROM review_tasks WHERE id LIKE ?",
                                 (f"data_type:{SALES}:#%",)).fetchone()
    assert row["status"] == "dismissed"
    # a new column failing the check is a new question: the item comes back open under a new mark
    _profile_with_hints(catalogue, extra=[{"name": "Code", "data_type": "nvarchar", "rows": 100, "nulls": 0,
                                           "distinct": 100, "hints": [{"code": "text_numbers",
                                                                       "text": "text that holds numbers"}]}])
    refresh_tasks(catalogue)
    rows = catalogue.conn.execute("SELECT id, status FROM review_tasks WHERE id LIKE ? ORDER BY created_at",
                                  (f"data_type:{SALES}:#%",)).fetchall()
    assert [r["status"] for r in rows] == ["dismissed", "open"] and rows[0]["id"] != rows[1]["id"]


def test_sensitive_columns_one_item_per_table_with_table_answers(catalogue):
    catalogue.set_columns(STORE, [{"name": "StoreId", "ordinal": 0, "data_type": "int"},
                                  {"name": "ManagerEmail", "ordinal": 1, "data_type": "varchar"},
                                  {"name": "ManagerPhone", "ordinal": 2, "data_type": "varchar"}])
    catalogue.conn.commit()
    refresh_tasks(catalogue)
    t = by_rule(catalogue, "sensitive")[STORE]
    assert "2 column(s)" in t["title"] and "ManagerEmail" in t["detail"]
    RA.answer_sensitive(catalogue, STORE, ["ManagerEmail"], sensitive=True)
    RA.answer_sensitive(catalogue, STORE, ["ManagerPhone"], sensitive=False)
    refresh_tasks(catalogue)
    assert STORE not in by_rule(catalogue, "sensitive")
    from pdcat.curation import column_info
    assert column_info(catalogue, STORE)["ManagerEmail"]["sensitive"] == 1
    assert RA.sensitive_answers(catalogue)["manageremail"]["yes"] == 1


def test_a_column_name_answered_not_sensitive_three_times_is_not_asked_again(catalogue):
    for i in range(RA.NAME_ANSWERS_NEEDED):
        RA.answer_sensitive(catalogue, f"sql://x/t{i}", ["ContactName"], sensitive=False)
    assert "contactname" in RA.names_usually(catalogue, "no")
    catalogue.set_columns(STORE, [{"name": "ContactName", "ordinal": 0, "data_type": "varchar"}])
    catalogue.conn.commit()
    refresh_tasks(catalogue)
    assert STORE not in by_rule(catalogue, "sensitive")


def test_whole_table_not_sensitive_sticks(catalogue):
    catalogue.set_columns(STORE, [{"name": "ManagerEmail", "ordinal": 0, "data_type": "varchar"}])
    catalogue.conn.commit()
    RA.answer_table_sensitive(catalogue, STORE, ["ManagerEmail"], sensitive=False)
    refresh_tasks(catalogue)
    assert STORE not in by_rule(catalogue, "sensitive")


def test_placeholder_values_are_not_odd(catalogue):
    catalogue.save_profile(SALES, [
        {"name": "Flag", "data_type": "int", "rows": 100, "nulls": 0, "distinct": 1, "min": "-1", "max": "-1"},
        {"name": "Note", "data_type": "varchar", "rows": 100, "nulls": 0, "distinct": 1, "min": "tbc", "max": "tbc"},
    ], "test")
    refresh_tasks(catalogue)
    t = by_rule(catalogue, "odd_values")[SALES]
    assert "Note has only one value (tbc)" in t["detail"] and "Flag" not in t["detail"]  # -1 is a placeholder
    RA.add_placeholder(catalogue, "tbc")
    refresh_tasks(catalogue)
    assert SALES not in by_rule(catalogue, "odd_values")


def test_unmatched_tables_group_by_server_or_database_and_temp_names_drop(catalogue):
    with catalogue.scan("pasted"):
        catalogue.save_snippet("q1", "SELECT * FROM OtherSrv.Crm.dbo.Accounts a JOIN OtherSrv.Crm.dbo.Contacts c ON 1=1")
        catalogue.save_snippet("q2", "SELECT * FROM #tmp t JOIN ErpDb.dbo.Orders o ON 1=1")
    refresh_tasks(catalogue)
    tasks = by_rule(catalogue, "unmatched")
    assert "table:othersrv.crm" in tasks and "2 table(s) on othersrv.crm" in tasks["table:othersrv.crm"]["title"]
    assert "table:erpdb" in tasks
    assert not any("#tmp" in t["title"] for t in tasks.values())
    RA.ignore_prefix(catalogue, "othersrv.crm")
    refresh_tasks(catalogue)
    assert "table:othersrv.crm" not in by_rule(catalogue, "unmatched")
    # the names with no database part (from conftest's staging.Mystery) are still one item each
    assert "table:staging.mystery" in by_rule(catalogue, "unmatched")


def test_rules_can_be_switched_off(catalogue):
    refresh_tasks(catalogue)
    assert by_rule(catalogue, "undocumented")
    RA.set_rule(catalogue, "undocumented", False)
    refresh_tasks(catalogue)
    assert not by_rule(catalogue, "undocumented")
    assert "naming" in RA.rules_off(catalogue)  # off by default
    RA.set_rule(catalogue, "undocumented", True)
    refresh_tasks(catalogue)
    assert by_rule(catalogue, "undocumented")


def test_counts_by_rule(catalogue):
    refresh_tasks(catalogue)
    rows = counts_by_rule(catalogue)
    assert rows and rows[0]["open"] >= rows[-1]["open"]
    assert {r["rule"] for r in rows} >= {"undocumented"}
    tid = by_rule(catalogue, "undocumented")[STORE]["id"]
    set_task_status(catalogue, tid, "done")
    from pdcat.review import done_recently
    assert done_recently(catalogue) == 1


# ---- suggested descriptions --------------------------------------------------------------------

def test_descriptions_from_names_and_abbreviations():
    abbrevs = {"SOH": "Stock on hand", "Cust": "Customer"}
    assert DD.from_name("CustOrderQty", abbrevs) == "Customer order quantity"
    assert DD.from_name("IsActive") == "Whether active"
    assert DD.from_name("LoadDate") == "Load date"
    assert DD.from_name("PlantID") == "Plant identifier"
    assert DD.from_name("SOH", abbrevs) == "Stock on hand"
    assert DD.from_name("Region") is None   # one plain word says nothing
    assert DD.from_name("delivery_status") == "Delivery status"


def test_column_suggestions_prefer_the_same_name_described_elsewhere(catalogue):
    from pdcat.curation import set_column_info
    set_column_info(catalogue, STORE, "Region", description="Sales region the store reports into")
    catalogue.set_columns(SALES, [{"name": "StoreId", "ordinal": 0, "data_type": "int"},
                                  {"name": "Region", "ordinal": 1, "data_type": "varchar"},
                                  {"name": "OrderQty", "ordinal": 2, "data_type": "int"}])
    catalogue.conn.commit()
    rows = {r["column"]: r for r in DD.column_suggestions(catalogue, SALES)}
    assert rows["Region"]["text"] == "Sales region the store reports into" and "Store" in rows["Region"]["source"]
    assert rows["StoreId"]["text"] == "Store identifier" and rows["StoreId"]["source"] == "from the name"
    assert rows["OrderQty"]["text"] == "Order quantity"
    n = DD.accept_columns(catalogue, SALES, {"StoreId": rows["StoreId"]["text"], "OrderQty": "Units ordered"})
    assert n == 2
    from pdcat.curation import column_description
    assert column_description(catalogue, SALES, "OrderQty") == "Units ordered"
    assert not [r for r in DD.column_suggestions(catalogue, SALES) if r["column"] in ("StoreId", "OrderQty")]


def test_table_suggestion_from_name_and_grain(catalogue):
    with catalogue.scan(SRC.name):
        db = {"objects": [{"schema_name": "dbo", "object_name": "dim_plant", "object_type": "U ", "row_count": 12,
                           "description": None, "definition": None}],
              "columns": [{"schema_name": "dbo", "object_name": "dim_plant", "column_name": "PlantID", "column_id": 1,
                           "type_name": "int", "max_length": 4, "precision": 10, "scale": 0, "is_nullable": False,
                           "description": None}]}
        ingest(catalogue, SRC, "ForecastDB", {**FAKE_DB, "objects": FAKE_DB["objects"] + db["objects"],
                                              "columns": FAKE_DB["columns"] + db["columns"]})
    key = "sql://warehouse/forecastdb/dbo.dim_plant"
    catalogue.save_profile(key, [{"name": "PlantID", "data_type": "int", "rows": 12, "nulls": 0, "distinct": 12,
                                  "min": 1, "max": 12}], "test")
    sug = DD.table_suggestion(catalogue, key)
    assert sug["text"].startswith("Dimension (master data): plant") and sug["grain"] == "one row per plant"
    DD.accept_table(catalogue, key, sug["text"], sug["grain"])
    from pdcat.curation import item_info
    assert item_info(catalogue, key)["grain"] == "one row per plant"
    refresh_tasks(catalogue)
    assert key not in by_rule(catalogue, "undocumented")
