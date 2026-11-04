"""Recipes for machine learning projects that supply chains usually run, and what each one needs from the data.

A recipe names the columns a project needs as *concepts* ("receipt date", "planned lead time", "supplier"),
each with the SAP fields it usually is (BUDAT, PLIFZ, LIFNR) and the friendlier names it goes by in extracts
(ReceiptDate, PlannedLeadTime, VendorID). Matching uses both, so extracts that keep SAP names, extracts with
friendly names, and a mix of the two are all found, and the SAP original is always shown next to the friendly
name.

Concept fields: `role` (target, date, key or feature), `sap` (SAP field names), `names` (friendly names,
lower case letters and digits only: a column matches when its squashed name equals one, or contains one of six
letters or more).

Recipe fields: `name`, `predicts`, `tool` (a key of TOOLS), `method`, `why`, `concepts`, `grain` (the key
concepts a row is identified by), `sap_tables`, `table_words`, `measure_words`, `plan_actual` (for the
baseline to beat), `leak_words` (things only known after the event), and `kind` ("agent" for an AI agent,
"unsupervised" when there is no labelled target).

These are general supply chain and SAP knowledge, the same in every company. You can switch recipes off, change
their words, and add your own on Knowledge › ML opportunities › Recipes; your changes are kept in the
catalogue's settings and these defaults are never edited.
"""
from __future__ import annotations

import copy
from typing import Any

from .store import Catalogue

SETTING = "ml_recipes"           # your changes and your own recipes: {recipe id: recipe}
OFF_SETTING = "ml_recipes_off"   # recipe ids switched off

TOOLS: dict[str, tuple[str, str]] = {
    "": ("Not sure yet", ""),
    "forecast": ("Forecast model", "LightGBM global model"),
    "regression": ("Prediction: a number", "LightGBM regression"),
    "classification": ("Prediction: yes or no", "LightGBM classification"),
    "anomaly": ("Anomaly detection", "Isolation Forest or comparison with peers"),
    "scoring": ("Scoring", "Logistic regression or LightGBM"),
    "optimisation": ("Optimisation or simulation", "Service-level formulas, simulation or a solver"),
    "agent": ("AI agent", "Copilot or Claude agent over the catalogue and SQL views"),
    "rules": ("Rules or a report first", "SQL rules or a Power BI report"),
}


def _c(cid: str, label: str, role: str, sap: tuple[str, ...] = (), names: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"id": cid, "label": label, "role": role, "sap": list(sap), "names": list(names)}


MATERIAL = _c("material", "Material", "key", ("MATNR",), ("material", "materialid", "materialnumber", "materialno",
                                                          "sku", "productcode", "itemcode", "partnumber", "product",
                                                          "productid"))
PLANT = _c("plant", "Plant", "key", ("WERKS",), ("plant", "plantid", "plantcode", "site", "siteid", "location",
                                                 "locationid", "locid"))
SUPPLIER = _c("supplier", "Supplier", "key", ("LIFNR",), ("vendor", "vendorid", "supplier", "supplierid",
                                                          "vendornumber", "suppliernumber"))
CUSTOMER = _c("customer", "Customer", "key", ("KUNNR",), ("customer", "customerid", "customernumber", "soldto",
                                                          "shipto", "soldtoparty"))
SALES_DOC = _c("sales_doc", "Sales order", "key", ("VBELN", "POSNR"), ("salesorder", "salesdocument",
                                                                       "salesordernumber", "orderline", "ordernumber",
                                                                       "salesorderitem", "deliverynumber"))
PO = _c("po", "Purchase order", "key", ("EBELN", "EBELP"), ("purchaseorder", "ponumber", "purchaseordernumber",
                                                            "poitem", "poline", "purchaseorderitem"))
STOCK = _c("stock", "Stock on hand", "feature", ("LABST",), ("stockonhand", "soh", "unrestrictedstock", "onhand",
                                                             "onhandqty", "stockqty", "inventoryqty"))
DEMAND_QTY = _c("sales_qty", "Quantity sold or shipped", "target", ("KWMENG", "FKIMG", "LFIMG"),
                ("salesqty", "salesquantity", "orderqty", "orderquantity", "shippedqty", "invoicedqty", "units",
                 "unitssold", "demand", "demandqty", "billedqty", "deliveredqty"))

