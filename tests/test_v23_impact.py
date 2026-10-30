"""Version 2.3: Power BI impact from the files alone: thin reports matched to their model, usage across connected
reports, "not used in scanned files", dependency chains, broken visuals as check items."""
from __future__ import annotations

from pdcat import pbimpact as PI
from pdcat.pbiobjects import table_key
from pdcat.scan_powerbi import measure_key

MODEL = "files://reports/Supply model.pbix"
THIN = "files://reports/Stock on hand.pbix"


def _visual(page, title, fields):
    return {"name": page, "visuals": [{"type": "table", "title": title, "fields": fields}]}


def _model(cat, *, modified="2026-03-01T09:30:00"):
    attrs = {
        "modified": modified,
        "tables": [
            {"name": "Stock", "columns": [{"name": "Plant", "data_type": "string", "calculated": False},
                                          {"name": "Units", "data_type": "int64", "calculated": False},
                                          {"name": "Cost", "data_type": "decimal", "calculated": False},
                                          {"name": "Value", "data_type": "decimal", "calculated": True,
                                           "expression": "Stock[Units] * Stock[Cost]"},
                                          {"name": "Spare", "data_type": "string", "calculated": False}]},
            {"name": "Plant", "columns": [{"name": "PlantId", "data_type": "string", "calculated": False},
                                          {"name": "Region", "data_type": "string", "calculated": False}]},
            {"name": "Unused", "columns": [{"name": "X", "data_type": "string", "calculated": False}]},
        ],
        "measures": [
            {"table": "Stock", "name": "Total units", "expression": "SUM(Stock[Units])"},
            {"table": "Stock", "name": "Total value", "expression": "SUMX(Stock, Stock[Value])"},
            {"table": "Stock", "name": "Value per unit", "expression": "DIVIDE([Total value], [Total units])"},
            {"table": "Stock", "name": "Orphan", "expression": "SUM(Stock[Spare])"},
        ],
        "relationships": [{"from": "Stock.Plant", "to": "Plant.PlantId", "active": True}],
        "pages": [_visual("Overview", "Units by plant", [{"table": "Plant", "name": "Region", "kind": "column"},
                                                          {"table": "Stock", "name": "Total units", "kind": "measure"}])],
    }
    cat.upsert_asset(MODEL, "report", "Supply model.pbix", "reports", attrs=attrs)
    for m in attrs["measures"]:
        cat.upsert_asset(measure_key(MODEL, m["table"], m["name"]), "measure", f"{m['table']}[{m['name']}]", "reports",
                         parent=MODEL, attrs=m)
    cat.conn.commit()


def _thin(cat, dataset="Supply model", fields=None):
    fields = fields if fields is not None else [{"table": "Stock", "name": "Value per unit", "kind": "measure"}]
    cat.upsert_asset(THIN, "report", "Stock on hand.pbix", "reports",
                     attrs={"live": {"dataset": dataset}, "tables": [], "measures": [],
                            "pages": [_visual("Page 1", "KPI", fields)]})
    cat.conn.commit()


def test_thin_report_matches_its_model_by_dataset_name_and_by_hand(catalogue):
    _model(catalogue)
    _thin(catalogue, dataset="Supply Model")
    m = PI.model_matches(catalogue)[THIN]
    assert m["model"] == MODEL and m["how"] == "name"
    assert [c["key"] for c in PI.connected_reports(catalogue, MODEL)] == [THIN]
    _thin(catalogue, dataset="Supply model v3")
    assert PI.model_matches(catalogue)[THIN]["how"] == "words"
    _thin(catalogue, dataset="Something else")
    assert PI.model_matches(catalogue)[THIN]["model"] is None
    PI.set_model(catalogue, THIN, MODEL)
    assert PI.model_matches(catalogue)[THIN] == {"model": MODEL, "how": "manual", "candidates": [],
                                                 "dataset": "Something else"}
    PI.forget_model(catalogue, THIN)
    assert PI.model_matches(catalogue)[THIN]["model"] is None
    assert PI.model_of(catalogue, MODEL) == MODEL


