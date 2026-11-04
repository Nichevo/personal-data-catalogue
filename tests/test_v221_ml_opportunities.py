"""Version 2.21: ML opportunities (recipes matched to the catalogue, readiness scores, splits, business project
links, related measures, baseline, leakage, export pack) and table types (snapshot, transaction, dimension)."""
import copy
import io
import json
import zipfile

import pytest
from conftest import FAKE_DB, SRC

from pdcat import bprojects, mlopps, mlrecipes, suggestions, tabletypes
from pdcat.scan_sqlserver import ingest
from pdcat.store import Catalogue, now

DB = "sql://warehouse/forecastdb/dbo."
POL, GR, VEN, SOH = DB + "purchaseorderlines", DB + "goodsreceipts", DB + "vendors", DB + "stockonhanddaily"

TABLES = {
    "PurchaseOrderLines": (1_800_000, [("EBELN", "nvarchar"), ("EBELP", "int"), ("MATNR", "nvarchar"),
                                       ("WERKS", "nvarchar"), ("LIFNR", "nvarchar"), ("OrderDate", "date"),
                                       ("PlannedLeadTime", "int"), ("ActualCost", "decimal")]),
    "GoodsReceipts": (2_300_000, [("EBELN", "nvarchar"), ("EBELP", "int"), ("ReceiptDate", "date"),
                                  ("Quantity", "decimal")]),
    "Vendors": (6_200, [("LIFNR", "nvarchar"), ("VendorName", "nvarchar"), ("Country", "nvarchar")]),
    "StockOnHandDaily": (27_000_000, [("SnapshotDate", "date"), ("MATNR", "nvarchar"), ("WERKS", "nvarchar"),
                                      ("LABST", "decimal")]),
}


def _db() -> dict:
    db = copy.deepcopy(FAKE_DB)
    for t, (rows, cols) in TABLES.items():
        db["objects"].append({"schema_name": "dbo", "object_name": t, "object_type": "U ", "row_count": rows,
                              "description": None, "definition": None})
        for i, (c, ty) in enumerate(cols):
            db["columns"].append({"schema_name": "dbo", "object_name": t, "column_name": c, "column_id": i + 1,
                                  "type_name": ty, "max_length": 20, "precision": 0, "scale": 0, "is_nullable": True,
                                  "description": None})
    db["objects"].append({"schema_name": "dbo", "object_name": "vLateReceipts", "object_type": "V ",
                          "row_count": None, "description": None,
                          "definition": "CREATE VIEW dbo.vLateReceipts AS SELECT p.EBELN FROM dbo.PurchaseOrderLines p "
                                        "JOIN dbo.GoodsReceipts g ON g.EBELN = p.EBELN AND g.EBELP = p.EBELP"})
    return db


def _profile(cat: Catalogue, key: str, column: str, rows: int, distinct: int, lo: str, hi: str,
             nulls: int = 0, mean: float | None = None) -> None:
    with cat.conn:
        cat.conn.execute("INSERT OR REPLACE INTO profiles (asset_key, column_name, row_count, null_count, "
                         "distinct_count, min_value, max_value, mean_value, profiled_at) VALUES (?, ?, ?, ?, ?, ?, ?, "
                         "?, ?)", (key, column, rows, nulls, distinct, lo, hi, mean, now()))


