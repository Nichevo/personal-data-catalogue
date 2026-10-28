"""Link data to SAP: what a table or column is in SAP terms ("PlantID is SAP WERKS", "this table is an
extract of MARC").

Matches are suggested from column names: SAP field names themselves (WERKS, MATNR) or the usual
English names for them (Plant, Material). Tables are matched when their name contains an SAP table
name (stg_MARC, MARC_extract). You confirm each one. Data and scripts that still use APO (its
tables, characteristics or planning terms) are flagged with the IBP equivalent.

The field and table lists here are general SAP knowledge, the same in every company; they're also
the basis of the SAP starter pack in Knowledge.
"""
from __future__ import annotations

import re
from typing import Any

from . import suggestions
from .store import Catalogue

# SAP field -> (meaning, English names it often goes by in extracts and warehouses)
SAP_FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "MATNR": ("Material number", ("material", "materialid", "materialnumber", "material_no", "materialno", "sku",
                                  "productcode", "itemcode", "partnumber")),
    "WERKS": ("Plant", ("plant", "plantid", "plantcode", "plant_code", "site", "siteid")),
    "LGORT": ("Storage location", ("storagelocation", "storage_location", "sloc", "storagelocationid")),
    "MEINS": ("Base unit of measure", ("baseunit", "base_uom", "baseuom", "uom", "unitofmeasure")),
    "MTART": ("Material type", ("materialtype", "material_type")),
    "MATKL": ("Material group", ("materialgroup", "material_group", "productgroup")),
    "MAKTX": ("Material description", ("materialdescription", "material_description", "materialname")),
    "BUKRS": ("Company code", ("companycode", "company_code")),
    "VKORG": ("Sales organisation", ("salesorg", "salesorganisation", "salesorganization", "sales_org")),
    "VTWEG": ("Distribution channel", ("distributionchannel", "distribution_channel", "distchannel")),
    "SPART": ("Division", ("division",)),
    "EKORG": ("Purchasing organisation", ("purchasingorg", "purchasingorganisation", "purchasingorganization")),
    "KUNNR": ("Customer number", ("customer", "customerid", "customernumber", "customer_no", "soldto", "soldtoparty")),
    "LIFNR": ("Vendor (supplier) number", ("vendor", "vendorid", "supplier", "supplierid", "vendornumber")),
    "VBELN": ("Sales, delivery or billing document number", ("salesorder", "salesdocument", "salesordernumber",
                                                             "deliverynumber", "billingdocument")),
    "POSNR": ("Document item number", ("itemnumber", "lineitem", "orderline", "salesorderitem")),
    "EBELN": ("Purchasing document number", ("purchaseorder", "ponumber", "purchaseordernumber", "po_number")),
    "EBELP": ("Purchasing document item", ("poitem", "purchaseorderitem", "poline")),
    "BWART": ("Movement type", ("movementtype", "movement_type")),
    "CHARG": ("Batch number", ("batch", "batchnumber", "lot", "lotnumber")),
    "MENGE": ("Quantity", ()),
    "KWMENG": ("Order quantity (sales)", ("orderquantity", "order_qty", "orderqty")),
    "LFIMG": ("Delivery quantity", ("deliveryquantity", "delivered_qty", "deliveredqty")),
    "NETWR": ("Net value", ("netvalue", "net_value")),
    "WAERS": ("Currency", ("currency", "currencycode")),
    "ERDAT": ("Created on (date)", ("createdon", "created_date", "createddate")),
    "BUDAT": ("Posting date", ("postingdate", "posting_date")),
    "EDATU": ("Schedule line date", ("scheduledate", "scheduleline_date")),
    "WADAT_IST": ("Actual goods issue date", ("goodsissuedate", "actualgoodsissue", "gidate")),
    "PRCTR": ("Profit centre", ("profitcenter", "profitcentre", "profit_center")),
    "KOSTL": ("Cost centre", ("costcenter", "costcentre", "cost_center")),
    "DISPO": ("MRP controller", ("mrpcontroller", "mrp_controller")),
    "DISMM": ("MRP type", ("mrptype", "mrp_type")),
    "PLIFZ": ("Planned delivery time (days)", ("planneddeliverytime", "plannedleadtime", "leadtimedays")),
    "EISBE": ("Safety stock", ("safetystock", "safety_stock")),
    "BSTMI": ("Minimum lot size", ("minimumlotsize", "minlotsize", "moq")),
    "LABST": ("Unrestricted-use stock", ("unrestrictedstock", "unrestricted_stock", "stockonhand", "soh")),
    "LGNUM": ("Warehouse number (EWM/WM)", ("warehousenumber", "warehouse_number")),
    "LGPLA": ("Storage bin (EWM/WM)", ("storagebin", "storage_bin", "bin")),
    "HUIDENT": ("Handling unit (EWM)", ("handlingunit", "handling_unit", "huid")),
}