def test_usage_follows_visuals_in_connected_reports_and_dax_chains(catalogue):
    _model(catalogue)
    _thin(catalogue)
    u = PI.usage(catalogue, MODEL)
    assert [r["key"] for r in u["reports"]] == [MODEL, THIN]
    m = u["measures"]
    assert m["Stock[Total units]"]["used"] and "Overview" in str(m["Stock[Total units]"]["visuals"])
    # the thin report shows Value per unit, which needs Total value, which needs the calculated column Value,
    # which needs Units and Cost
    assert m["Stock[Value per unit]"]["why"].startswith("shown in 1 visual(s) in Stock on hand")
    assert m["Stock[Total value]"]["why"] == "needed by Stock[Value per unit]"
    c = u["columns"]
    assert c["Stock[Value]"]["why"] == "needed by Stock[Total value]" and c["Stock[Cost]"]["used"]
    assert c["Stock[Plant]"]["why"] == "in a relationship" and c["Plant[Region]"]["used"]
    assert not m["Stock[Orphan]"]["used"] and m["Stock[Orphan]"]["why"] == PI.NOT_USED
    assert c["Stock[Spare]"]["in_measures"] == ["Stock[Orphan]"] and not c["Stock[Spare]"]["used"]
    assert not u["tables"]["Unused"]["used"] and u["tables"]["Stock"]["columns_used"] == 4
    # the lists hide what nothing uses, and the switch is remembered
    assert PI.unused_keys(catalogue, "measures") == {measure_key(MODEL, "Stock", "Orphan")}
    assert PI.unused_keys(catalogue, "tables") == {table_key(MODEL, "Unused")}
    assert not PI.show_unused(catalogue)
    PI.set_show_unused(catalogue, True)
    assert PI.show_unused(catalogue)
    rows = {(r["table"], r["column"]): r for r in PI.column_rows(catalogue)}
    assert rows[("Stock", "Units")]["in measures"] == "Stock[Total units]" and not rows[("Unused", "X")]["used"]
    # the measures table counts the connected report's visual as a use
    from pdcat.measures import measure_rows
    by = {r["measure"]: r for r in measure_rows(catalogue, MODEL)}
    assert by["Value per unit"]["used"] and by["Orphan"]["why"] == PI.NOT_USED
    # SQL tables nothing reads
    sql_unused = PI.unused_keys(catalogue, "sql")
    assert "sql://warehouse/forecastdb/dbo.salesdaily" not in sql_unused     # the script reads it
    assert "sql://warehouse/forecastdb/dbo.store" not in sql_unused         # joined in the script
    assert "sql://warehouse/forecastdb/ml.vforecastlatest" in sql_unused     # nothing reads the view


def test_measure_chain_both_ways(catalogue):
    _model(catalogue)
    _thin(catalogue)
    chain = PI.measure_chain(catalogue, measure_key(MODEL, "Stock", "Value per unit"))
    deps = {(d["label"], d["kind"], d["depth"]) for d in chain["depends_on"]}
    assert ("Stock[Total value]", "measure", 1) in deps and ("Stock[Total units]", "measure", 1) in deps
    assert ("Stock[Value]", "calculated column", 2) in deps and ("Stock[Units]", "column", 2) in deps
    assert ("Stock[Cost]", "column", 3) in deps
    assert chain["needed_by"] == [] and chain["affected"] == [("Stock on hand.pbix", "Page 1", "KPI")]
    chain = PI.measure_chain(catalogue, measure_key(MODEL, "Stock", "Total value"))
    assert [n["label"] for n in chain["needed_by"]] == ["Stock[Value per unit]"]
    assert chain["affected"] == [("Stock on hand.pbix", "Page 1", "KPI")]


def test_broken_visuals_become_check_items_naming_the_model_version(catalogue):
    _model(catalogue)
    _thin(catalogue, fields=[{"table": "Stock", "name": "Gone measure", "kind": "measure"},
                             {"table": "Stock", "name": "Units", "kind": "column"},
                             {"table": "Stock", "name": "Renamed", "kind": "column"}])
    broken = PI.broken_visuals(catalogue)
    assert [(b["field"], b["model_version"]) for b in broken] == [("Stock[Gone measure]", "2026-03-01"),
                                                                   ("Stock[Renamed]", "2026-03-01")]
    from pdcat.review import open_tasks, refresh_tasks
    refresh_tasks(catalogue)
    tasks = open_tasks(catalogue, rule="broken_visual")
    assert len(tasks) == 1 and tasks[0]["asset_key"] == THIN
    assert "2 field(s)" in tasks[0]["title"] and "as read on 2026-03-01" in tasks[0]["title"]
    assert "Page 1 › KPI: Stock[Gone measure]" in tasks[0]["detail"]
