"""Version 2.18: relationship suggestions keep up with a big database (415 tables, 302 views), read only SQL
that changed, save as they go, can be switched off, and say how far they've got. Unmatched table names drop
SQL Server's own views, table-valued functions and FROM inside TRIM(), and say why each name didn't match."""
import json
import shutil
import time

import pytest
from bigdb import big_database

from pdcat import relationships as R
from pdcat.config import SqlServerSource
from pdcat.features import set_on
from pdcat.postscan import after_scan
from pdcat.scan_sqlserver import ingest
from pdcat.sqlrefs import extract_refs
from pdcat.store import Catalogue

BIG = SqlServerSource(name="wh", server="S", databases=["Big"])
VIEW = "sql://warehouse/forecastdb/ml.vforecastlatest"


@pytest.fixture(scope="module")
def big_data():
    return big_database()


@pytest.fixture(scope="module")
def big_file(tmp_path_factory, big_data):
    """The big database scanned once; each test gets its own copy."""
    path = tmp_path_factory.mktemp("big") / "big.db"
    cat = Catalogue(path)
    with cat.scan("wh", prefix="sql://wh/big"):
        ingest(cat, BIG, "Big", big_data)
    R.ingest_keys(cat, "wh", "Big", big_data["keys"])
    cat.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    cat.close()
    return path


@pytest.fixture
def big(tmp_path, big_file):
    shutil.copy(big_file, tmp_path / "big.db")
    cat = Catalogue(tmp_path / "big.db")
    yield cat
    cat.close()


def _reads(cat):
    return cat.conn.execute("SELECT COUNT(*) FROM relationship_reads").fetchone()[0]


def test_the_test_database_is_the_size_of_the_real_one(big_data):
    kinds = [o["object_type"].strip() for o in big_data["objects"]]
    assert kinds.count("U") >= 415 and kinds.count("V") >= 302


def test_big_database_is_quick_and_a_second_run_reads_nothing_again(big):
    said = []
    began = time.monotonic()
    found = R.find_suggestions(big, report=lambda text, done=None, total=None: said.append((text, done, total)))
    first = time.monotonic() - began
    assert found > 1000
    assert first < 60, f"took {first:.0f}s"
    reading = big.setting("relationship_reading")
    assert reading["read"] == _reads(big) >= 400 and reading["left"] == 0
    # It says which view it is reading and how many there are.
    assert any(t.startswith("reading the SQL of") and total == reading["read"] for t, _, total in said)

    began = time.monotonic()
    assert R.find_suggestions(big) == found
    assert big.setting("relationship_reading")["read"] == 0
    assert time.monotonic() - began < first


def test_a_changed_view_is_read_again(big):
    R.find_suggestions(big)
    key = big.conn.execute("SELECT key FROM assets WHERE kind = 'view' LIMIT 1").fetchone()[0]
    attrs = big.get_asset(key)["attrs"]
    attrs["definition"] = ("CREATE VIEW rpt.x AS SELECT * FROM dbo.SalesDaily20 s "
                           "JOIN dbo.Store9 st ON st.StoreKey = s.StoreKey")
    big.conn.execute("UPDATE assets SET attrs = ? WHERE key = ?", (json.dumps(attrs), key))
    R.find_suggestions(big)
    assert big.setting("relationship_reading")["read"] == 1
    row = big.conn.execute("SELECT joins FROM relationship_reads WHERE asset_key = ?", (key,)).fetchone()
    assert json.loads(row[0]) == [["dbo.store9", "StoreKey", "dbo.salesdaily20", "StoreKey"]]


def test_what_was_read_is_kept_when_the_step_is_stopped(big, monkeypatch):
    monkeypatch.setattr(R, "SAVE_EVERY", 0)
    real, calls = R.joins_in_sql, []

    def stop_after_ten(sql):
        calls.append(1)
        if len(calls) > 10:
            raise KeyboardInterrupt  # what pressing Stop (or Ctrl+C) does to a running step
        return real(sql)

    monkeypatch.setattr(R, "joins_in_sql", stop_after_ten)
    with pytest.raises(KeyboardInterrupt):
        R.find_suggestions(big)
    kept = _reads(big)
    assert 5 <= kept <= 10
    monkeypatch.setattr(R, "joins_in_sql", real)
    R.find_suggestions(big)
    assert big.setting("relationship_reading")["reused"] == kept


def test_the_time_cap_leaves_the_rest_for_next_time(big):
    said = []
    R.find_suggestions(big, report=lambda text, *a: said.append(text), budget=0)
    reading = big.setting("relationship_reading")
    assert reading["read"] == 0 and reading["left"] >= 400
    assert any("left to read next time" in t for t in said)
    R.find_suggestions(big)
    assert big.setting("relationship_reading")["left"] == 0


def test_the_step_shows_its_own_progress_and_can_be_switched_off(catalogue):
    attrs = catalogue.get_asset(VIEW)["attrs"]
    attrs["definition"] = "CREATE VIEW ml.v AS SELECT * FROM dbo.SalesDaily s JOIN dbo.Store st ON st.StoreId = s.StoreId"
    catalogue.conn.execute("UPDATE assets SET attrs = ? WHERE key = ?", (json.dumps(attrs), VIEW))
    said = []
    out = after_scan(catalogue, report=lambda text, done=None, total=None: said.append(text))
    assert out["relationships"] == 1
    assert any(t.startswith("After the scan: relationship suggestions (1 of") and "reading the SQL of" in t
               for t in said)
    set_on(catalogue, "relationships", False)
    assert after_scan(catalogue)["relationships"] == 0
    # Switching it off keeps what was found before.
    assert len(R.relationships(catalogue)) == 1