# SAP table -> what it holds
SAP_TABLES: dict[str, str] = {
    "MARA": "Material master: general data (type, group, base unit)",
    "MARC": "Material master: plant data (MRP settings, lead times, safety stock)",
    "MARD": "Material master: storage location data and stock",
    "MAKT": "Material descriptions",
    "MBEW": "Material valuation",
    "T001W": "Plants",
    "T001L": "Storage locations",
    "KNA1": "Customer master (general)",
    "LFA1": "Vendor (supplier) master (general)",
    "VBAK": "Sales document header",
    "VBAP": "Sales document item",
    "VBEP": "Sales document schedule lines",
    "LIKP": "Delivery header",
    "LIPS": "Delivery item",
    "VBRK": "Billing document header",
    "VBRP": "Billing document item",
    "EKKO": "Purchasing document header",
    "EKPO": "Purchasing document item",
    "EKET": "Purchasing schedule lines",
    "MKPF": "Material document header (before S/4HANA)",
    "MSEG": "Material document item (stock movements, before S/4HANA)",
    "MATDOC": "Material documents (stock movements, S/4HANA)",
    "EKBE": "Purchasing document history",
    "RESB": "Reservations and dependent requirements",
    "PLAF": "Planned orders",
    "AFKO": "Production order header",
    "AFPO": "Production order item",
}

# APO marker -> (what it is, IBP equivalent)
APO_MARKERS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"/sapapo/", re.I), "an APO table", "IBP master data and planning objects"),
    (re.compile(r"\b9amatnr\b", re.I), "APO product characteristic (9AMATNR)", "IBP product ID (PRDID)"),
    (re.compile(r"\b9alocno\b", re.I), "APO location characteristic (9ALOCNO)", "IBP location ID (LOCID)"),
    (re.compile(r"\bplanning[ _]?book", re.I), "an APO planning book", "IBP planning views"),
    (re.compile(r"(?:^|[^a-z0-9])(?:apo[ _-]?dp|dp[ _]planning[ _]area)(?:$|[^a-z0-9])", re.I), "APO Demand Planning", "IBP for demand"),
    (re.compile(r"(?:^|[^a-z0-9])(?:apo[ _-]?snp|snp[ _](?:plan|run|heuristic))", re.I), "APO Supply Network Planning",
     "IBP for supply (and response)"),
    (re.compile(r"\bcvcs?\b|characteristic[ _]value[ _]combination", re.I), "APO characteristic value combinations",
     "IBP planning objects (master data combinations)"),
    (re.compile(r"(?:^|[_\W])apo(?:$|[_\W])", re.I), "APO", "IBP"),
]

_ENGLISH = {re.sub(r"[^a-z0-9]", "", n): field for field, (_, names) in SAP_FIELDS.items() for n in names}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def match_column(name: str) -> tuple[str, str] | None:
    """(SAP field, how) for a column name, or None."""
    bare = name.strip("[]")
    up = bare.upper()
    if up in SAP_FIELDS:
        return up, "the SAP field name"
    for field in SAP_FIELDS:  # MARC_WERKS, WERKS_ID, werks_code
        if re.search(rf"(?:^|_){field}(?:$|_)", up):
            return field, "contains the SAP field name"
    n = _norm(bare)
    if n in _ENGLISH:
        return _ENGLISH[n], "a usual English name for it"
    return None


def match_table(name: str) -> str | None:
    up = name.split(".")[-1].strip("[]").upper()
    for t in sorted(SAP_TABLES, key=len, reverse=True):
        if re.search(rf"(?:^|[_\W]){re.escape(t)}(?:$|[_\W])", up):
            return t
    return None


def suggest_sap(cat: Catalogue) -> int:
    """Suggest SAP meanings for columns and tables that don't have one yet."""
    have_cols = {(r[0], r[1].lower()) for r in cat.conn.execute(
        "SELECT asset_key, column_name FROM column_info WHERE sap_name IS NOT NULL AND sap_name != ''")}
    have_items = {r[0] for r in cat.conn.execute(
        "SELECT asset_key FROM item_info WHERE sap_name IS NOT NULL AND sap_name != ''")}
    from .curation import hidden_keys
    hidden = hidden_keys(cat, old=True)
    found = []
    names = {r[0]: r[1] for r in cat.conn.execute(
        "SELECT key, name FROM assets WHERE status = 'active' AND kind IN ('table', 'view', 'data_file', "
        "'file_series')")}
    for r in cat.conn.execute("SELECT asset_key, name FROM columns"):
        key, col = r[0], r[1]
        if key not in names or key in hidden or (key, col.lower()) in have_cols:
            continue
        m = match_column(col)
        if not m:
            continue
        field, how = m
        found.append({"id": suggestions.make_id("sap", f"{key}#{col}", field), "subject": key,
                      "title": f"{names[key]}.{col} is SAP {field} ({SAP_FIELDS[field][0]})",
                      "detail": f"Suggested because the column name is {how}.",
                      "score": 2 if how.startswith("the SAP") else 1,
                      "payload": {"key": key, "column": col, "sap_name": field}})
    for key, name in names.items():
        if key in hidden or key in have_items:
            continue
        t = match_table(name)
        if t:
            found.append({"id": suggestions.make_id("sap", key, t), "subject": key,
                          "title": f"{name} is an extract of SAP {t} ({SAP_TABLES[t]})",
                          "detail": "Suggested because its name contains the SAP table name.", "score": 2,
                          "payload": {"key": key, "sap_name": t}})
    return suggestions.sync(cat, "sap", found)