@pytest.fixture
def sc(catalogue: Catalogue) -> Catalogue:
    with catalogue.scan(SRC.name):
        ingest(catalogue, SRC, "ForecastDB", _db())
    _profile(catalogue, GR, "ReceiptDate", 2_300_000, 1500, "2022-10-03", "2026-11-01")
    _profile(catalogue, POL, "OrderDate", 1_800_000, 1600, "2022-08-01", "2026-11-02")
    _profile(catalogue, SOH, "SnapshotDate", 27_000_000, 700, "2024-12-01", "2026-11-02")
    _profile(catalogue, VEN, "LIFNR", 6_200, 6_200, "V0001", "V9999")
    # a Power BI report that reads the receipts table, with a measure about late receipts
    with catalogue.conn:
        catalogue.upsert_asset("pbi://reports/Supplier performance.pbix", "report", "Supplier performance.pbix",
                               "reports")
        catalogue.add_link("pbi://reports/Supplier performance.pbix", "reads", GR, "reports", dst_key=GR)
        catalogue.upsert_asset("pbi://reports/Supplier performance.pbix#m/late", "measure", "Late receipts %",
                               "reports", parent="pbi://reports/Supplier performance.pbix",
                               attrs={"name": "Late receipts %", "used_on": ["Overview", "Suppliers"],
                                      "expression": "DIVIDE(\n CALCULATE(COUNTROWS(Receipts), FILTER(Receipts, "
                                                    "Receipts[DaysLate] > [Tolerance]),\n Receipts[MoveType] = \"101\")"
                                                    ",\n [Receipt lines])"})
    from pdcat.relationships import find_suggestions
    find_suggestions(catalogue)
    return catalogue


def _lead(cat: Catalogue) -> dict:
    return next(o for o in mlopps.list_opps(cat) if o["recipe"] == "lead_time")


# ---- recipes ----------------------------------------------------------------------------------------------

def test_twelve_recipes_and_an_agent_example():
    real = [r for r in mlrecipes.RECIPES.values() if not r.get("example")]
    assert len(real) == 12
    assert mlrecipes.RECIPES["planner_agent"]["tool"] == "agent"
    for rid, r in mlrecipes.RECIPES.items():
        assert r["tool"] in mlrecipes.TOOLS, rid
        ids = {c["id"] for c in r["concepts"]}
        assert set(r.get("grain", [])) <= ids, rid
        assert any(c["role"] == "target" for c in r["concepts"]) or r.get("kind") in ("agent", "unsupervised"), rid


def test_recipes_can_be_switched_off_changed_and_added(catalogue):
    mlrecipes.set_on(catalogue, "freight", False)
    assert "freight" not in mlrecipes.all_recipes(catalogue)
    assert not mlrecipes.all_recipes(catalogue, include_off=True)["freight"]["on"]
    r = mlrecipes.get_recipe(catalogue, "returns")
    r["measure_words"] = ["return rate", "rma"]
    mlrecipes.save_recipe(catalogue, "returns", r)
    assert mlrecipes.get_recipe(catalogue, "returns")["measure_words"] == ["return rate", "rma"]
    assert mlrecipes.get_recipe(catalogue, "returns")["changed"]
    mlrecipes.reset_recipe(catalogue, "returns")
    assert "return" in mlrecipes.get_recipe(catalogue, "returns")["measure_words"]
    rid = mlrecipes.new_recipe_id(catalogue, "Customer cancellations")
    mlrecipes.save_recipe(catalogue, rid, {"name": "Customer cancellations", "concepts": [
        {"id": "cancel", "label": "Cancelled", "role": "target", "sap": ["ABGRU"], "names": ["cancelled"]}]})
    assert mlrecipes.all_recipes(catalogue)[rid]["name"] == "Customer cancellations"
    assert mlrecipes.RECIPES["returns"]["measure_words"] != ["return rate", "rma"]  # defaults never edited


# ---- matching and readiness ----------------------------------------------------------------------------------

def test_scan_matches_recipes_by_sap_and_friendly_names(sc):
    assert mlopps.refresh(sc) >= 12
    lead = _lead(sc)
    got = {i["item"]: i for i in mlopps.items(sc, lead["uid"])}
    assert GR in got and POL in got
    assert got[GR + "#ReceiptDate"]["role"] == "target"          # friendly name
    assert got[POL + "#LIFNR"]["concept"] == "supplier"          # SAP name
    assert got[POL + "#PlannedLeadTime"]["concept"] == "planned_lt"
    assert all(i["state"] == "suggested" for i in got.values())