def test_name_matches_need_types_that_can_join(tmp_path):
    cat = Catalogue(tmp_path / "c.db")
    data = {"objects": [{"schema_name": "dbo", "object_name": n, "object_type": "U ", "definition": None}
                        for n in ("Store", "Sales", "Survey")],
            "columns": [
                {"schema_name": "dbo", "object_name": t, "column_name": "StoreId", "column_id": 1, "type_name": ty,
                 "max_length": 4, "precision": 10, "scale": 0, "is_nullable": False}
                for t, ty in (("Store", "int"), ("Sales", "bigint"), ("Survey", "nvarchar"))],
            "keys": [{"kind": "pk", "schema_name": "dbo", "object_name": "Store", "column_name": "StoreId",
                      "ordinal": 1, "constraint_name": "PK_Store"}]}
    with cat.scan("wh", prefix="sql://wh/db"):
        ingest(cat, BIG, "Db", data)
    R.ingest_keys(cat, "wh", "Db", data["keys"])
    R.find_suggestions(cat)
    pairs = {(r["src_key"], r["dst_key"]) for r in R.relationships(cat)}
    assert pairs == {("sql://wh/db/dbo.sales", "sql://wh/db/dbo.store")}  # not the text StoreId in Survey
    cat.close()


@pytest.mark.parametrize("sql, tables", [
    ("SELECT * FROM dbo.fnSales(@day) f JOIN dbo.Store s ON s.Id = f.Id", ["dbo.store"]),
    ("SELECT * FROM sys.objects o JOIN INFORMATION_SCHEMA.COLUMNS c ON 1 = 1 JOIN db.sys.tables t ON 1 = 1", []),
    ("SELECT TRIM(' ' FROM Name), SUBSTRING(Code FROM 2 FOR 3) FROM dbo.Store", ["dbo.store"]),
    ("SELECT * FROM dbo.Sales (NOLOCK) JOIN dbo.Store(nolock) ON 1 = 1", ["dbo.sales", "dbo.store"]),
])
def test_names_that_are_not_tables_are_left_out(sql, tables):
    assert [r.name for r in extract_refs(sql)] == tables


def test_unmatched_names_say_why(catalogue):
    key = "sql://warehouse/forecastdb/ml.vforecastlatest"
    for ref, note in (("table:otherdb.dbo.sales", None), ("table:far.db.dbo.x", None), ("table:sales_{}", None),
                      ("table:dbo.nothere", None), ("table:sys.objects", None)):
        catalogue.add_link(key, "reads", ref, "warehouse", evidence="test")
    why = {r["table"]: r["why"] for r in catalogue.unmatched_reasons()}
    assert why["otherdb.dbo.sales"] == "in database otherdb, not scanned"
    assert why["far.db.dbo.x"] == "on server far, not scanned"
    assert why["sales_{}"] == "name built while the code runs"
    assert why["dbo.nothere"] == "not in the databases you've scanned"
    assert "sys.objects" not in why


def test_a_table_page_has_a_small_diagram_of_its_joins(catalogue):
    from pdcat import erd
    sales, store = "sql://warehouse/forecastdb/dbo.salesdaily", "sql://warehouse/forecastdb/dbo.store"
    R.add_relationship(catalogue, sales, "StoreId", store, "StoreId")
    elements, left_out = erd.item_elements(catalogue, sales)
    nodes = {e["data"]["id"]: e["data"] for e in elements if "source" not in e["data"]}
    assert set(nodes) == {sales, store} and nodes[sales].get("focus") and left_out == 0
    assert [e["data"]["label"] for e in elements if "source" in e["data"]] == ["StoreId = StoreId"]
    assert "Region" not in nodes[store]["label"]  # fields are off by default
    assert "Region" in {e["data"]["id"]: e["data"] for e in erd.item_elements(catalogue, sales, fields=True)[0]
                        }[store]["label"]


def test_the_table_page_diagram_renders_and_opens_the_full_diagram(catalogue, monkeypatch):
    pytest.importorskip("streamlit")
    from pathlib import Path

    from streamlit.testing.v1 import AppTest
    sales, store = "sql://warehouse/forecastdb/dbo.salesdaily", "sql://warehouse/forecastdb/dbo.store"
    R.add_relationship(catalogue, sales, "StoreId", store, "StoreId")
    monkeypatch.setenv("PDCAT_DB", str(catalogue.path))
    app = AppTest.from_file(str(Path(__file__).parents[1] / "src" / "pdcat" / "app.py"), default_timeout=20)
    app.query_params["asset"] = sales
    app.run()
    assert not app.exception
    assert any(h.value == "Diagram" for h in app.subheader)
    [b for b in app.button if b.label.startswith("🗺️ Open the full database diagram")][0].click().run()
    assert not app.exception
    assert app.title[0].value == "Database diagrams"
    assert app.selectbox(key="erd-db").value == "sql://warehouse/forecastdb"