@suggestions.handler("sap")
def _accept(cat: Catalogue, p: dict[str, Any]) -> None:
    from .curation import set_column_extra, set_item_info
    if p.get("column"):
        set_column_extra(cat, p["key"], p["column"], "sap_name", p["sap_name"])
    else:
        set_item_info(cat, p["key"], sap_name=p["sap_name"])


def meaning(sap_name: str | None) -> str:
    if not sap_name:
        return ""
    up = sap_name.upper()
    return SAP_FIELDS.get(up, (SAP_TABLES.get(up, ""),))[0]


def apo_flags(cat: Catalogue, key: str) -> list[tuple[str, str]]:
    """(what APO thing it uses, the IBP equivalent) for an item whose name, columns, SQL or code mention APO."""
    a = cat.get_asset(key)
    if a is None:
        return []
    texts = [a["name"], " ".join(c["name"] for c in cat.columns(key))]
    attrs = a["attrs"]
    texts += [attrs.get("definition") or "", attrs.get("sql") or ""]
    texts += [q.get("sql", "") for q in attrs.get("sql_strings") or []]
    texts += [r for r in (row[0] for row in cat.conn.execute(
        "SELECT dst_ref FROM links WHERE src_key = ?", (key,)))]
    text = "\n".join(t for t in texts if t)
    out = []
    for rx, what, ibp in APO_MARKERS:
        if rx.search(text) and not any(o[0] == what for o in out):
            out.append((what, ibp))
            if what == "APO":
                break
    if len(out) > 1:
        out = [o for o in out if o[0] != "APO"]
    return out


def sap_items(cat: Catalogue) -> list[dict[str, Any]]:
    """Every table and column linked to SAP, for the SAP links page."""
    out = []
    for r in cat.conn.execute("SELECT i.asset_key, i.sap_name, a.name FROM item_info i JOIN assets a ON "
                              "a.key = i.asset_key WHERE i.sap_name IS NOT NULL AND i.sap_name != ''"):
        out.append({"item": r["name"], "key": r["asset_key"], "column": "", "SAP": r["sap_name"],
                    "meaning": meaning(r["sap_name"])})
    for r in cat.conn.execute("SELECT c.asset_key, c.column_name, c.sap_name, a.name FROM column_info c JOIN assets a "
                              "ON a.key = c.asset_key WHERE c.sap_name IS NOT NULL AND c.sap_name != ''"):
        out.append({"item": r["name"], "key": r["asset_key"], "column": r["column_name"], "SAP": r["sap_name"],
                    "meaning": meaning(r["sap_name"])})
    return sorted(out, key=lambda x: (x["item"].lower(), x["column"].lower()))


def apo_items(cat: Catalogue, limit: int = 500) -> list[dict[str, Any]]:
    """Items that still use APO, with the IBP equivalent of each thing they use."""
    keys = set()
    for r in cat.conn.execute(
            "SELECT key FROM assets WHERE status = 'active' AND kind IN ('table', 'view', 'procedure', 'data_file', "
            "'script', 'notebook', 'sql_script', 'report', 'workbook') AND (lower(name) LIKE '%apo%' OR "
            "lower(attrs) LIKE '%sapapo%' OR lower(attrs) LIKE '%9amatnr%' OR lower(attrs) LIKE '%9alocno%' OR "
            "lower(attrs) LIKE '%planning book%' OR lower(attrs) LIKE '%planningbook%')"):
        keys.add(r[0])
    for r in cat.conn.execute("SELECT DISTINCT asset_key FROM columns WHERE lower(name) IN ('9amatnr', '9alocno')"):
        keys.add(r[0])
    out = []
    for key in sorted(keys)[:limit]:
        flags = apo_flags(cat, key)
        if flags:
            a = cat.get_asset(key)
            out.append({"key": key, "item": a["name"], "kind": a["kind"],
                        "uses": "; ".join(f for f, _ in flags), "IBP equivalent": "; ".join(i for _, i in flags)})
    return out


def column_sap_names(cat: Catalogue, key: str) -> dict[str, str]:
    return {r[0]: r[1] for r in cat.conn.execute(
        "SELECT column_name, sap_name FROM column_info WHERE asset_key = ? AND sap_name IS NOT NULL AND sap_name != ''",
        (key,))}