def test_readiness_signals_and_score(sc):
    mlopps.refresh(sc)
    e = mlopps.evaluate(sc, _lead(sc)["uid"])
    sig = {s["id"]: s for s in e["signals"]}
    assert sig["target"]["state"] == "ready" and "ReceiptDate" in sig["target"]["evidence"]
    assert sig["history"]["state"] == "ready" and "4.1 years" in sig["history"]["evidence"]
    assert sig["grain"]["state"] in ("ready", "partial")
    assert sig["features"]["state"] in ("ready", "partial")
    assert sig["pain"]["state"] == "partial"                     # a measure found, not ticked yet
    assert 0 < e["score"] <= 100 and e["band"] in mlopps.BANDS


def test_columns_show_the_sap_original_next_to_the_friendly_name(sc):
    with sc.conn:
        sc.conn.execute("INSERT INTO column_info (asset_key, column_name, sap_name) VALUES (?, 'OrderDate', 'BEDAT')",
                        (POL,))
    mlopps.refresh(sc)
    e = mlopps.evaluate(sc, _lead(sc)["uid"])
    labels = [c["column"] for c in e["columns"]]
    assert any("OrderDate (SAP: BEDAT)" in x for x in labels)


def test_weights_change_the_score(sc):
    mlopps.refresh(sc)
    uid = _lead(sc)["uid"]
    mlopps.set_check(sc, uid, "pain", override="missing")
    before = mlopps.evaluate(sc, uid)["score"]
    sc.set_setting("ml_weights", {"target": 1, "history": 1, "grain": 1, "features": 1, "freshness": 1, "pain": 10})
    after = mlopps.evaluate(sc, uid)["score"]
    assert after < before


def test_confirm_and_override_a_signal(sc):
    mlopps.refresh(sc)
    uid = _lead(sc)["uid"]
    mlopps.set_check(sc, uid, "freshness", confirmed=True, override="ready", note="loads nightly")
    s = next(s for s in mlopps.evaluate(sc, uid)["signals"] if s["id"] == "freshness")
    assert s["confirmed"] and s["state"] == "ready" and s.get("overridden") and s["note"] == "loads nightly"
    mlopps.set_check(sc, uid, "freshness", override="")
    s = next(s for s in mlopps.evaluate(sc, uid)["signals"] if s["id"] == "freshness")
    assert not s.get("overridden")


def test_decisions_survive_the_next_scan(sc):
    mlopps.refresh(sc)
    uid = _lead(sc)["uid"]
    mlopps.set_item(sc, uid, GR + "#ReceiptDate", role="target")          # confirmed
    mlopps.remove_item(sc, uid, VEN)                                      # rejected
    mlopps.refresh(sc)
    got = {i["item"]: i for i in mlopps.items(sc, uid, include_rejected=True)}
    assert got[GR + "#ReceiptDate"]["state"] == "confirmed"
    assert VEN not in got or got[VEN]["state"] == "rejected"


def test_history_from_a_snapshot_table_when_the_target_keeps_today_only(sc):
    uid = mlopps.save(sc, "Safety stock test")
    mlopps.set_item(sc, uid, DB + "vendors", role="target")
    mlopps.set_item(sc, uid, SOH, role="feature")
    tabletypes.set_type(sc, SOH, "snapshot")
    mlopps.set_item(sc, uid, SOH + "#SnapshotDate", role="date")
    x = mlopps._Ctx(sc, mlopps.get(sc, uid))
    h = mlopps._history(x, 24)
    assert "StockOnHandDaily" in h["evidence"] or "stockonhanddaily" in h["evidence"].lower()


# ---- new opportunities, splits, business projects --------------------------------------------------------------

def test_new_opportunity_needs_only_a_name(sc):
    with pytest.raises(ValueError):
        mlopps.save(sc, " ")
    uid = mlopps.save(sc, "Customer order cancellations")
    o = mlopps.get(sc, uid)
    assert o["status"] == "idea" and not o["auto"]
    e = mlopps.evaluate(sc, uid)
    assert e["band"] == "notyet"


