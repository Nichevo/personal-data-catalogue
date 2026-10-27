"""What you know about items, kept apart from what the scanners find.

Purpose, grain, refresh, status, trust, owner and sensitivity for any item (a
table, file, script, report, business project or package), plus your own
description of each column. Rescans never touch any of it.

Sensitive columns: their values are never stored or shown. Marking a column
sensitive deletes the values already measured (min, max, common values, range
charts), later profiles skip them, and every export leaves them out.
"""
from __future__ import annotations

import re
from typing import Any

from .store import Catalogue, now

INFO_FIELDS = ("purpose", "grain", "refresh", "lifecycle", "replaced_by", "trust", "owner", "sensitive",
               "freshness_column", "copy_of")
REFRESH = ["", "daily", "weekly", "monthly", "quarterly", "ad hoc", "one-off"]
LIFECYCLE = ["", "in use", "experimental", "deprecated", "retired"]
TRUST = ["", "trusted", "caution", "do not use"]

SENSITIVE_NAME = re.compile(
    r"(e-?mail|phone|mobile|telephone|address|post_?code|zip_?code|\bdob\b|birth|salary|wage|\bpay\b|ssn|"
    r"national_?insurance|\bnin?o?\b|passport|iban|sort_?code|account_?(no|num)|card_?(no|num)|password|passwd|"
    r"tax_?id|first_?name|last_?name|surname|full_?name|forename|employee_?name|customer_?name|ip_?address)", re.I)


def item_info(cat: Catalogue, key: str) -> dict[str, Any]:
    row = cat.conn.execute("SELECT * FROM item_info WHERE asset_key = ?", (key,)).fetchone()
    return dict(row) if row else {}


def all_item_info(cat: Catalogue) -> dict[str, dict[str, Any]]:
    return {r["asset_key"]: dict(r) for r in cat.conn.execute("SELECT * FROM item_info")}


def set_item_info(cat: Catalogue, key: str, **fields: Any) -> None:
    """Update some of an item's fields; the others are kept. Empty strings clear a field."""
    bad = set(fields) - set(INFO_FIELDS)
    if bad:
        raise ValueError(f"unknown field(s): {', '.join(sorted(bad))}")
    current = item_info(cat, key)
    merged = {f: current.get(f) for f in INFO_FIELDS}
    for f, v in fields.items():
        merged[f] = (v.strip() or None) if isinstance(v, str) else v
    merged["sensitive"] = int(bool(merged.get("sensitive")))
    with cat.conn:
        cat.conn.execute(
            f"INSERT INTO item_info (asset_key, {', '.join(INFO_FIELDS)}, updated_at, author) "
            f"VALUES (?, {', '.join('?' * len(INFO_FIELDS))}, ?, ?) "
            f"ON CONFLICT(asset_key) DO UPDATE SET {', '.join(f'{f} = excluded.{f}' for f in INFO_FIELDS)}, "
            "updated_at = excluded.updated_at, author = excluded.author",
            (key, *(merged[f] for f in INFO_FIELDS), now(), cat.author()))
    if merged["sensitive"] and not current.get("sensitive"):
        purge_values(cat, key)
    cat.reindex_search(key)


def column_info(cat: Catalogue, key: str) -> dict[str, dict[str, Any]]:
    return {r["column_name"]: dict(r) for r in cat.conn.execute(
        "SELECT * FROM column_info WHERE asset_key = ?", (key,))}


def set_column_info(cat: Catalogue, key: str, column: str, *, description: str | None = None,
                    sensitive: bool | None = None, rebuild: bool = True) -> None:
    old = cat.conn.execute("SELECT * FROM column_info WHERE asset_key = ? AND column_name = ?",
                           (key, column)).fetchone()
    desc = (description.strip() or None) if description is not None else (old["description"] if old else None)
    sens = int(sensitive) if sensitive is not None else (old["sensitive"] if old else 0)
    with cat.conn:
        cat.conn.execute(
            "INSERT INTO column_info (asset_key, column_name, description, sensitive, updated_at, author) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(asset_key, column_name) DO UPDATE SET "
            "description = excluded.description, sensitive = excluded.sensitive, "
            "updated_at = excluded.updated_at, author = excluded.author",
            (key, column, desc, sens, now(), cat.author()))
    if sens and not (old and old["sensitive"]):
        purge_values(cat, key, column)
    if rebuild:
        cat.reindex_search(key)


def column_description(cat: Catalogue, key: str, column: str) -> str:
    """Your description if you wrote one, else the one from the database."""
    row = cat.conn.execute(
        "SELECT COALESCE(ci.description, c.description) FROM columns c LEFT JOIN column_info ci "
        "ON ci.asset_key = c.asset_key AND ci.column_name = c.name WHERE c.asset_key = ? AND c.name = ?",
        (key, column)).fetchone()
    return (row[0] if row else "") or ""