RECIPES: dict[str, dict[str, Any]] = {
    "demand_forecasting": {
        "name": "Demand forecasting", "tool": "forecast", "method": "LightGBM global model",
        "predicts": "Units per material, location and week.",
        "why": "Many related series with promotions and prices: one model learns across all of them.",
        "concepts": [DEMAND_QTY,
                     _c("sales_date", "Sales date or week", "date", ("BUDAT", "ERDAT", "EDATU", "FKDAT"),
                        ("salesdate", "invoicedate", "billingdate", "orderdate", "calweek", "calday", "weekstart",
                         "fiscalweek", "salesweek")),
                     MATERIAL, PLANT, CUSTOMER,
                     _c("price", "Price", "feature", ("NETWR", "NETPR"), ("netprice", "price", "unitprice",
                                                                         "netvalue", "listprice")),
                     _c("promo", "Promotion", "feature", (), ("promotion", "promo", "promoflag", "campaign",
                                                             "discount", "dealtype")),
                     _c("forecast_qty", "Current forecast", "feature", (), ("forecast", "forecastqty", "fcst",
                                                                            "fcstqty", "statisticalforecast",
                                                                            "consensusforecast"))],
        "grain": ["material", "plant"], "sap_tables": ["VBAP", "VBRP", "VBAK", "VBRK", "LIPS", "MSEG", "MATDOC"],
        "table_words": ["sales", "demand", "billing", "invoice", "shipment", "forecast", "history"],
        "measure_words": ["forecast accuracy", "forecast bias", "mape", "wape", "forecast", "demand"],
        "plan_actual": {"plan": "forecast_qty", "actual": "sales_qty", "unit": "units"},
        "leak_words": ["actual", "final", "invoiced"],
    },
    "safety_stock": {
        "name": "Safety stock optimisation", "tool": "optimisation",
        "method": "Service-level formulas or simulation, with ML for the inputs",
        "predicts": "The right safety stock per material and plant for a target service level.",
        "why": "The target itself is a calculation; ML helps estimate forecast error and lead time variability.",
        "concepts": [_c("safety_stock", "Safety stock", "target", ("EISBE",), ("safetystock", "sstock", "ss",
                                                                              "safetystockqty")),
                     _c("snapshot_date", "Snapshot date", "date", (), ("snapshotdate", "extractdate", "asofdate",
                                                                      "loaddate", "stockdate", "calday")),
                     MATERIAL, PLANT, STOCK,
                     _c("lead_time", "Lead time", "feature", ("PLIFZ",), ("plannedleadtime", "leadtime",
                                                                         "leadtimedays", "planneddeliverytime")),
                     _c("forecast_qty", "Forecast", "feature", (), ("forecast", "forecastqty", "fcst"))],
        "grain": ["material", "plant"], "sap_tables": ["MARC", "MARD"],
        "table_words": ["stock", "inventory", "safety", "materialplant", "onhand", "soh"],
        "measure_words": ["stock cover", "days cover", "service level", "fill rate", "stock out", "safety stock"],
        "leak_words": [],
    },
    "lead_time": {
        "name": "Lead time prediction", "tool": "regression", "method": "LightGBM regression",
        "predicts": "Actual supplier lead time per purchase order line, against the planned lead time.",
        "why": "Tabular data with numbers and codes; it predicts days, and feature importance shows which suppliers "
               "and lanes drive delays.",
        "concepts": [_c("receipt_date", "Goods receipt date", "target", ("BUDAT",),
                        ("receiptdate", "grdate", "goodsreceiptdate", "receiveddate", "actualdeliverydate",
                         "arrivaldate")),
                     _c("order_date", "Order date", "date", ("BEDAT", "AEDAT", "ERDAT"),
                        ("orderdate", "podate", "purchaseorderdate", "createddate", "documentdate")),
                     _c("planned_lt", "Planned lead time", "feature", ("PLIFZ",),
                        ("plannedleadtime", "planneddeliverytime", "leadtimedays", "leadtime", "plifz")),
                     _c("planned_date", "Planned delivery date", "feature", ("EINDT",),
                        ("planneddeliverydate", "scheduledate", "deliverydate", "duedate", "eindt")),
                     PO, SUPPLIER, MATERIAL, PLANT],
        "grain": ["po", "material"], "sap_tables": ["EKKO", "EKPO", "EKET", "EKBE", "MSEG", "MATDOC", "LFA1", "MARC"],
        "table_words": ["purchase", "receipt", "goodsreceipt", "inbound", "supplier", "vendor", "procurement"],
        "measure_words": ["lead time", "late", "on time", "days late", "delay", "receipt"],
        "plan_actual": {"plan": "planned_lt", "actual": "receipt_date", "start": "order_date", "unit": "days"},
        "leak_words": ["invoice", "invoiced", "received", "grqty", "receivedqty", "actual"],
    },
    "otif_risk": {
        "name": "Stock-out and OTIF risk", "tool": "classification", "method": "LightGBM classification",
        "predicts": "Which open order lines are likely to ship late or short.",
        "why": "Gives each open line a chance of being late, so planners can sort and act on the riskiest first.",
        "concepts": [_c("actual_gi", "Actual goods issue date", "target", ("WADAT_IST",),
                        ("actualgoodsissue", "goodsissuedate", "gidate", "actualshipdate", "shippeddate",
                         "actualgidate", "wadatist")),
                     _c("requested_date", "Requested date", "date", ("EDATU", "VDATU"),
                        ("requesteddate", "requestdate", "requesteddeliverydate", "wishdate", "customerrequestdate")),
                     _c("delivered_qty", "Delivered quantity", "feature", ("LFIMG",),
                        ("deliveredqty", "deliveryquantity", "shippedqty")),
                     _c("ordered_qty", "Ordered quantity", "feature", ("KWMENG",), ("orderqty", "orderquantity",
                                                                                  "orderedqty")),
                     SALES_DOC, MATERIAL, PLANT, CUSTOMER, STOCK],
        "grain": ["sales_doc"], "sap_tables": ["VBAK", "VBAP", "VBEP", "LIKP", "LIPS"],
        "table_words": ["delivery", "deliveries", "salesorder", "orderline", "shipment", "otif", "backorder"],
        "measure_words": ["otif", "on time", "in full", "fill rate", "late", "short", "backorder"],
        "plan_actual": {"plan": "requested_date", "actual": "actual_gi", "unit": "days late", "dates": True},
        "leak_words": ["actual", "shipped", "delivered", "goodsissue", "gi"],
    },
    "supplier_risk": {
        "name": "Supplier risk", "tool": "scoring", "method": "Logistic regression or LightGBM",
        "predicts": "The chance a supplier misses date, quantity or quality on the next order.",
        "why": "A simple, explainable score is easier to discuss with suppliers than a black box.",
        "concepts": [_c("receipt_date", "Goods receipt date", "target", ("BUDAT",),
                        ("receiptdate", "grdate", "goodsreceiptdate", "receiveddate")),
                     _c("quality", "Quality result", "target", (), ("rejectedqty", "inspectionresult", "defects",
                                                                   "qualityscore", "usagedecision", "blockedqty")),
                     SUPPLIER, PO, MATERIAL,
                     _c("planned_date", "Planned delivery date", "feature", ("EINDT",),
                        ("planneddeliverydate", "deliverydate", "duedate"))],
        "grain": ["supplier", "po"], "sap_tables": ["EKKO", "EKPO", "EKET", "MSEG", "MATDOC", "QALS", "LFA1"],
        "table_words": ["supplier", "vendor", "purchase", "receipt", "quality", "inspection", "scorecard"],
        "measure_words": ["supplier", "vendor", "scorecard", "rating", "late", "quality"],
        "leak_words": ["invoice", "actual"],
    },
    "master_data": {
        "name": "Master data anomalies", "tool": "anomaly", "method": "Isolation Forest or comparison with peers",
        "kind": "unsupervised",
        "predicts": "Planning and material settings that look wrong compared with similar materials.",
        "why": "There are no labelled errors, so the model flags settings that differ from similar materials for "
               "someone to check.",
        "concepts": [_c("mrp_type", "MRP type", "feature", ("DISMM",), ("mrptype",)),
                     _c("mrp_controller", "MRP controller", "feature", ("DISPO",), ("mrpcontroller",)),
                     _c("lot_size", "Lot size", "feature", ("BSTMI", "DISLS"), ("minimumlotsize", "minlotsize", "moq",
                                                                               "lotsize")),
                     _c("safety_stock", "Safety stock", "feature", ("EISBE",), ("safetystock",)),
                     _c("planned_lt", "Planned lead time", "feature", ("PLIFZ",), ("plannedleadtime",
                                                                                  "planneddeliverytime")),
                     _c("material_group", "Material group", "feature", ("MATKL", "MTART"), ("materialgroup",
                                                                                           "materialtype",
                                                                                           "productgroup")),
                     MATERIAL, PLANT],
        "grain": ["material", "plant"], "sap_tables": ["MARA", "MARC", "MARD", "MBEW", "EORD"],
        "table_words": ["material", "master", "materialplant", "planning", "mrp"],
        "measure_words": ["master data", "data quality", "changes", "errors"],
        "leak_words": [],
    },
    "slow_movers": {
        "name": "Slow mover and obsolescence", "tool": "classification",
        "method": "LightGBM classification, with simple rules first",
        "predicts": "Which stock will not move before it expires or becomes obsolete.",
        "why": "Clear rules (no movement in 12 months) catch most cases; a model adds early warning for the rest.",
        "concepts": [_c("scrap", "Scrap or write-off", "target", ("BWART",), ("movementtype", "scrap", "scrapqty",
                                                                             "writeoff", "writeoffvalue", "obsolete")),
                     _c("last_movement", "Last movement date", "date", (), ("lastmovement", "lastmovementdate",
                                                                           "lastissuedate", "lastgidate", "stockage",
                                                                           "agedays")),
                     _c("shelf_life", "Shelf life or expiry", "feature", ("MHDHB", "VFDAT"),
                        ("shelflife", "expiry", "expirydate", "bestbefore", "sled")),
                     _c("batch", "Batch", "key", ("CHARG",), ("batch", "batchnumber", "lot", "lotnumber")),
                     MATERIAL, PLANT, STOCK],
        "grain": ["material", "plant"], "sap_tables": ["MARD", "MCHB", "MCH1", "MSEG", "MATDOC", "MBEW"],
        "table_words": ["stock", "inventory", "movement", "ageing", "aging", "obsolete", "slow"],
        "measure_words": ["slow moving", "slow-moving", "obsolete", "write off", "write-off", "stock age", "excess"],
        "leak_words": ["writeoff", "scrapped"],
    },
    "order_promising": {
        "name": "Order promising", "tool": "regression", "method": "LightGBM regression",
        "predicts": "A realistic confirmed date per sales order line.",
        "why": "Learns how far actual shipping drifts from the date ATP confirmed, by material, plant and customer.",
        "concepts": [_c("actual_gi", "Actual goods issue date", "target", ("WADAT_IST",),
                        ("actualgoodsissue", "goodsissuedate", "gidate", "actualshipdate", "shippeddate")),
                     _c("confirmed_date", "Confirmed date", "date", ("EDATU",),
                        ("confirmeddate", "confirmationdate", "atpdate", "promisedate", "commitdate")),
                     SALES_DOC, MATERIAL, PLANT, CUSTOMER],
        "grain": ["sales_doc"], "sap_tables": ["VBAP", "VBEP", "LIKP", "LIPS"],
        "table_words": ["confirmation", "atp", "scheduleline", "promise", "salesorder"],
        "measure_words": ["promise", "confirmed", "on time", "atp"],
        "plan_actual": {"plan": "confirmed_date", "actual": "actual_gi", "unit": "days late", "dates": True},
        "leak_words": ["actual", "shipped"],
    },
    "warehouse": {
        "name": "Warehouse labour and slotting", "tool": "forecast",
        "method": "LightGBM for labour; an optimiser for slotting",
        "predicts": "Picks and hours per shift, and the best bin for each material.",
        "why": "Labour is a forecasting problem; bin slotting is an assignment problem.",
        "concepts": [_c("task", "Warehouse task", "target", ("TANUM",), ("warehousetask", "taskid", "picks",
                                                                         "pickqty", "pickedqty", "linespicked")),
                     _c("task_time", "Task confirmed at", "date", (), ("confirmedat", "confirmationtime",
                                                                      "confirmeddate", "taskdate", "pickdate")),
                     _c("bin", "Storage bin", "feature", ("LGPLA",), ("storagebin", "bin", "binlocation")),
                     _c("warehouse_no", "Warehouse", "key", ("LGNUM",), ("warehousenumber", "warehouse")),
                     _c("labour", "Labour hours", "feature", (), ("labourhours", "laborhours", "hoursworked",
                                                                 "headcount")),
                     _c("hu", "Handling unit", "feature", ("HUIDENT",), ("handlingunit", "huid")),
                     MATERIAL],
        "grain": ["warehouse_no"], "sap_tables": ["LTAK", "LTAP", "LAGP"],
        "table_words": ["ewm", "warehouse", "task", "pick", "bin", "labour", "labor", "putaway"],
        "measure_words": ["picks per hour", "productivity", "lines picked", "utilisation", "utilization"],
        "leak_words": ["confirmed"],
    },
    "returns": {
        "name": "Returns prediction", "tool": "classification", "method": "LightGBM classification",
        "predicts": "Which orders or materials are likely to come back.",
        "why": "A yes or no per order line, explained by customer, material and channel.",
        "concepts": [_c("return_flag", "Return", "target", ("AUGRU",), ("returnqty", "returnreason", "returnflag",
                                                                        "reasoncode", "creditmemo", "rma",
                                                                        "returnorder", "returned")),
                     _c("order_date", "Order date", "date", ("ERDAT", "AUDAT"), ("orderdate", "createddate",
                                                                                 "documentdate")),
                     SALES_DOC, CUSTOMER, MATERIAL],
        "grain": ["sales_doc"], "sap_tables": ["VBAK", "VBAP", "VBRK", "VBRP"],
        "table_words": ["return", "returns", "credit", "rma", "complaint"],
        "measure_words": ["return", "returns rate", "credit", "complaint"],
        "leak_words": ["credit", "refund"],
    },
    "price_promo": {
        "name": "Price and promotion effects", "tool": "regression", "method": "LightGBM regression",
        "predicts": "The uplift from promotions and price changes.",
        "why": "Separates baseline demand from what price and promotions add.",
        "concepts": [DEMAND_QTY,
                     _c("sales_date", "Sales date or week", "date", ("BUDAT", "FKDAT"),
                        ("salesdate", "invoicedate", "billingdate", "calweek", "weekstart")),
                     _c("price", "Price", "feature", ("NETPR", "KBETR"), ("netprice", "price", "unitprice",
                                                                         "listprice", "conditionvalue")),
                     _c("promo", "Promotion", "feature", (), ("promotion", "promo", "promoflag", "campaign",
                                                             "discount", "dealtype")),
                     MATERIAL, CUSTOMER],
        "grain": ["material"], "sap_tables": ["KONV", "PRCD_ELEMENTS", "A004", "VBRP"],
        "table_words": ["price", "pricing", "promo", "promotion", "campaign", "condition"],
        "measure_words": ["uplift", "promo", "promotion", "price", "elasticity"],
        "leak_words": ["actual"],
    },
    "freight": {
        "name": "Transport and freight cost", "tool": "regression", "method": "LightGBM regression",
        "predicts": "Cost or transit time per shipment.",
        "why": "Learns cost and time by lane, carrier, weight and season.",
        "concepts": [_c("freight_cost", "Freight cost", "target", (), ("freightcost", "freight", "shippingcost",
                                                                       "transportcost", "carriercost")),
                     _c("ship_date", "Ship date", "date", ("WADAT_IST",), ("shipdate", "shipmentdate",
                                                                          "departuredate", "pickupdate")),
                     _c("arrival", "Arrival date", "feature", (), ("arrivaldate", "deliverydate", "podate",
                                                                  "proofofdelivery")),
                     _c("carrier", "Carrier", "key", ("TDLNR",), ("carrier", "forwardingagent", "carrierid")),
                     _c("shipment", "Shipment", "key", ("TKNUM",), ("shipment", "shipmentnumber", "shipmentid")),
                     _c("weight", "Weight", "feature", ("BRGEW", "NTGEW"), ("weight", "grossweight", "netweight"))],
        "grain": ["shipment"], "sap_tables": ["VTTK", "VTTP", "VFKP", "VFKK"],
        "table_words": ["shipment", "freight", "transport", "carrier", "logistics", "route"],
        "measure_words": ["freight", "cost per", "transit", "transport"],
        "leak_words": ["invoice", "actualcost"],
    },
    "planner_agent": {
        "name": "Planner assistant (AI agent example)", "tool": "agent", "kind": "agent", "example": True,
        "method": "Copilot or Claude agent over the catalogue and SQL views",
        "predicts": "Answers planners' questions such as “why is this material short at this plant?” from "
                    "the catalogue and live tables.",
        "why": "Questions are in plain language and change every day; the value is finding and explaining data, not "
               "predicting a number.",
        "concepts": [MATERIAL, PLANT, STOCK,
                     _c("open_orders", "Open orders", "feature", ("KWMENG",), ("openqty", "openorderqty",
                                                                              "backorderqty", "orderqty"))],
        "grain": ["material", "plant"], "sap_tables": ["MARD", "MARC", "VBAP"],
        "table_words": ["stock", "order", "inventory", "supply"],
        "measure_words": ["stock cover", "shortage", "backorder", "service level"],
        "leak_words": [],
    },
}


