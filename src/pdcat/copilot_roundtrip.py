"""The Copilot round trip: a "gaps pack" of items with no description, for your Copilot agent to draft them,
and its answers read back in as suggestions you accept or edit. No AI service is connected to the
catalogue: you carry the files there and back.

The gaps pack holds, for each item with no description, its name, kind, columns and a little of its
code or SQL, plus naming rules the app couldn't read as checks. It asks for answers in a fixed CSV
layout (id, column, answer), which is what `read_answers` takes back. Answers to rules are re-read
as naming rules; everything else becomes a description suggestion.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from . import suggestions
from .store import Catalogue

KINDS = ("table", "view", "procedure", "data_file", "file_series", "script", "notebook", "sql_script", "report",
         "workbook")
MAX_ITEMS = 300
MAX_COLUMNS = 60
CODE_LINES = 60
ANSWER_HEADER = ["id", "column", "answer"]


def gaps(cat: Catalogue, limit: int = MAX_ITEMS) -> list[dict[str, Any]]:
    """Items with no description and no purpose, most used first, with the columns that have none either."""
    used = dict(cat.conn.execute("SELECT dst_key, COUNT(DISTINCT src_key) FROM links WHERE dst_key IS NOT NULL "
                                 "GROUP BY dst_key").fetchall())
    hidden = {r[0] for r in cat.conn.execute("SELECT asset_key FROM item_info WHERE sensitive = 1 OR "
                                             "relevance = 'not relevant'")}
    has_purpose = {r[0] for r in cat.conn.execute("SELECT asset_key FROM item_info WHERE coalesce(purpose, '') != ''")}
    col_done = {(r[0], r[1].lower()) for r in cat.conn.execute(
        "SELECT asset_key, column_name FROM column_info WHERE coalesce(description, '') != '' OR sensitive = 1")}
    rows = [dict(r) for r in cat.conn.execute(
        f"SELECT key, kind, name, description, attrs FROM assets WHERE status = 'active' AND kind IN "
        f"({', '.join('?' * len(KINDS))})", KINDS)]
    out = []
    for r in sorted(rows, key=lambda r: (-used.get(r["key"], 0), r["name"])):
        if r["key"] in hidden:
            continue
        cols = [dict(c) for c in cat.columns(r["key"])]
        blank_cols = [c for c in cols if not c["description"] and (r["key"], c["name"].lower()) not in col_done]
        needs_item = not r["description"] and r["key"] not in has_purpose
        if not needs_item and not blank_cols:
            continue
        attrs = json.loads(r["attrs"] or "{}")
        out.append({"key": r["key"], "kind": r["kind"], "name": r["name"], "needs_item": needs_item,
                    "columns": [(c["name"], c["data_type"] or "") for c in blank_cols[:MAX_COLUMNS]],
                    "all_columns": [c["name"] for c in cols][:MAX_COLUMNS], "code": _code(attrs),
                    "used_by": used.get(r["key"], 0)})
        if len(out) >= limit:
            break
    return out


def _code(attrs: dict[str, Any]) -> str:
    if attrs.get("definition"):
        return "\n".join(attrs["definition"].splitlines()[:CODE_LINES])
    path = attrs.get("full_path")
    if path:
        try:
            p = Path(path)
            if p.is_file() and p.stat().st_size < 2_000_000 and p.suffix.lower() in (".py", ".sql", ".r"):
                return "\n".join(p.read_text(encoding="utf-8", errors="replace").splitlines()[:CODE_LINES])
        except OSError:
            return ""
    strings = attrs.get("sql_strings") or []
    if strings:
        first = strings[0]
        return "\n".join((first.get("sql") if isinstance(first, dict) else str(first)).splitlines()[:CODE_LINES])
    return ""


def text_rules(cat: Catalogue) -> list[dict[str, Any]]:
    return [dict(r) for r in cat.conn.execute(
        "SELECT uid, scope, text FROM naming_rules WHERE state = 'active' AND check_type = 'text' ORDER BY scope")]


def gaps_markdown(cat: Catalogue, *, limit: int = MAX_ITEMS) -> str:
    items = gaps(cat, limit)
    rules = text_rules(cat)
    out = ["# Gaps to fill", "",
           "Items in the team's data catalogue that have no description yet. For each one, write a short, plain "
           "description from its name, columns and code, using the knowledge you already have (business rules, "
           "glossary, example questions). Only describe what you're fairly sure of; leave the rest out.", "",
           "## How to answer", "",
           "Reply with a CSV with exactly these columns: `id,column,answer`.", "",
           "- `id` is the id shown for the item (copy it exactly).",
           "- `column` is blank for the item itself, or the column name for a column.",
           "- `answer` is the description: one or two sentences.",
           "- For a naming rule, `id` is the rule id, `column` is blank, and `answer` is the rule rewritten as "
           "simple statements such as “Views start with vw_” or “Columns are PascalCase”.", "",
           "Example:", "", "```csv", "id,column,answer",
           "sql://server/db/dbo.sales,,Daily sales by store and product.",
           "sql://server/db/dbo.sales,Units,Units sold, net of returns.", "```", ""]
    for g in items:
        out += [f"## {g['name']}", "", f"id: `{g['key']}`  ", f"kind: {g['kind'].replace('_', ' ')}"
                + (f" · used by {g['used_by']} item(s)" if g["used_by"] else ""), ""]
        if g["needs_item"]:
            out.append("Needs: a description of the item.")
        if g["columns"]:
            out.append("Columns needing a description: " + ", ".join(f"{n} ({t})" if t else n for n, t in g["columns"]))
        if g["all_columns"] and len(g["all_columns"]) > len(g["columns"]):
            out.append("All columns: " + ", ".join(g["all_columns"]))
        if g["code"]:
            lang = "sql" if g["kind"] in ("view", "procedure", "sql_script") else "python"
            out += ["", f"```{lang}", g["code"], "```"]
        out.append("")
    if rules:
        out += ["## Naming rules to rewrite", "",
                "These were written in a way the catalogue couldn't check. Rewrite each as simple statements.", ""]
        for r in rules:
            out += [f"- id: `rule:{r['uid']}` (for {r['scope']}): {r['text']}"]
        out.append("")
    return "\n".join(out)


def gaps_zip(cat: Catalogue) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("gaps-to-fill.md", gaps_markdown(cat))
        z.writestr("answers-template.csv", ",".join(ANSWER_HEADER) + "\n")
    return buf.getvalue()


def parse_answers(text: str) -> list[dict[str, str]]:
    """Rows of id, column, answer from Copilot's reply: a CSV, possibly inside a ```csv block or with chatter."""
    m = re.search(r"```(?:csv)?\s*\n(.*?)```", text or "", re.S)
    body = m.group(1) if m else (text or "")
    lines = body.strip().splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip().lower().replace(" ", "").startswith("id,")), None)
    if start is None:
        return []
    out = []
    for row in csv.DictReader(io.StringIO("\n".join(lines[start:]))):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items() if k}
        if row.get("id") and row.get("answer"):
            out.append({"id": row["id"].strip("` "), "column": row.get("column", "").strip("` "),
                        "answer": row["answer"]})
    return out