# ---- sensitive values ------------------------------------------------------------

def sensitive_columns(cat: Catalogue) -> set[tuple[str, str]]:
    """(asset key, lower-case column name) for every sensitive column; ("key", "*") for whole items."""
    out = {(r[0], r[1].lower()) for r in cat.conn.execute(
        "SELECT asset_key, column_name FROM column_info WHERE sensitive = 1")}
    out |= {(r[0], "*") for r in cat.conn.execute("SELECT asset_key FROM item_info WHERE sensitive = 1")}
    return out


def is_sensitive(sensitive: set[tuple[str, str]], key: str, column: str | None = None) -> bool:
    if (key, "*") in sensitive:
        return True
    return column is not None and (key, column.lower()) in sensitive


def purge_values(cat: Catalogue, key: str, column: str | None = None) -> None:
    """Delete stored values (min, max, common values, charts) for a column, or a whole item."""
    where, params = "asset_key = ?", [key]
    if column is not None:
        where += " AND lower(column_name) = lower(?)"
        params.append(column)
    with cat.conn:
        cat.conn.execute(f"UPDATE profiles SET min_value = NULL, max_value = NULL, mean_value = NULL, "
                         f"top_values = '[]', histogram = NULL, percentiles = NULL WHERE {where}", params)


def sensitive_suggestions(cat: Catalogue) -> list[dict[str, Any]]:
    """Columns whose names suggest personal or confidential data and that aren't marked yet."""
    marked = sensitive_columns(cat)
    dismissed = set(map(tuple, cat.setting("sensitive_dismissed", [])))
    out = []
    for r in cat.conn.execute("SELECT c.asset_key, c.name, a.name AS item, a.kind FROM columns c "
                              "JOIN assets a ON a.key = c.asset_key AND a.status = 'active' ORDER BY a.name, c.ordinal"):
        if SENSITIVE_NAME.search(r["name"]) and not is_sensitive(marked, r["asset_key"], r["name"]) \
                and (r["asset_key"], r["name"]) not in dismissed:
            out.append(dict(r))
    return out


def dismiss_sensitive_suggestion(cat: Catalogue, key: str, column: str) -> None:
    dismissed = cat.setting("sensitive_dismissed", [])
    if [key, column] not in dismissed:
        cat.set_setting("sensitive_dismissed", [*dismissed, [key, column]])


# ---- describe once, apply everywhere -----------------------------------------------

def same_named_columns(cat: Catalogue, column: str) -> list[dict[str, Any]]:
    """Every column with this name (any case) in any table or file, with its current description."""
    return [dict(r) for r in cat.conn.execute(
        "SELECT c.asset_key, a.name AS item, a.kind, c.name, c.data_type, "
        "COALESCE(ci.description, c.description, '') AS description FROM columns c "
        "JOIN assets a ON a.key = c.asset_key AND a.status = 'active' "
        "LEFT JOIN column_info ci ON ci.asset_key = c.asset_key AND ci.column_name = c.name "
        "WHERE lower(c.name) = lower(?) ORDER BY a.name", (column,))]


def describe_everywhere(cat: Catalogue, column: str, description: str, *, only_empty: bool = True,
                        keys: list[str] | None = None) -> int:
    """Give every column with this name the same description. Returns how many were changed."""
    n = 0
    for c in same_named_columns(cat, column):
        if keys is not None and c["asset_key"] not in keys:
            continue
        if only_empty and c["description"]:
            continue
        set_column_info(cat, c["asset_key"], c["name"], description=description, rebuild=False)
        n += 1
    cat.rebuild_search()
    return n


def undescribed_column_names(cat: Catalogue, limit: int = 200) -> list[dict[str, Any]]:
    """Column names that appear in several places and have no description somewhere, most common first."""
    return [dict(r) for r in cat.conn.execute(
        """SELECT lower(c.name) AS column_name, MIN(c.name) AS example, COUNT(*) AS places,
                  SUM(CASE WHEN COALESCE(ci.description, c.description, '') = '' THEN 1 ELSE 0 END) AS undescribed,
                  MAX(COALESCE(ci.description, c.description, '')) AS a_description
           FROM columns c JOIN assets a ON a.key = c.asset_key AND a.status = 'active'
           LEFT JOIN column_info ci ON ci.asset_key = c.asset_key AND ci.column_name = c.name
           GROUP BY lower(c.name) HAVING undescribed > 0
           ORDER BY places DESC, column_name LIMIT ?""", (limit,))]