def _with_defaults(rid: str, r: dict[str, Any]) -> dict[str, Any]:
    out = {"id": rid, "name": rid, "predicts": "", "tool": "", "method": "", "why": "", "concepts": [], "grain": [],
           "sap_tables": [], "table_words": [], "measure_words": [], "leak_words": [], "plan_actual": None,
           "kind": "", "example": False, "builtin": rid in RECIPES}
    out.update(copy.deepcopy(r))
    out["id"] = rid
    return out


def all_recipes(cat: Catalogue, *, include_off: bool = False) -> dict[str, dict[str, Any]]:
    """Every recipe (the built-in ones with your changes, then your own), each with `on`."""
    mine = cat.setting(SETTING, {}) or {}
    off = set(cat.setting(OFF_SETTING, []) or [])
    out = {}
    for rid, r in RECIPES.items():
        out[rid] = _with_defaults(rid, {**r, **mine.get(rid, {})})
    for rid, r in mine.items():
        if rid not in RECIPES:
            out[rid] = _with_defaults(rid, r)
    for rid, r in out.items():
        r["on"] = rid not in off
        r["changed"] = rid in mine
    return out if include_off else {k: v for k, v in out.items() if v["on"]}


def get_recipe(cat: Catalogue, rid: str | None) -> dict[str, Any] | None:
    if not rid:
        return None
    return all_recipes(cat, include_off=True).get(rid)


