"""Company-wide keys: marked by hand, suggested after scans, checked across tables."""
from conftest import FAKE_DB, SRC

from pdcat import keys as K
from pdcat.scan_sqlserver import ingest

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"
STORE = "sql://warehouse/forecastdb/dbo.store"


def _add_storeid_to_store(cat):
    cat.conn.execute("INSERT INTO columns (asset_key, name, ordinal, data_type) VALUES (?, 'StoreID', 0, 'int')",
                     (STORE,))


def test_mark_a_key_and_confirm_the_suggested_columns(catalogue):
    _add_storeid_to_store(catalogue)
    name = K.mark_key(catalogue, "StoreId", STORE, "StoreID")
    k = K.get_key(catalogue, name)
    assert (k["master_key"], k["master_column"]) == (STORE, "StoreID")
    suggested = K.key_columns(catalogue, name, "suggested")
    assert [(c["asset_key"], c["column_name"]) for c in suggested] == [(SALES, "StoreId")]
    K.set_column_state(catalogue, SALES, "StoreId", "confirmed")
    hints = K.join_hints(catalogue, SALES)
    assert hints[0]["other"] == STORE and hints[0]["other_column"] == "StoreID"
    assert K.columns_with_keys(catalogue, SALES)["StoreId"] == {"key": "StoreId", "state": "confirmed"}
    assert catalogue.search("storeid")  # keys are searchable


def test_rejected_columns_and_keys_stay_gone(catalogue):
    _add_storeid_to_store(catalogue)
    name = K.mark_key(catalogue, "StoreId", STORE, "StoreID")
    K.unlink_column(catalogue, SALES, "StoreId")
    K.suggest_columns(catalogue, name)
    assert K.key_columns(catalogue, name, "suggested") == []
    # Suggested keys from a scan: the same key-like name in three tables across two databases.
    with catalogue.scan(SRC.name, prefix="sql://warehouse/otherdb"):
        ingest(catalogue, SRC, "OtherDB", FAKE_DB)
    with catalogue.scan("warehouse2", prefix="sql://warehouse2/x"):
        ingest(catalogue, type(SRC)(name="warehouse2", server="S", databases=["X"]), "X", FAKE_DB)
    K.delete_key(catalogue, name)
    assert K.suggest_keys(catalogue) == 1
    assert K.list_keys(catalogue, "suggested")[0]["name"].lower() == "storeid"
    K.reject_key(catalogue, "StoreId")
    assert K.suggest_keys(catalogue) == 0


def test_key_checks(catalogue):
    _add_storeid_to_store(catalogue)
    name = K.mark_key(catalogue, "StoreId", STORE, "StoreID")
    K.set_column_state(catalogue, SALES, "StoreId", "confirmed")
    catalogue.save_profile(STORE, [{"name": "StoreID", "rows": 40, "nulls": 0, "distinct": 40, "min": 1, "max": 40}], "t")
    catalogue.save_profile(SALES, [{"name": "StoreId", "rows": 900, "nulls": 0, "distinct": 41, "min": 1, "max": 99}], "t")
    problems = K.checks(catalogue, name)
    assert any("outside the master list" in p for p in problems)
    assert any("more distinct values" in p for p in problems)


def test_rename_and_aliases(catalogue):
    _add_storeid_to_store(catalogue)
    K.mark_key(catalogue, "StoreId", STORE, "StoreID")
    catalogue.conn.execute("INSERT INTO columns (asset_key, name, ordinal, data_type) VALUES (?, 'Site_No', 9, 'int')",
                           (SALES,))
    new = K.update_key(catalogue, "StoreId", new_name="Store", aliases=["Site No"])
    assert new == "Store"
    assert ("Site_No" in [c["column_name"] for c in K.key_columns(catalogue, "Store", "suggested")])
