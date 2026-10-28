"""Starter knowledge packs: optional general knowledge to load into Knowledge and the Metric glossary.

Everything in a pack is general (the same in any company), tagged "general" so it stays apart from
what you know about your own data, and editable like anything else. Loading a pack again adds what's
new and refreshes what you haven't touched; anything you edited is left as you left it.

Packs: Supply chain, SAP, SQL Server good practice, APO to IBP.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .store import Catalogue, now

TAG = "general"

# ---- Supply chain ---------------------------------------------------------------------------

SUPPLY_CHAIN_METRICS = [
    ("mape", "MAPE", "Mean absolute percentage error: the average of each period's (or item's) error as a share of "
     "its actual.", "mean(|actual - forecast| / actual)",
     "Undefined when an actual is 0 and dominated by small actuals; WAPE is usually better for intermittent items."),
    ("wape", "WAPE", "Weighted absolute percentage error: total absolute error as a share of total actuals, so big "
     "items count for more.", "sum(|actual - forecast|) / sum(actual)",
     "Also called MAD/mean ratio. Say what level it's measured at (item-location-week vs total), as it changes a lot."),
    ("forecast-accuracy", "Forecast accuracy", "How close the forecast was to what happened, usually 1 - WAPE.",
     "1 - sum(|actual - forecast|) / sum(actual)",
     "Can go below zero when errors exceed actuals; some teams floor it at 0. State the lag (forecast made how many "
     "periods before) and the level."),
    ("bias", "Forecast bias", "Whether the forecast is consistently too high or too low.",
     "sum(forecast - actual) / sum(actual)",
     "Positive means over-forecasting. Bias near zero with high WAPE means errors cancel out, not that the forecast is good."),
    ("tracking-signal", "Tracking signal", "Running sum of forecast errors divided by the mean absolute deviation; "
     "flags a forecast drifting one way.", "sum(actual - forecast) / MAD",
     "Commonly flagged outside about ±4."),
    ("fva", "Forecast value add (FVA)", "How much a step in the forecasting process (a model, a planner's "
     "override) improves accuracy compared with a simpler forecast.", "accuracy(step) - accuracy(baseline)",
     "The usual baseline is a naive forecast (last period's actual)."),
    ("service-level", "Service level (cycle)", "The chance of not running out of stock during a replenishment "
     "cycle.", "cycles without a stockout / all cycles",
     "Not the same as fill rate: a 95% cycle service level can still have a lower fill rate."),
    ("fill-rate", "Fill rate", "The share of demand met straight from stock.", "quantity shipped on time from stock / "
     "quantity ordered", "Say whether it's by line, by order or by unit; the three give different numbers."),
    ("otif", "OTIF (on time in full)", "The share of orders (or lines) delivered on the agreed date and in the full "
     "quantity.", "orders on time and in full / all orders",
     "Define 'on time' (requested or confirmed date, window allowed) and the level (order or line)."),
    ("inventory-turns", "Inventory turns", "How many times stock is used up and replaced in a year.",
     "cost of goods sold (year) / average inventory value", "Use the same valuation (cost) on both sides."),
    ("days-on-hand", "Days on hand (days of supply)", "How many days current stock would last at the expected rate "
     "of demand.", "stock on hand / average daily demand",
     "Backward-looking (past usage) and forward-looking (forecast) versions differ; say which."),
    ("safety-stock", "Safety stock", "Extra stock held to cover demand and supply variation during lead time.",
     "z × sqrt(LT × σd² + d² × σLT²)",
     "z comes from the target cycle service level (1.65 for 95%). With steady lead times it reduces to z × σd × sqrt(LT)."),
    ("lead-time", "Lead time", "Time from placing an order (or deciding to produce) to the stock being available.",
     "goods receipt date - order date", "Planned lead time in master data often differs from the actual; compare both."),
    ("stockout-rate", "Stockout rate", "Share of item-location-days (or weeks) with no stock available.",
     "periods with zero available stock / all periods", ""),
]

SUPPLY_CHAIN_NOTES = [
    ("demand-vs-supply", "Demand planning vs supply planning", "Definitions",
     "**Demand planning** forecasts what customers will want (by product, location, customer and time), using "
     "history, promotions, events and market input. **Supply planning** works out how to meet that demand: "
     "production, purchasing and distribution plans within capacity, lead times and stock policies. **S&OP** "
     "(sales and operations planning) is the monthly process that brings the two together and agrees one plan "
     "with finance.", "demand planning, supply planning, s&op"),
    ("sop", "S&OP (sales and operations planning)", "Definitions",
     "A monthly cycle, usually: data gathering, demand review (agree the unconstrained forecast), supply review "
     "(check it against capacity and materials), pre-S&OP (resolve gaps and options) and the executive meeting "
     "(decide). IBP (integrated business planning) is the same idea extended to financial plans and strategy.",
     "s&op, sales and operations planning, ibp"),
    ("abc-xyz", "ABC/XYZ classification", "Definitions",
     "**ABC** ranks items by value (often A = the items making up the first 80% of value, B the next 15%, C the "
     "rest). **XYZ** ranks them by how steady demand is (coefficient of variation: X steady, Y variable, Z erratic "
     "or intermittent). Combined (AX, CZ...) they guide forecasting methods and stock policies: AX items suit "
     "statistical forecasts and tight stock; CZ items may suit make-to-order or simple rules.",
     "abc, xyz, classification, segmentation"),
    ("moq", "MOQ and lot sizes", "Definitions",
     "**MOQ** (minimum order quantity) is the smallest quantity a supplier or plant will take. Lot sizing rules "
     "(fixed lot, lot-for-lot, multiples of a rounding value, economic order quantity) decide how much is ordered "
     "each time. Large MOQs push average stock up, which shows in days on hand.", "moq, lot size, rounding value"),
    ("intermittent-demand", "Intermittent demand", "Definitions",
     "Demand with many zero periods (spare parts, slow movers). Percentage errors break down (division by zero), "
     "so use WAPE or scaled errors (MASE). Croston-type methods forecast the size and the gap between demands "
     "separately.", "intermittent, croston, slow movers"),
    ("forecast-lag", "Forecast lag", "Definitions",
     "Accuracy depends on how far ahead the forecast was made. Lag 1 = the forecast made one period before; lag 3 "
     "= three periods before. Always store and report the lag, and compare like with like.", "lag, forecast lag"),
    ("forecast-levels", "Forecast levels and aggregation", "Definitions",
     "Forecasts are made and measured at a level (product × location × week, product family × month...). Errors "
     "shrink as you aggregate, so accuracy at total level is always higher. Say the level with every accuracy "
     "number.", "level, aggregation, hierarchy"),
    ("stock-types", "Stock types", "Definitions",
     "On hand (physically there), unrestricted (free to use), quality inspection, blocked, in transit, allocated or "
     "reserved, and available (on hand minus allocated). Planning normally uses available stock; finance values "
     "all of it.", "on hand, available stock, in transit, blocked stock"),
    ("service-vs-fill", "Service level, fill rate and OTIF: not the same thing", "Business rules",
     "Cycle service level counts cycles without a stockout; fill rate counts the quantity met from stock; OTIF "
     "counts orders delivered on time and complete. They answer different questions and can't be compared "
     "directly. Always name which one a target uses.", "service level, fill rate, otif"),
]

# ---- SQL Server good practice -------------------------------------------------------------
# Each article is keyed by the SQL check it explains (pdcat.sqlchecks.RULES), so a check links here.

SQLSERVER_NOTES = [
    ("joins-and-nulls", "Joins and NULLs", ["left_join_where", "null_compare", "not_in_subquery", "join_no_condition",
                                            "comma_join", "non_unique_join", "distinct_join"],
     "* `NULL` is unknown, so `= NULL` and `<> NULL` are never true. Use `IS NULL` / `IS NOT NULL`.\n"
     "* `NOT IN (subquery)` returns nothing at all if the subquery returns a NULL. Prefer `NOT EXISTS`.\n"
     "* A condition on the right-hand table of a `LEFT JOIN` in `WHERE` removes the unmatched rows (it becomes an "
     "inner join). Put it in the `ON` clause to keep them, or test `IS NULL` deliberately.\n"
     "* A join with no condition, or `FROM a, b` with a missing condition in `WHERE`, multiplies rows. Write "
     "explicit `JOIN ... ON`, and `CROSS JOIN` when you really mean every combination.\n"
     "* Join on columns that identify one row on at least one side (a key). If neither side is unique, rows "
     "multiply and totals are inflated. `DISTINCT` hides this rather than fixing it."),
    ("sargable-filters", "Sargable filters and indexes", ["function_on_column", "leading_wildcard", "type_mismatch"],
     "A filter is *sargable* when SQL Server can use an index to seek to the rows. Wrapping the column in a "
     "function (`YEAR(SaleDate) = 2026`, `CONVERT(date, x) = ...`, `ISNULL(col, 0) = 0`) prevents that. Rewrite as "
     "a range on the bare column: `SaleDate >= '2026-01-01' AND SaleDate < '2027-01-01'`.\n\n"
     "`LIKE 'abc%'` can seek; `LIKE '%abc'` can't. Comparing columns of different types forces a conversion on one "
     "side (an *implicit conversion*), which can also stop index use and make values fail to match."),
    ("dates", "Date handling", ["between_datetime", "hard_coded_date"],
     "* For date-time columns use half-open ranges: `>= start AND < day after end`. `BETWEEN '2026-01-01' AND "
     "'2026-01-31'` misses everything after midnight on the 31st.\n"
     "* Write literal dates as `'YYYYMMDD'` or `'YYYY-MM-DD'` (ISO); other formats depend on language settings.\n"
     "* Avoid hard-coded dates in saved queries; work them out from `GETDATE()` / `SYSDATETIME()` or pass them as "
     "parameters, so the query doesn't go stale.\n"
     "* `date`, `datetime2` and `datetimeoffset` are preferred over `datetime` for new columns."),
    ("isolation", "NOLOCK and isolation levels", ["nolock"],
     "`WITH (NOLOCK)` (the same as `READ UNCOMMITTED`) reads without waiting for locks, so it can return rows "
     "twice, miss rows, or read changes that are later rolled back. It's fine for a rough look at a busy table, not "
     "for numbers that must be exact. If readers and writers block each other, ask the DBA about *read committed "
     "snapshot isolation* (RCSI), which gives consistent reads without blocking."),
    ("temp-tables", "Temp tables, table variables and CTEs", [],
     "* **CTE** (`WITH x AS (...)`): a named subquery for one statement; not stored, may be evaluated more than once.\n"
     "* **Temp table** (`#t`): stored in tempdb, has statistics and can be indexed; best for larger intermediate "
     "results used several times.\n"
     "* **Table variable** (`@t`): no statistics (estimates are poor for many rows); fine for small sets.\n"
     "Break very large queries into steps with temp tables when the optimiser struggles."),
    ("window-functions", "Window functions", [],
     "`ROW_NUMBER()`, `RANK()`, `LAG()`, `LEAD()` and aggregates with `OVER (PARTITION BY ... ORDER BY ...)` compute "
     "per-group values without collapsing rows: latest row per key (`ROW_NUMBER() ... = 1`), running totals "
     "(`SUM(x) OVER (ORDER BY d ROWS UNBOUNDED PRECEDING)`), period-on-period changes (`LAG`). They usually replace "
     "self-joins and correlated subqueries and are faster."),
    ("set-based", "Set-based work instead of cursors", ["cursor"],
     "Cursors and `WHILE` loops process one row at a time. One `UPDATE`, `INSERT ... SELECT` or `MERGE` over the "
     "whole set is usually far faster. When a loop is unavoidable, loop over batches (say 10,000 rows) rather than "
     "single rows."),
    ("dynamic-sql", "Safe dynamic SQL and SQL injection", ["dynamic_sql", "built_from_variables"],
     "Building SQL text by pasting values in (`f\"... WHERE Region = '{region}'\"`, `'...' + @x`) lets a value change "
     "the query itself: SQL injection. Pass values as parameters instead: in Python, `cursor.execute(sql, params)` "
     "or `pd.read_sql(sql, con, params=...)`; in T-SQL, `sp_executesql @sql, N'@region nvarchar(50)', @region`. "
     "Object names that must be dynamic should be checked against a list and wrapped with `QUOTENAME()`."),
    ("select-star", "SELECT * and naming columns", ["select_star"],
     "`SELECT *` reads every column (more I/O, can stop covering indexes being used) and changes shape when the "
     "table changes, which breaks code that depends on column positions. List the columns you need. "
     "`EXISTS (SELECT * ...)` is fine: nothing is returned."),
    ("schema-names", "Always use schema names", ["missing_schema", "linked_server"],
     "Refer to tables as `schema.table` (`dbo.Sales`). Without the schema, SQL Server checks the user's default "
     "schema first, which can pick another table and prevents plan reuse. Four-part names (`server.db.schema.table`) "
     "go through a linked server: they depend on the link, and filters may not be sent to the remote side, so whole "
     "tables can come across the network."),
    ("union", "UNION vs UNION ALL", ["union_not_all"],
     "`UNION` removes duplicate rows, which needs a sort or hash over everything. `UNION ALL` keeps every row and is "
     "much cheaper. Use `UNION` only when duplicates are possible and must go."),
    ("division", "Dividing safely", ["divide_no_guard"],
     "`a / b` fails with a divide-by-zero error when `b` is 0. Use `a / NULLIF(b, 0)` (gives NULL) and decide how to "
     "show it. Integer division truncates (`1 / 2 = 0`): multiply by `1.0` or cast to decimal first."),
    ("top-order", "TOP needs ORDER BY", ["top_without_order"],
     "Without `ORDER BY`, the rows `TOP n` returns are whichever the engine reads first and can change between "
     "runs. Add `ORDER BY` (with a tie-breaker) whenever the choice of rows matters."),
    ("changing-data", "Changing data safely", ["no_where_change", "drop_or_truncate", "missing_object",
                                               "deprecated_object"],
     "* Run an `UPDATE` or `DELETE` as a `SELECT` with the same `WHERE` first, and check the row count.\n"
     "* Wrap changes in a transaction (`BEGIN TRAN ... COMMIT`) so they can be rolled back.\n"
     "* `TRUNCATE` and `DROP` on permanent tables remove data for everyone; they belong in deployment scripts, "
     "not routine queries.\n"
     "* Queries that use dropped, renamed or deprecated tables should be updated to the replacement."),
    ("naming-formatting", "Naming and formatting", [],
     "Common conventions (follow your own database's rules first): one case style for objects and columns "
     "(PascalCase or snake_case), prefixes only where agreed (`vw_` views, `usp_` procedures), singular or plural "
     "table names consistently, keys named `<Table>ID`, date columns ending in `Date`, no spaces or reserved words. "
     "Format queries with one clause per line, aliases that mean something (`s` for Sales, not `a`, `b`, `c`), and "
     "keywords in upper case."),
]

# ---- SAP --------------------------------------------------------------------------------------

SAP_NOTES = [
    ("sap-core-tables", "SAP: common tables", None),   # body built from sapterms.SAP_TABLES
    ("sap-core-fields", "SAP: common fields", None),   # body built from sapterms.SAP_FIELDS
    ("sap-material-master", "SAP material master views", "Definitions",
     "Material master data is split by organisational level: **MARA** general data for the material everywhere; "
     "**MARC** plant-level data (MRP type `DISMM`, MRP controller `DISPO`, planned delivery time `PLIFZ`, safety "
     "stock `EISBE`, lot size settings); **MARD** storage-location data and stock (`LABST` unrestricted stock); "
     "**MBEW** valuation. Descriptions are in **MAKT** by language. Extracts that join MARA and MARC are per "
     "material and plant."),
    ("sap-order-to-cash", "SAP order to cash documents", "Definitions",
     "Sales order (**VBAK** header, **VBAP** items, **VBEP** schedule lines with confirmed dates and quantities) → "
     "delivery (**LIKP**/**LIPS**, with the goods issue date) → billing document (**VBRK**/**VBRP**). The document "
     "number field is `VBELN` in all three, item `POSNR`. The document flow table links them. Order quantity is "
     "`KWMENG`, delivered quantity `LFIMG`."),
    ("sap-procure-to-pay", "SAP procure to pay documents", "Definitions",
     "Purchase order (**EKKO** header, **EKPO** items, **EKET** schedule lines) → goods receipt (a material "
     "document) → invoice. `EBELN` is the PO number, `EBELP` the item; **EKBE** holds the PO history (receipts and "
     "invoices against each item)."),
    ("sap-stock-movements", "SAP stock movements (MSEG and MATDOC)", "Definitions",
     "Every goods movement creates a material document. Before S/4HANA it was **MKPF** (header) + **MSEG** (items); "
     "in S/4HANA it's **MATDOC**, with compatibility views for the old names. The movement type `BWART` says what "
     "happened (101 goods receipt for a PO, 261 goods issue to production, 601 goods issue for delivery, 311 "
     "transfer...). Stock at a date is rebuilt by summing movements."),
    ("sap-ewm", "SAP EWM basics", "Definitions",
     "Extended Warehouse Management runs the inside of a warehouse: warehouse number `LGNUM`, storage types and "
     "**storage bins** (`LGPLA`), **warehouse tasks** (move stock from one bin to another) grouped into warehouse "
     "orders, and **handling units** (`HUIDENT`, a pallet or box with its contents). Stock in EWM is by bin and "
     "handling unit; ERP sees it by plant and storage location."),
    ("sap-apo", "SAP APO basics", "Definitions",
     "Advanced Planner and Optimizer (APO, part of SCM) handled planning before IBP. **DP** (Demand Planning) "
     "forecast in **planning areas** viewed through **planning books** and data views, at the level of "
     "**characteristic value combinations** (CVCs) such as product × location × customer; values were **key "
     "figures**, often with `9A` InfoObjects (`9AMATNR` product, `9ALOCNO` location). **SNP** (Supply Network "
     "Planning) planned supply across the network with heuristics or the optimiser. Master data sat in "
     "`/SAPAPO/` tables (location products, transportation lanes)."),
    ("sap-ibp", "SAP IBP basics", "Definitions",
     "Integrated Business Planning is SAP's cloud planning suite. Data is organised by **planning areas** with "
     "**attributes** (`PRDID` product, `LOCID` location, `CUSTID` customer...), **master data types** and "
     "**planning levels** (which attributes and time bucket a key figure is stored at). **Key figures** hold the "
     "numbers (stored, or calculated from others). Planners work in **planning views**, mostly in the Excel "
     "add-in, and **planning operators** run forecasting, supply heuristics and copies."),
]

# ---- APO to IBP ---------------------------------------------------------------------------------

APO_IBP_NOTES = [
    ("apo-ibp-modules", "APO DP and SNP become IBP for demand and for supply",
     "**APO DP → IBP for demand** (statistical forecasting, demand sensing). **APO SNP → IBP for supply** (supply "
     "heuristics and optimiser) and **IBP for response and supply** for order-based planning. S&OP moves into IBP "
     "for sales and operations.\n\n*Check when rebuilding:* which IBP application now owns the number your old "
     "report used."),
    ("apo-ibp-books-views", "Planning books and data views become planning views",
     "APO planning books / data views → IBP **planning views**, mostly in the Excel add-in (and Fiori apps). "
     "Layouts are saved as templates and favourites.\n\n*Check:* old extracts taken from a planning book "
     "usually map to a planning view template; agree which one with the planners."),
    ("apo-ibp-cvc", "Characteristics and CVCs become attributes and planning objects",
     "APO characteristics (`9AMATNR`, `9ALOCNO`, customer...) → IBP **attributes** (`PRDID`, `LOCID`, `CUSTID`). "
     "Characteristic value combinations → **planning objects** created from master data combinations.\n\n"
     "*Check:* custom characteristics need a matching attribute; product and location codes may be formatted "
     "differently (leading zeros)."),
    ("apo-ibp-location-product", "Location products become product-location master data",
     "APO location product (`/SAPAPO/MATLOC`, with its lot sizes, safety stock settings and lead times) → IBP "
     "**location product** master data type.\n\n*Check:* which settings are now maintained in IBP versus still "
     "taken from ERP (MARC)."),
    ("apo-ibp-key-figures", "Key figures and macros become key figure calculations and planning operators",
     "APO key figures → IBP key figures (stored or calculated). APO **macros** → **key figure calculations** "
     "(for derived values) and **planning operators** or application jobs (for steps that run).\n\n*Check:* a "
     "number your report recalculated from APO key figures may already exist as a calculated key figure in IBP; "
     "compare the definitions, not just the name."),
    ("apo-ibp-forecasting", "Forecasting differences",
     "APO DP used forecast profiles (univariate, MLR, composite) per selection. IBP uses **forecast models** with "
     "pre-processing (outlier correction), several algorithms (exponential smoothing, Croston, gradient boosting) "
     "and post-processing, run by planning operators.\n\n*Check:* accuracy measures before and after the move "
     "differ partly because methods changed; compare at the same lag and level."),
    ("apo-ibp-time", "Time buckets and planning levels",
     "APO stored data in time buckets profiles (often weeks and months) per planning area. IBP stores each key "
     "figure at a **planning level** that includes the time level (day, week, month), and aggregates or "
     "disaggregates between levels by rules.\n\n*Check:* week definitions (start day, split weeks at month "
     "end) and disaggregation rules when comparing numbers."),
    ("apo-ibp-lifecycle", "Lifecycle planning and realignment",
     "APO handled product launches and phase-outs with like-modelling and phase-in/out profiles, and moved history "
     "between CVCs with realignment. IBP has **product lifecycle** settings (reference products, phase-in/out) and "
     "**realignment** of key figure data between planning objects.\n\n*Check:* history moved by realignment changes "
     "past numbers; a report built on old APO extracts won't match after realignment."),
]


# ---- loading --------------------------------------------------------------------------------------

def _sap_tables_body() -> str:
    from .sapterms import SAP_TABLES
    return "| Table | What it holds |\n|---|---|\n" + "\n".join(f"| `{t}` | {d} |" for t, d in SAP_TABLES.items())


def _sap_fields_body() -> str:
    from .sapterms import SAP_FIELDS
    return "| Field | Meaning | Often called |\n|---|---|---|\n" + "\n".join(
        f"| `{f}` | {m} | {', '.join(n[:3])} |" for f, (m, n) in SAP_FIELDS.items())


def contents(pack: str) -> dict[str, list[dict[str, Any]]]:
    """{"knowledge": [...], "metrics": [...]} with stable ids, for one pack."""
    knowledge, metrics = [], []
    if pack == "supply_chain":
        for slug, name, definition, formula, caveats in SUPPLY_CHAIN_METRICS:
            metrics.append({"uid": f"{TAG}-sc-{slug}", "name": name, "definition": definition, "formula": formula,
                            "caveats": caveats, "tags": f"{TAG}, supply chain"})
        for slug, title, section, body, triggers in SUPPLY_CHAIN_NOTES:
            knowledge.append({"uid": f"{TAG}-sc-{slug}", "title": title, "section": section, "body": body,
                              "triggers": triggers, "tags": f"{TAG}, supply chain"})
    elif pack == "sap":
        for slug, title, section, *body in SAP_NOTES:
            text = body[0] if body else None
            if slug == "sap-core-tables":
                text, section = _sap_tables_body(), "Definitions"
            elif slug == "sap-core-fields":
                text, section = _sap_fields_body(), "Definitions"
            knowledge.append({"uid": f"{TAG}-{slug}", "title": title, "section": section, "body": text,
                              "triggers": "", "tags": f"{TAG}, sap"})
    elif pack == "sqlserver":
        for slug, title, rules, body in SQLSERVER_NOTES:
            knowledge.append({"uid": f"{TAG}-sql-{slug}", "title": f"SQL Server: {title}", "section": "How-to",
                              "body": body, "triggers": "", "tags": f"{TAG}, sql server", "rules": rules})
    elif pack == "apo_ibp":
        for slug, title, body in APO_IBP_NOTES:
            knowledge.append({"uid": f"{TAG}-{slug}", "title": f"APO to IBP: {title}", "section": "History",
                              "body": body, "triggers": "", "tags": f"{TAG}, sap, apo, ibp"})
    else:
        raise KeyError(pack)
    return {"knowledge": knowledge, "metrics": metrics}


PACKS = {
    "supply_chain": ("Supply chain", "Forecast accuracy (MAPE, WAPE, bias), safety stock, lead time, service level, "
                     "fill rate, OTIF, inventory turns, days on hand, MOQ, ABC/XYZ, S&OP, demand vs supply planning."),
    "sap": ("SAP", "Common tables and fields (MARA, MARC, VBAK, LIKP, EKKO, MATDOC; MATNR, WERKS, LGORT...), "
            "plus APO, EWM and IBP basics."),
    "sqlserver": ("SQL Server good practice", "Joins and NULLs, sargable filters, dates, NOLOCK, temp tables vs CTEs, "
                  "window functions, set-based work, safe dynamic SQL, naming and formatting. SQL checks link here."),
    "apo_ibp": ("APO to IBP", "What changed in the move from APO to IBP, side by side, with what to check when an old "
                "report, extract or metric is rebuilt on IBP data."),
}


def _hash(*parts: Any) -> str:
    return hashlib.sha1(json.dumps(parts, default=str).encode()).hexdigest()[:16]


def load(cat: Catalogue, pack: str) -> dict[str, int]:
    """Load (or refresh) a pack. Returns {"added", "updated", "kept"}: kept = items you edited, left alone."""
    c = contents(pack)
    loaded = cat.setting("packs_loaded", {})
    counts = {"added": 0, "updated": 0, "kept": 0}
    ts = now()
    with cat.conn:
        for k in c["knowledge"]:
            row = cat.conn.execute("SELECT title, body FROM knowledge WHERE uid = ?", (k["uid"],)).fetchone()
            new_hash = _hash(k["title"], k["body"])
            if row is None:
                cat.conn.execute(
                    "INSERT INTO knowledge (uid, title, body, section, tags, triggers, pinned, excluded, author, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, '[]', '[]', ?, ?, ?)",
                    (k["uid"], k["title"], k["body"], k["section"], k["tags"], k["triggers"], "starter pack", ts, ts))
                counts["added"] += 1
            elif _hash(row["title"], row["body"]) == loaded.get(k["uid"]):
                if loaded.get(k["uid"]) != new_hash:
                    cat.conn.execute("UPDATE knowledge SET title = ?, body = ?, updated_at = ? WHERE uid = ?",
                                     (k["title"], k["body"], ts, k["uid"]))
                    counts["updated"] += 1
            else:
                counts["kept"] += 1
                continue
            loaded[k["uid"]] = new_hash
        for m in c["metrics"]:
            row = cat.conn.execute("SELECT name, definition, formula FROM metrics WHERE uid = ?", (m["uid"],)).fetchone()
            new_hash = _hash(m["name"], m["definition"], m["formula"])
            if row is None:
                cat.conn.execute(
                    "INSERT INTO metrics (uid, name, definition, formula, sources, caveats, tags, author, created_at, "
                    "updated_at) VALUES (?, ?, ?, ?, '[]', ?, ?, ?, ?, ?)",
                    (m["uid"], m["name"], m["definition"], m["formula"], m["caveats"], m["tags"], "starter pack", ts, ts))
                counts["added"] += 1
            elif _hash(row["name"], row["definition"], row["formula"]) == loaded.get(m["uid"]):
                if loaded.get(m["uid"]) != new_hash:
                    cat.conn.execute("UPDATE metrics SET name = ?, definition = ?, formula = ?, caveats = ?, "
                                     "updated_at = ? WHERE uid = ?",
                                     (m["name"], m["definition"], m["formula"], m["caveats"], ts, m["uid"]))
                    counts["updated"] += 1
            else:
                counts["kept"] += 1
                continue
            loaded[m["uid"]] = new_hash
    cat.set_setting("packs_loaded", loaded)
    cat.rebuild_search()
    return counts


def status(cat: Catalogue) -> dict[str, int]:
    """pack -> how many of its items are in the catalogue."""
    have = {r[0] for r in cat.conn.execute("SELECT uid FROM knowledge WHERE uid LIKE 'general-%' "
                                           "UNION SELECT uid FROM metrics WHERE uid LIKE 'general-%'")}
    out = {}
    for pack in PACKS:
        c = contents(pack)
        out[pack] = sum(1 for x in c["knowledge"] + c["metrics"] if x["uid"] in have)
    return out


def article_for_rule(cat: Catalogue | None, rule: str) -> str | None:
    """The uid of the SQL Server pack article that explains a check, when the pack is loaded."""
    for slug, _, rules, _ in SQLSERVER_NOTES:
        if rule in rules:
            uid = f"{TAG}-sql-{slug}"
            if cat is None or cat.conn.execute("SELECT 1 FROM knowledge WHERE uid = ?", (uid,)).fetchone():
                return uid
    return None
