"""Descriptions from comments: drafts from what the code already says, offered as suggestions.

  * a view's or procedure's header comment (`-- Daily sales by store` or `/* ... */` above CREATE)
    as its purpose;
  * comments on a view's columns (`Units AS qty,  -- units sold, net of returns`) as the column's
    description;
  * a script's docstring or a notebook's first markdown cell as its purpose;
  * the comment next to a column rename (`"Units": "qty",  # units sold`) as the new column's
    description, wherever that column ends up.

Only items and columns with no description of their own get a suggestion. No AI is involved.
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import suggestions
from .store import Catalogue

MAX_TEXT = 500


def header_comment(sql: str) -> str | None:
    """The comment at the top of a SQL definition, before CREATE / ALTER."""
    text = (sql or "").lstrip()
    m = re.match(r"/\*(.*?)\*/", text, re.S)
    if m:
        body = m.group(1)
    else:
        lines = []
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("--"):
                lines.append(s.lstrip("-").strip())
            elif s:
                break
        body = "\n".join(lines)
    body = re.sub(r"^\s*\*+\s?", "", body, flags=re.M)
    # skip boilerplate like "Author: x" / "Date: y" / "=====" lines
    keep = [ln.strip() for ln in body.splitlines()
            if ln.strip() and not re.match(r"(?i)^(author|created|date|modified|change|history|version|=+|-+)\b",
                                           ln.strip())]
    text = " ".join(keep).strip()
    return text[:MAX_TEXT] or None


def column_comments(sql: str) -> dict[str, str]:
    """Column name (or alias) -> the comment at the end of its line in a SELECT list."""
    out = {}
    for line in (sql or "").splitlines():
        m = re.match(r"^\s*,?\s*(?P<expr>[^-]+?)\s*,?\s*--\s*(?P<c>.+)$", line)
        if not m:
            continue
        expr = m.group("expr").rstrip(", ")
        expr = re.sub(r"(?i)^.*?\bselect\s+(?:distinct\s+)?(?:top\s+\d+\s+)?", "", expr)
        alias = re.search(r"(?i)\bas\s+\[?(\w+)\]?\s*$", expr) or re.search(r"(?:\.|^)\[?(\w+)\]?\s*$", expr)
        if alias and not re.match(r"(?i)^(select|from|where|join|on|and|or|group|order)$", alias.group(1)):
            out[alias.group(1)] = m.group("c").strip()[:MAX_TEXT]
    return out


def find_descriptions(cat: Catalogue) -> int:
    from .curation import column_info, item_info
    found = []

    def has_item_text(key: str) -> bool:
        return bool((item_info(cat, key) or {}).get("purpose"))

    rows = cat.conn.execute("SELECT key, kind, name, description, attrs FROM assets WHERE status = 'active' AND kind "
                            "IN ('view', 'procedure', 'script', 'notebook')").fetchall()
    for r in rows:
        a = json.loads(r["attrs"])
        key, kind = r["key"], r["kind"]
        if kind in ("view", "procedure") and a.get("definition"):
            head = header_comment(a["definition"])
            if head and not has_item_text(key):
                found.append(_item(key, r["name"], head, "its header comment"))
            if kind == "view":
                have = {c.lower(): c for c in (x["name"] for x in cat.columns(key))}
                mine = column_info(cat, key)
                described = {c for c, v in mine.items() if v.get("description")}
                described |= {x["name"] for x in cat.columns(key) if x["description"]}
                for col, text in column_comments(a["definition"]).items():
                    real = have.get(col.lower())
                    if real and real not in described:
                        found.append(_column(key, r["name"], real, text, "the comment on its line in the view"))
        elif kind in ("script", "notebook") and r["description"] and not has_item_text(key):
            what = "its docstring" if kind == "script" else "its first markdown cell"
            found.append(_item(key, r["name"], r["description"][:MAX_TEXT], what))
    # comments next to renames, carried to where the renamed column ends up
    comments: dict[tuple[str, str], tuple[str, str]] = {}
    for r in rows:
        if r["kind"] not in ("script", "notebook"):
            continue
        for rn in json.loads(r["attrs"]).get("column_renames") or []:
            if rn.get("comment"):
                comments[(r["key"], rn["new"].lower())] = (rn["comment"][:MAX_TEXT], r["name"])
    if comments:
        for link in cat.conn.execute("SELECT * FROM column_links WHERE how = 'rename'"):
            script = (link["evidence"] or "").split(" (", 1)[0]
            hit = comments.get((script, link["dst_column"].lower()))
            if not hit or link["dst_item"] == script:
                continue
            mine = column_info(cat, link["dst_item"]).get(link["dst_column"], {})
            if mine.get("description"):
                continue
            dst = cat.get_asset(link["dst_item"]) or {}
            found.append(_column(link["dst_item"], dst.get("name", link["dst_item"]), link["dst_column"], hit[0],
                                 f"the comment next to the rename in {hit[1]}"))
    return suggestions.sync(cat, "description", found)


def _item(key: str, name: str, text: str, where: str) -> dict[str, Any]:
    return {"id": suggestions.make_id("description", key), "subject": key,
            "title": f"{name}: “{text[:120]}{'…' if len(text) > 120 else ''}”",
            "detail": f"Use {where} as its purpose.", "payload": {"key": key, "text": text}}


def _column(key: str, name: str, column: str, text: str, where: str) -> dict[str, Any]:
    return {"id": suggestions.make_id("description", f"{key}#{column}"), "subject": key,
            "title": f"{name}.{column}: “{text[:120]}{'…' if len(text) > 120 else ''}”",
            "detail": f"Use {where} as the column's description.",
            "payload": {"key": key, "column": column, "text": text}}


@suggestions.handler("description")
def _accept(cat: Catalogue, p: dict[str, Any]) -> None:
    from .curation import set_column_info, set_item_info
    if p.get("column"):
        set_column_info(cat, p["key"], p["column"], description=p["text"])
    else:
        set_item_info(cat, p["key"], purpose=p["text"])
