"""Version 2.30: ML opportunities use table profiles: a Data quality signal, stricter targets and features, why
each check came out as it did, gates on the band, tables to profile, deeper checks and a suggested effort."""
import io
import json
import zipfile

from test_v221_ml_opportunities import GR, POL, SOH, VEN, _draw, _lead, _profile, sc  # noqa: F401

from pdcat import mlopps, mlquality
from pdcat.pacing import full_table_asked, request_profiles
from pdcat.store import Catalogue, now


def _sig(e: dict, sid: str) -> dict:
    return next(s for s in e["signals"] if s["id"] == sid)


def _set_profile(cat: Catalogue, key: str, column: str, **fields) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    with cat.conn:
        cat.conn.execute(f"UPDATE profiles SET {sets} WHERE asset_key = ? AND column_name = ?",
                         (*fields.values(), key, column))


def test_seven_signals_each_with_why(sc):  # noqa: F811
    mlopps.refresh(sc)
    e = mlopps.evaluate(sc, _lead(sc)["uid"])
    assert [s["id"] for s in e["signals"]] == [s for s, _ in mlopps.SIGNALS]
    assert "quality" in mlopps.DEFAULT_WEIGHTS
    t = _sig(e, "target")
    assert any("Picked because" in w for w in t["why"]) and any(w.startswith("Profile") for w in t["why"])
    assert any("BUDAT" in w for w in t["why"])                     # the SAP field the recipe looks for


def test_target_guessed_from_its_name_only_is_partial(sc):  # noqa: F811
    with sc.conn:
        sc.conn.execute("DELETE FROM profiles WHERE asset_key = ?", (GR,))
    mlopps.refresh(sc)
    uid = _lead(sc)["uid"]
    t = _sig(mlopps.evaluate(sc, uid), "target")
    assert t["state"] == "partial" and "guessed" in t["evidence"] and t["raise_by"]
    mlopps.set_item(sc, uid, GR + "#ReceiptDate", role="target")   # confirm it
    assert _sig(mlopps.evaluate(sc, uid), "target")["state"] == "ready"


def test_mostly_blank_target_is_not_ready_and_not_suggested(sc):  # noqa: F811
    mlopps.refresh(sc)
    uid = _lead(sc)["uid"]
    mlopps.set_item(sc, uid, GR + "#ReceiptDate", role="target")
    _profile(sc, GR, "ReceiptDate", 1000, 10, "2022-10-03", "2026-11-01", nulls=900)
    t = _sig(mlopps.evaluate(sc, uid), "target")
    assert t["state"] == "partial" and "blank in 90%" in t["evidence"]
    other = mlopps.save(sc, "Another lead time", recipe="lead_time")
    mlopps.suggest_for(sc, other)
    assert GR + "#ReceiptDate" not in {i["item"] for i in mlopps.items(sc, other)}


def test_band_is_held_back_without_target_and_history(sc):  # noqa: F811
    o = {"status": "idea"}
    sig = [{"id": s, "state": "ready"} for s, _ in mlopps.SIGNALS]
    assert mlopps.band(o, 90, sig) == ("ready", "")
    sig[1]["state"] = "partial"                                    # history
    b, why = mlopps.band(o, 90, sig)
    assert b == "partial" and "history" in why
    sig[0]["state"] = "missing"                                    # target
    b, why = mlopps.band(o, 60, sig)
    assert b == "notyet" and "no target" in why


def test_history_unknown_until_profiled_and_sample_is_flagged(sc):  # noqa: F811
    with sc.conn:
        sc.conn.execute("DELETE FROM profiles")
    mlopps.refresh(sc)
    h = _sig(mlopps.evaluate(sc, _lead(sc)["uid"]), "history")
    assert h["state"] == "missing" and "profiled" in h["evidence"]
    _profile(sc, GR, "ReceiptDate", 2_300_000, 1500, "2022-10-03", "2026-11-01")
    _set_profile(sc, GR, "ReceiptDate", method="sample of 100,000 rows for counts and ranges; common values from "
                                               "first 5,000 rows")
    e = mlopps.evaluate(sc, _lead(sc)["uid"])
    h = _sig(e, "history")
    assert "sample" in h["evidence"] and any("sample" in w for w in h["why"])
    deeper = {d["title"]: d for d in e["deeper"]}
    exact = next(d for t, d in deeper.items() if t.startswith("Exact date range"))
    assert exact["full_profile"] == GR and "MIN([ReceiptDate])" in exact["sql"] and "Read-only" in exact["sql"]
    assert any(t.startswith("Rows per month") for t in deeper)


def test_data_quality_signal_and_issues(sc):  # noqa: F811
    mlopps.refresh(sc)
    uid = _lead(sc)["uid"]
    mlopps.set_item(sc, uid, POL + "#ActualCost", role="feature")
    _profile(sc, POL, "ActualCost", 1_800_000, 1, "0", "0")         # one value only
    _profile(sc, POL, "EBELN", 1_800_000, 1_790_000, "4500000000", "4599999999")
    mlopps.set_item(sc, uid, POL + "#EBELN", role="feature")         # an ID used as a feature
    e = mlopps.evaluate(sc, uid)
    texts = " ".join(i["text"] for i in e["issues"])
    assert "one value only" in texts and "ID" in texts
    q = _sig(e, "quality")
    assert q["state"] in ("partial", "ready") and "profiled" in q["evidence"]
    cols = {c["item"]: c for c in e["columns"]}
    assert cols[POL + "#ActualCost"]["check"] == "one value only"