def save_recipe(cat: Catalogue, rid: str, recipe: dict[str, Any]) -> None:
    """Keep your version of a recipe (a built-in one you changed, or your own)."""
    mine = dict(cat.setting(SETTING, {}) or {})
    keep = {k: v for k, v in recipe.items() if k not in ("id", "on", "changed", "builtin")}
    mine[rid] = keep
    cat.set_setting(SETTING, mine)


def reset_recipe(cat: Catalogue, rid: str) -> None:
    """Back to the built-in recipe (your own recipe is deleted)."""
    mine = dict(cat.setting(SETTING, {}) or {})
    mine.pop(rid, None)
    cat.set_setting(SETTING, mine)


def set_on(cat: Catalogue, rid: str, value: bool) -> None:
    off = set(cat.setting(OFF_SETTING, []) or [])
    off.discard(rid) if value else off.add(rid)
    cat.set_setting(OFF_SETTING, sorted(off))


def new_recipe_id(cat: Catalogue, name: str) -> str:
    import re
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "recipe"
    have = all_recipes(cat, include_off=True)
    rid, n = f"my_{base}", 2
    while rid in have:
        rid, n = f"my_{base}_{n}", n + 1
    return rid


def concept(recipe: dict[str, Any], cid: str) -> dict[str, Any] | None:
    return next((c for c in recipe.get("concepts", []) if c.get("id") == cid), None)