def read_answers(cat: Catalogue, text: str) -> dict[str, int]:
    """Turn answers into suggestions. Returns {"added", "unknown"}: answers for ids not in the catalogue are skipped."""
    rows = parse_answers(text)
    added = unknown = 0
    rules = {f"rule:{r['uid']}": r for r in text_rules(cat)}
    for r in rows:
        if r["id"] in rules:
            rule = rules[r["id"]]
            suggestions.add(cat, "copilot", rule["scope"], f"Naming rule: “{r['answer'][:140]}”",
                            detail=f"Copilot's rewrite of “{rule['text'][:140]}”.",
                            payload={"type": "rule", "uid": rule["uid"], "scope": rule["scope"], "text": r["answer"]},
                            ident=suggestions.make_id("copilot", r["id"]))
            added += 1
            continue
        a = cat.get_asset(r["id"])
        if a is None:
            unknown += 1
            continue
        col = r["column"]
        if col:
            real = next((c["name"] for c in cat.columns(a["key"]) if c["name"].lower() == col.lower()), None)
            if real is None:
                unknown += 1
                continue
            col = real
        title = f"{a['name']}{'.' + col if col else ''}: “{r['answer'][:140]}”"
        suggestions.add(cat, "copilot", a["key"], title, detail="Drafted by your Copilot agent from the gaps pack.",
                        payload={"type": "description", "key": a["key"], "column": col, "text": r["answer"]},
                        ident=suggestions.make_id("copilot", a["key"], col.lower()))
        added += 1
    return {"added": added, "unknown": unknown}


@suggestions.handler("copilot")
def _accept(cat: Catalogue, p: dict[str, Any]) -> None:
    if p.get("type") == "rule":
        from .naming import add_rule, parse_rules, update_rule
        found = [r for r in parse_rules(p["text"]) if r["check_type"] != "text"]
        for r in found:
            add_rule(cat, p["scope"], r["applies_to"], r["check_type"], r["value"], r["text"])
        if found:
            update_rule(cat, p["uid"], state="replaced")
        return
    from .curation import set_column_info, set_item_info
    if p.get("column"):
        set_column_info(cat, p["key"], p["column"], description=p["text"])
    else:
        set_item_info(cat, p["key"], purpose=p["text"])