def test_quality_missing_when_nothing_profiled(sc):  # noqa: F811
    with sc.conn:
        sc.conn.execute("DELETE FROM profiles")
    mlopps.refresh(sc)
    e = mlopps.evaluate(sc, _lead(sc)["uid"])
    assert _sig(e, "quality")["state"] == "missing"
    assert any(r["state"] == "none" and r["can_queue"] for r in e["coverage"])
    assert e["effort_suggested"] >= 2 and any("not profiled" in w for w in e["effort_why"])


def test_tables_to_profile_and_queue_without_running(sc):  # noqa: F811
    mlopps.refresh(sc)
    rows = mlquality.tables_to_profile(sc)
    keys = {r["key"] for r in rows}
    assert POL not in keys and GR not in keys                       # profiled in the fixture
    assert rows and all(r["needed_by"] >= 1 and r["opportunities"] for r in rows)
    request_profiles(sc, [rows[0]["key"]])
    state = sc.conn.execute("SELECT state FROM profile_queue WHERE asset_key = ?", (rows[0]["key"],)).fetchone()[0]
    assert state == "queued"                                        # waits for approval
    assert rows[0]["key"] not in {r["key"] for r in mlquality.tables_to_profile(sc)}


def test_full_table_profile_request(sc):  # noqa: F811
    request_profiles(sc, [SOH], full_table=True)
    assert full_table_asked(sc, SOH)
    request_profiles(sc, [VEN])
    assert not full_table_asked(sc, VEN)
    request_profiles(sc, [VEN], full_table=True)                    # already waiting: turned on
    assert full_table_asked(sc, VEN)
    from pdcat.pacing import plan_profile
    plan = plan_profile(sc, [SOH])
    assert "whole table" in plan[0]["purpose"]


def test_freshness_from_newest_profiled_date(sc):  # noqa: F811
    mlopps.refresh(sc)
    uid = _lead(sc)["uid"]
    f = _sig(mlopps.evaluate(sc, uid), "freshness")
    assert f["state"] == "ready" and "Rows up to" in f["evidence"]
    for c in ("ReceiptDate",):
        _profile(sc, GR, c, 2_300_000, 1500, "2019-10-03", "2023-01-01")
    f = _sig(mlopps.evaluate(sc, uid), "freshness")
    assert f["state"] == "missing" and "no longer" in f["evidence"]


def test_classification_target_balance_and_placeholders(sc):  # noqa: F811
    uid = mlopps.save(sc, "Late or not", tool="classification")
    mlopps.set_item(sc, uid, GR + "#Late", kind="column", role="target")
    with sc.conn:
        sc.conn.execute("INSERT OR REPLACE INTO profiles (asset_key, column_name, row_count, null_count, "
                        "distinct_count, min_value, max_value, top_values, profiled_at) VALUES (?, 'Late', 1000, 0, "
                        "2, '0', '1', ?, ?)", (GR, json.dumps([["0", 995], ["1", 5]]), now()))
    q = mlopps.evaluate(sc, uid)["quality"]
    assert any("rarest outcome" in x["text"] for x in q)
    assert any("“0”" in x["text"] for x in q)                      # 99.5% zeros reads as a stand-in too


def test_same_blank_share_as_target_warns_of_leakage(sc):  # noqa: F811
    uid = mlopps.save(sc, "Receipts")
    mlopps.set_item(sc, uid, GR + "#ReceiptDate", kind="column", role="target")
    mlopps.set_item(sc, uid, GR + "#Quantity", kind="column", role="feature")
    _profile(sc, GR, "ReceiptDate", 1000, 300, "2022-10-03", "2026-11-01", nulls=200)
    _profile(sc, GR, "Quantity", 1000, 300, "1", "500", nulls=205)
    assert any("Quantity" in w and "same as the target" in w for w in mlopps.evaluate(sc, uid)["leakage"])


def test_grain_and_match_rate_queries(sc):  # noqa: F811
    mlopps.refresh(sc)
    e = mlopps.evaluate(sc, _lead(sc)["uid"])
    sql = "\n".join(d["sql"] or "" for d in e["deeper"])
    assert "COUNT_BIG(*) > 1" in sql or "EXISTS" in sql or "Rows per month" in sql
    assert "INSERT" not in sql.upper() and "UPDATE " not in sql.upper() and "DELETE" not in sql.upper()


def test_pack_has_why_and_deeper_checks(sc):  # noqa: F811
    mlopps.refresh(sc)
    data = mlopps.export_pack(sc, _lead(sc)["uid"])
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        readme = z.read("README.md").decode()
        assert "Data quality" in readme and "Suggested effort" in readme and "Picked because" in readme
        assert "deeper_checks.sql" in z.namelist()