def test_split_copies_then_goes_its_own_way(sc):
    mlopps.refresh(sc)
    a = _lead(sc)["uid"]
    mlopps.set_item(sc, a, GR + "#ReceiptDate", role="target")
    p = bprojects.save_project(sc, "Purchasing")
    mlopps.link_project(sc, a, p)
    b = mlopps.split(sc, a, "Target B: late or on time")
    ob = mlopps.get(sc, b)
    assert ob["parent"] == a and ob["variant"] == "Target B: late or on time"
    assert mlopps.get(sc, a)["variant"] == "Target A"
    assert {i["item"] for i in mlopps.items(sc, b)} == {i["item"] for i in mlopps.items(sc, a)}
    assert [x["name"] for x in mlopps.projects(sc, b)] == ["Purchasing"]
    # from now on they're separate
    mlopps.set_item(sc, b, GR + "#Quantity", role="target")
    mlopps.save(sc, ob["name"], b, tool="classification")
    assert GR + "#Quantity" not in {i["item"] for i in mlopps.items(sc, a)}
    assert mlopps.get(sc, a)["tool"] != "classification"
    c = mlopps.split(sc, b)
    assert mlopps.get(sc, c)["variant"] == "Target C" and mlopps.get(sc, c)["parent"] == a
    rows = mlopps.ordered(mlopps.overview(sc))
    pos = [r["uid"] for r in rows]
    assert pos.index(a) < pos.index(b) < pos.index(c)


def test_business_project_links_suggest_its_tables(sc):
    uid = mlopps.save(sc, "Supplier delays")
    mlopps.set_item(sc, uid, POL, role="target")
    p = bprojects.save_project(sc, "Purchasing")
    bprojects.set_member(sc, p, VEN)
    bprojects.set_member(sc, p, DB + "salesdaily")       # shares no keys: not suggested
    n = mlopps.link_project(sc, uid, p)
    got = {i["item"]: i for i in mlopps.items(sc, uid)}
    assert n == 1 and got[VEN]["state"] == "suggested" and "Purchasing" in got[VEN]["why"]
    assert [o["uid"] for o in mlopps.opps_for_project(sc, p)] == [uid]
    mlopps.unlink_project(sc, uid, p)
    assert mlopps.projects(sc, uid) == []


# ---- measures, baseline, leakage, quality --------------------------------------------------------------------

def test_related_measures_with_complexity_and_ticks(sc):
    mlopps.refresh(sc)
    uid = _lead(sc)["uid"]
    rel = mlopps.related_measures(sc, uid)
    m = next(m for m in rel if m["measure"] == "Late receipts %")
    assert m["level"] in ("Complex", "Medium") and "CALCULATE" in m["detail"] and not m["ticked"]
    assert "reads" in m["found"]
    mlopps.tick_measure(sc, uid, m["key"], True)
    e = mlopps.evaluate(sc, uid)
    assert next(s for s in e["signals"] if s["id"] == "pain")["state"] == "ready"
    sql = mlopps.use_as_target(sc, uid, m["key"])
    assert sql.startswith("-- Read-only") and "DIVIDE" in sql and "SELECT" in sql
    assert next(x for x in mlopps.related_measures(sc, uid) if x["key"] == m["key"])["target"]
    mlopps.tick_measure(sc, uid, m["key"], False)
    assert not any(x["ticked"] for x in mlopps.related_measures(sc, uid))


def test_complexity_levels():
    assert mlopps.complexity("SUM(Sales[Units])")[0] == "Simple"
    assert mlopps.complexity("CALCULATE(SUM(S[U]), FILTER(ALL(D), D[Y] = 1), KEEPFILTERS(V[A]))\n" * 3)[0] == \
        "Complex"


