"""Search words: how typed words are matched against names, and the abbreviation list.

Names are matched by their parts as well as whole: "OfficeStockonHand" is indexed as "Office Stockon Hand" too,
and a typed word also matches inside a name ("stock" finds "Overstock" and "OfficeStockonHand"). Separators don't
matter: "stock on hand", "stock_on_hand" and "StockOnHand" are the same.

Abbreviations work both ways: "SOH" finds "StockonHand", and "stock on hand" finds "SOH". The list starts with
common supply chain terms and can be changed on the Abbreviations page (Knowledge menu); it's kept in the catalogue.
"""
from __future__ import annotations

import re

# Short form -> what it stands for. General supply chain and SAP planning terms, the same in every company.
STARTER: dict[str, str] = {
    "SOH": "Stock on hand",
    "ATP": "Available to promise",
    "CTP": "Capable to promise",
    "MRP": "Material requirements planning",
    "DRP": "Distribution requirements planning",
    "MPS": "Master production schedule",
    "SKU": "Stock keeping unit",
    "UOM": "Unit of measure",
    "MOQ": "Minimum order quantity",
    "EOQ": "Economic order quantity",
    "ROP": "Reorder point",
    "SS": "Safety stock",
    "LT": "Lead time",
    "DOS": "Days of supply",
    "DOH": "Days on hand",
    "WOS": "Weeks of supply",
    "OTIF": "On time in full",
    "DIFOT": "Delivered in full on time",
    "FG": "Finished goods",
    "RM": "Raw materials",
    "WIP": "Work in progress",
    "BOM": "Bill of materials",
    "PO": "Purchase order",
    "SO": "Sales order",
    "STO": "Stock transport order",
    "GR": "Goods receipt",
    "GI": "Goods issue",
    "ASN": "Advance shipping notice",
    "DC": "Distribution centre",
    "WH": "Warehouse",
    "WHS": "Warehouse",
    "INV": "Inventory",
    "QTY": "Quantity",
    "FCST": "Forecast",
    "DMD": "Demand",
    "MAPE": "Mean absolute percentage error",
    "WAPE": "Weighted absolute percentage error",
    "S&OP": "Sales and operations planning",
    "IBP": "Integrated business planning",
    "APO": "Advanced planning and optimisation",
    "EWM": "Extended warehouse management",
    "VMI": "Vendor managed inventory",
    "FIFO": "First in first out",
    "FEFO": "First expired first out",
    "COGS": "Cost of goods sold",
    "OOS": "Out of stock",
}

SETTING = "abbreviations"

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SEPARATORS = re.compile(r"[_.:/\\#-]+")


def split_name(text: str) -> str:
    """Parts of names, separated by spaces: 'dbo.vw_MaterialStockOnHand' -> 'dbo vw Material Stock On Hand'."""
    return _CAMEL.sub(" ", _SEPARATORS.sub(" ", text or ""))


def squash(text: str | None) -> str:
    """Lower case with every space and separator taken out, so 'Stock on_hand' and 'StockOnHand' compare equal."""
    return re.sub(r"[\W_]+", "", (text or "").lower())


def words(text: str | None) -> list[str]:
    """The words someone typed, lower case. '&' stays inside a word, so 'S&OP' is one word."""
    return [w.lower() for w in re.findall(r"\w+(?:&\w+)*", text or "")]


def parse(text: str) -> dict[str, str]:
    """Lines like 'SOH = Stock on hand' (or 'SOH: Stock on hand', or tab separated) into a list."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        m = re.match(r"\s*([^=:\t]+?)\s*[=:\t]\s*(.+?)\s*$", line)
        if m and squash(m.group(1)) and squash(m.group(2)):
            out[m.group(1)] = m.group(2)
    return out


def as_text(abbrevs: dict[str, str]) -> str:
    return "\n".join(f"{k} = {v}" for k, v in abbrevs.items())


def alternatives(token: str, abbrevs: dict[str, str]) -> list[str]:
    """A typed word and what it can also mean, lower case: 'soh' -> ['soh', 'stock on hand'], and back."""
    t = squash(token)
    out = [token.lower()]
    for short, long in abbrevs.items():
        s, lg = squash(short), squash(long)
        if t == s:
            out.append(long.lower())
        elif t == lg:
            out.append(short.lower())
    seen: set[str] = set()
    return [o for o in out if squash(o) and not (squash(o) in seen or seen.add(squash(o)))]


def phrase_matches(text: str, abbrevs: dict[str, str]) -> list[str]:
    """Abbreviations for a whole typed phrase: 'stock on hand' -> ['soh']."""
    q = squash(text)
    if not q or len(words(text)) < 2:
        return []
    return [short.lower() for short, lg in abbrevs.items() if squash(lg) == q]


def fts_term(alt: str) -> str:
    """One alternative as an FTS5 prefix query: 'stock on hand' -> '"stock on hand"*'."""
    return '"' + " ".join(re.findall(r"\w+", alt)) + '"*'
