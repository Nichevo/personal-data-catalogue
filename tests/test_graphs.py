"""The graph viewer's elements: code map groups and packages, and ERD subject areas."""
from pdcat import codemap, erd
from pdcat.relationships import add_relationship

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"
STORE = "sql://warehouse/forecastdb/dbo.store"
DB = "sql://warehouse/forecastdb"


def test_code_map_elements(catalogue):
    els = codemap.cyto_elements(catalogue, None, include_packages=True)
    nodes = {e["data"]["id"]: e["data"] for e in els if "source" not in e["data"]}
    script = nodes["py://forecasting/src/features.py"]
    assert script["parent"] == "grp:forecasting/src" and nodes["grp:forecasting/src"]["parent"] == "grp:forecasting"
    assert nodes[SALES]["parent"] == "grp:db:warehouse/ForecastDB" and nodes[SALES]["key"] == SALES
    assert nodes["pkg://pandas"]["parent"] == "grp:packages" and nodes["grp:packages"]["closed"]
    assert nodes["grp:forecasting"]["count"] == 1
    edges = [e["data"] for e in els if "source" in e["data"]]
    assert {(e["target"], e["relation"]) for e in edges if e["source"] == script["id"]} >= {
        (SALES, "reads"), ("pkg://pandas", "uses")}
    # Every edge's ends exist.
    assert all(e["source"] in nodes and e["target"] in nodes for e in edges)


def test_subject_areas(catalogue):
    areas = erd.suggest_areas(catalogue, DB)
    where = {m["key"]: a["name"] for a in areas for m in a["members"]}
    assert where["sql://warehouse/forecastdb/ml.forecastoutput"] == where["sql://warehouse/forecastdb/ml.vforecastlatest"]
    # Move a table by hand, rename its area: both survive a fresh suggestion.
    target = next(a for a in areas if SALES not in {m["key"] for m in a["members"]})
    erd.move_tables(catalogue, [SALES], target["uid"])
    erd.rename_area(catalogue, target["uid"], "Forecasting")
    again = erd.suggest_areas(catalogue, DB)
    mine = next(a for a in again if a["uid"] == target["uid"])
    assert mine["name"] == "Forecasting" and SALES in {m["key"] for m in mine["members"]}
    assert sum(len(a["members"]) for a in again) == 4


def test_erd_elements_and_mermaid(catalogue):
    add_relationship(catalogue, SALES, "StoreId", STORE, "StoreId")
    els = erd.erd_elements(catalogue, DB)
    edges = [e["data"] for e in els if "source" in e["data"]]
    assert any(e["source"] == SALES and e["target"] == STORE and e["label"] == "StoreId = StoreId" for e in edges)
    mmd = erd.to_mermaid(catalogue, DB)
    assert mmd.startswith("erDiagram") and "dbo_SalesDaily }o--|| dbo_Store" in mmd and "int StoreId FK" in mmd