def test_baseline_pair_and_leakage(sc):
    mlopps.refresh(sc)
    uid = _lead(sc)["uid"]
    e = mlopps.evaluate(sc, uid)
    b = e["baseline"]
    assert b["pair"] and "PlannedLeadTime" in b["sql"] and "DATEDIFF" in b["sql"] and b["sql"].startswith("-- Read")
    mlopps.set_item(sc, uid, POL + "#ActualCost", role="feature")
    assert any("ActualCost" in w for w in mlopps.evaluate(sc, uid)["leakage"])
    mlopps.save(sc, "Lead time prediction", uid, baseline="Planned lead time is off by 6.2 days on average")
    assert mlopps.evaluate(sc, uid)["baseline"]["typed"].startswith("Planned")


def test_target_quality_flags_blanks(sc):
    _profile(sc, GR, "ReceiptDate", 1000, 300, "2022-10-03", "2026-11-01", nulls=200)
    mlopps.refresh(sc)
    q = mlopps.evaluate(sc, _lead(sc)["uid"])["quality"]
    assert any(x["level"] == "warn" and "blank" in x["text"] for x in q)


def test_shared_tables(sc):
    mlopps.refresh(sc)
    shared = mlopps.shared_tables(sc)
    assert any(s["key"] == POL and s["count"] >= 2 for s in shared)


def test_export_pack(sc):
    mlopps.refresh(sc)
    uid = _lead(sc)["uid"]
    data = mlopps.export_pack(sc, uid)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        assert {"README.md", "columns.csv", "tables.csv", "measures.csv", "starter.sql", "baseline.sql"} <= names
        readme = z.read("README.md").decode()
        assert "Readiness" in readme and "Lead time prediction" in readme
        assert "ReceiptDate" in z.read("columns.csv").decode("utf-8-sig")


def test_feature_ideas_list_tables_not_read_yet(sc):
    uid = mlopps.save(sc, "Supplier delays")
    mlopps.set_item(sc, uid, GR + "#ReceiptDate", role="target")
    mlopps.set_item(sc, uid, GR, role="target")
    ideas = mlopps.feature_ideas(sc, uid)
    assert any(i["key"] == POL for i in ideas)


def test_agent_example_uses_question_library(sc):
    mlopps.refresh(sc)
    o = next(o for o in mlopps.list_opps(sc) if o["recipe"] == "planner_agent")
    e = mlopps.evaluate(sc, o["uid"])
    sig = {s["id"]: s for s in e["signals"]}
    assert "Question library" in sig["target"]["evidence"]
    assert sig["history"]["state"] == "ready"


def test_switched_off_recipe_drops_out_of_the_list(sc):
    mlopps.refresh(sc)
    mlrecipes.set_on(sc, "freight", False)
    assert not any(o["recipe"] == "freight" for o in mlopps.list_opps(sc))


def test_opportunities_never_write_outside_the_catalogue(sc, monkeypatch):
    """Read-only: nothing here opens a connection to SQL Server."""
    import pdcat.scan_sqlserver as S
    monkeypatch.setattr(S, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("connected")),
                        raising=False)
    mlopps.refresh(sc)
    for o in mlopps.list_opps(sc):
        mlopps.evaluate(sc, o["uid"])
        mlopps.export_pack(sc, o["uid"])


# ---- table types -------------------------------------------------------------------------------------------

def test_table_type_guesses():
    snap = tabletypes.guess("dbo.StockOnHandDaily", [{"name": "SnapshotDate", "data_type": "date"},
                                                     {"name": "MATNR", "data_type": "nvarchar"}],
                            {"SnapshotDate": {"row_count": 1_000_000, "distinct_count": 700}}, 1_000_000)
    assert snap["type"] == "snapshot" and snap["band"] == "likely"
    trans = tabletypes.guess("dbo.GoodsReceipts", [{"name": "EBELN", "data_type": "nvarchar"},
                                                   {"name": "PostingDate", "data_type": "date"}],
                             {"PostingDate": {"row_count": 2_000_000, "distinct_count": 1500}}, 2_000_000, grew=True)
    assert trans["type"] == "transaction"
    dim = tabletypes.guess("dbo.Vendors", [{"name": "LIFNR", "data_type": "nvarchar"},
                                           {"name": "VendorName", "data_type": "nvarchar"},
                                           {"name": "Country", "data_type": "nvarchar"}],
                           {"LIFNR": {"row_count": 6200, "distinct_count": 6200}}, 6200)
    assert dim["type"] == "dimension"
    assert tabletypes.guess("dbo.Thing", [{"name": "A", "data_type": "int"}], {}, 10) is None