def _draw_parts(db: str, uid: str) -> object:
    from streamlit.testing.v1 import AppTest

    def app(db: str, uid: str) -> None:
        from pdcat import mlopps as M
        from pdcat.store import Catalogue
        from pdcat.ui import mlopp_pages as P
        c = Catalogue(db)
        e = M.evaluate(c, uid)
        P._checklist(c, uid, e)
        P._tool_box(e["opp"], e)
        P._tables(c, uid, e)
        P._data_quality(c, uid, e)
        P._measures(c, uid, e)
        P._to_profile(c)
        P._value_chart(c)

    return AppTest.from_function(app, args=(db, uid)).run(timeout=90)


def test_pages_draw_with_profiles(sc):  # noqa: F811
    mlopps.refresh(sc)
    lead = _lead(sc)
    mlopps.save(sc, lead["name"], lead["uid"], value=4)
    db = str(sc.path)
    for page, key in (("list", ""), ("opp", f"mlo://{lead['uid']}"), ("settings", "")):
        at = _draw(db, page, key)
        assert not at.exception, (page, at.exception)
    at = _draw_parts(db, lead["uid"])
    assert not at.exception, at.exception
    assert any(e.label == "Why" for e in at.expander)
    assert any("Data quality" in s.value for s in at.subheader)
    assert any("Deeper checks" in m.value for m in at.markdown)
    assert any(b.label.startswith("Queue") for b in at.button)
    _model(sc)
    mlopps.use_as_target(sc, lead["uid"], f"{REP}#m/Late receipts %")
    at = _draw_parts(db, lead["uid"])
    assert not at.exception, at.exception
    assert any(e.label.startswith("Other measures in") for e in at.expander)
    assert any("comes from" in m.value for m in at.markdown)


# ---- a Power BI measure as the target ----------------------------------------------------------------------------

REP = "pbi://reports/Receipts model.pbix"


def _model(cat: Catalogue) -> None:
    with cat.conn:
        cat.upsert_asset(REP, "report", "Receipts model.pbix", "reports", attrs={
            "tables": [{"name": "Receipts", "columns": [{"name": "DaysLate"}, {"name": "MoveType"},
                                                        {"name": "Quantity"}]},
                       {"name": "Vendors", "columns": [{"name": "Country"}]}],
            "queries": [{"name": "Receipts", "tables": ["dbo.GoodsReceipts"]},
                        {"name": "Vendors", "tables": ["dbo.Vendors"]}],
            "pages": [{"name": "Overview", "visuals": [{"title": "Late", "fields": [
                {"table": "Receipts", "name": "Late receipts %", "kind": "measure"}]}]}]})
        cat.add_link(REP, "reads", GR, "reports", dst_key=GR, how="Power Query “Receipts”")
        cat.add_link(REP, "reads", VEN, "reports", dst_key=VEN, how="Power Query “Vendors”")
        for name, expr in (("Receipt lines", "COUNTROWS(Receipts)"),
                           ("Late receipts", "CALCULATE([Receipt lines], Receipts[DaysLate] > 0)"),
                           ("Late receipts %", "DIVIDE([Late receipts], [Receipt lines])"),
                           ("Late trend", "[Late receipts %] - 0.1"),
                           ("Total quantity", "SUM(Receipts[Quantity])"),
                           ("Vendor countries", "DISTINCTCOUNT(Vendors[Country])")):
            cat.upsert_asset(f"{REP}#m/{name}", "measure", name, "reports", parent=REP,
                             attrs={"name": name, "table": "Receipts", "expression": expr})


def test_measure_target_sources_and_other_measures(sc):  # noqa: F811
    from pdcat import mlpbi
    _model(sc)
    key = f"{REP}#m/Late receipts %"
    src = mlpbi.measure_sources(sc, key)
    assert "Receipts[DaysLate]" in src["columns"]
    assert any(s["key"] == GR and s["catalogued"] for s in src["sources"])
    others = {m["measure"]: m for m in mlpbi.other_measures(sc, key)}
    assert others["Late receipts"]["leaks"] and "built from" in others["Late receipts"]["relation"]
    assert others["Late trend"]["leaks"] and others["Late trend"]["relation"] == "built on the target"
    assert not others["Total quantity"]["leaks"] and others["Total quantity"]["shared"] == ["Receipts"]
    assert not others["Vendor countries"]["shared"]
    order = [m["measure"] for m in mlpbi.other_measures(sc, key)]
    assert order.index("Total quantity") < order.index("Vendor countries") < order.index("Late receipts")

    uid = mlopps.save(sc, "Late receipts")
    mlopps.use_as_target(sc, uid, key)
    t = _sig(mlopps.evaluate(sc, uid), "target")
    assert any("Original data" in w and "goodsreceipts" in w.lower() for w in t["why"])
    mlopps.set_item(sc, uid, f"{REP}#m/Late receipts", kind="measure", role="feature")
    assert any("gives the answer away" in w for w in mlopps.evaluate(sc, uid)["leakage"])