def test_table_type_suggestions_accept_and_filter(sc):
    n = tabletypes.suggest_all(sc)
    assert n >= 3
    rows = {s["subject"]: s for s in suggestions.open_suggestions(sc, "table_type")}
    assert json.loads(json.dumps(rows[SOH]["payload"]))["type"] == "snapshot"
    suggestions.accept(sc, rows[SOH]["id"])
    assert tabletypes.table_type(sc, SOH) == "snapshot"
    assert tabletypes.all_types(sc)[SOH] == "snapshot"
    assert SOH not in {s["subject"] for s in suggestions.open_suggestions(sc, "table_type")} or \
        tabletypes.suggest_all(sc) >= 0
    tabletypes.suggest_all(sc)
    assert SOH not in {s["subject"] for s in suggestions.open_suggestions(sc, "table_type")}
    with pytest.raises(ValueError):
        tabletypes.set_type(sc, SOH, "fact")


def test_table_type_triage_uses_its_own_band(sc):
    from pdcat.triage import triage
    tabletypes.suggest_all(sc)
    rows = triage(sc, "table_type", suggestions.open_suggestions(sc, "table_type"))
    assert rows and rows[0]["band"] in ("likely", "check") and rows[0]["group"] in ("Snapshot", "Transaction",
                                                                                     "Dimension")


def _draw(db: str, page: str, key: str = "") -> object:
    from streamlit.testing.v1 import AppTest

    def app(db: str, page: str, key: str) -> None:
        from pdcat.store import Catalogue
        from pdcat.ui import browse_pages, curation_pages, mlopp_pages, settings_page
        from pdcat.ui.detail import detail
        c = Catalogue(db)
        if page == "list":
            mlopp_pages.ml_opportunities_page(c)
        elif page == "opp":
            mlopp_pages.opportunity_page(c, key)
        elif page == "tables":
            browse_pages.tables_page(c)
        elif page == "bulk":
            curation_pages.bulk_edit_page(c)
        elif page == "settings":
            settings_page.ml_section(c)
        else:
            detail(c, key)

    return AppTest.from_function(app, args=(db, page, key)).run(timeout=90)


def test_pages_draw(sc):
    db = str(sc.path)
    mlopps.refresh(sc)
    lead = _lead(sc)
    mlopps.split(sc, lead["uid"])
    tabletypes.set_type(sc, SOH, "snapshot")
    for page, key in (("list", ""), ("opp", f"mlo://{lead['uid']}"), ("tables", ""), ("bulk", ""),
                      ("settings", ""), ("item", POL), ("item", SOH)):
        at = _draw(db, page, key)
        assert not at.exception, (page, at.exception)
    at = _draw(db, "opp", f"mlo://{lead['uid']}")
    assert any("Lead time" in m.value for m in at.markdown) or any("Lead time" in t.value for t in at.title)


def test_new_opportunity_from_the_page(sc):
    at = _draw(str(sc.path), "list")
    next(t for t in at.text_input if t.label == "Name").set_value("Pallet damage prediction")
    next(b for b in at.button if b.label == "Save opportunity").click()
    at.run(timeout=90)
    assert not at.exception, at.exception
    assert any(o["name"] == "Pallet damage prediction" for o in mlopps.list_opps(Catalogue(str(sc.path))))
