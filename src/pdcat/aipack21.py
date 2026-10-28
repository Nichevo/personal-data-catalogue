"""Version 2.1 additions to the Copilot pack: naming rules, SAP meanings, the SQL query library, reference
documents (when ticked) and test questions built from your own catalogue, with the answer the pack holds."""
from __future__ import annotations

import json
from typing import Any

from .aipack import Pack, _table

MAX_QUERIES = 300
MAX_TESTS = 12


def naming_rules(p: Pack) -> str:
    from .naming import describe
    rows = [dict(r) for r in p.cat.conn.execute("SELECT * FROM naming_rules WHERE state = 'active' "
                                                "ORDER BY scope, applies_to, check_type")]
    if not rows:
        return ""
    out = ["# Naming rules", "",
           "The team's naming rules. Follow them in any SQL or code you write, and use them to guess what an object "
           "is from its name.", ""]
    by_scope: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_scope.setdefault(r["scope"], []).append(r)
    for scope, rules in by_scope.items():
        out += [f"## {p.name(scope)}", ""]
        for r in rules:
            text = describe(r) if r["check_type"] != "text" else (r["text"] or "")
            out.append(f"- {text}" + (f" (as written: “{r['text']}”)" if r["text"] and r["check_type"] != "text"
                                      and r["text"] != text else ""))
        out.append("")
    return "\n".join(out)


def sap(p: Pack) -> str:
    from .sapterms import apo_items, sap_items
    items = sap_items(p.cat)
    apo = apo_items(p.cat)
    if not items and not apo:
        return ""
    out = ["# SAP links", "",
           "Which tables and columns match SAP tables and fields. Users may ask with either the SAP name (WERKS, "
           "MATNR) or the business name (plant, material); treat them as the same thing.", ""]
    if items:
        out.append(_table([[p.name(i["key"]), i["column"], i["SAP"], i["meaning"]] for i in items],
                          ["item", "column", "SAP name", "meaning"]))
        out.append("")
    if apo:
        out += ["## Still using APO", "",
                "These still use SAP APO, which is being replaced by IBP. Mention it when they come up.", "",
                _table([[a["item"], a["uses"], a["IBP equivalent"]] for a in apo], ["item", "APO", "IBP equivalent"]),
                ""]
    return "\n".join(out)


def sql_queries(p: Pack) -> str:
    rows = [dict(r) for r in p.cat.conn.execute(
        "SELECT q.uid, q.name, q.sql, q.tables, (SELECT COUNT(*) FROM sql_query_places x WHERE x.query_uid = q.uid) "
        "AS places FROM sql_queries q WHERE q.status = 'active' ORDER BY places DESC, q.name LIMIT ?", (MAX_QUERIES,))]
    if not rows:
        return ""
    out = ["# SQL query library", "",
           "Queries the team already uses, most widely used first. Reuse one when it fits the question.", ""]
    for r in rows:
        tables = json.loads(r["tables"] or "[]")
        out += [f"## {r['name']}", ""]
        if tables:
            out += ["Uses: " + ", ".join(p.name(t) for t in tables) + ".", ""]
        out += ["```sql", r["sql"].strip()[:4000], "```", ""]
    return "\n".join(out)


def documents(p: Pack) -> str:
    from .documents import all_latest
    docs = all_latest(p.cat)
    if not docs:
        return ""
    out = ["# Reference documents", "", "Design documents kept with business projects (latest version of each).", ""]
    for d in docs:
        owner = p.cat.conn.execute("SELECT name FROM bprojects WHERE 'bp://' || uid = ?", (d["owner"],)).fetchone()
        out += [f"## {d['title']}", ""] + ([f"Business project: {owner[0]}.", ""] if owner else [])
        out += [(d["text"] or "(no text could be read)").strip()[:60_000], ""]
    return "\n".join(out)


def test_questions(cat: Any, limit: int = MAX_TESTS) -> list[dict[str, str]]:
    """Questions built from the catalogue, each with the answer the pack holds, to check Copilot with."""
    from .curation import all_item_info
    from .knowledge import list_knowledge, list_metrics, list_questions
    p = Pack(cat)
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(q: str, a: str, kind: str) -> None:
        if q not in seen and a.strip():
            seen.add(q)
            out.append({"question": q, "answer": a.strip(), "about": kind})

    picks: dict[str, list[tuple[str, str]]] = {k: [] for k in
                                               ("writes", "metric", "replace", "grain", "join", "column", "sap", "rule",
                                                "question")}
    for r in cat.conn.execute(
            "SELECT l.dst_key, group_concat(DISTINCT a.name) FROM links l JOIN assets a ON a.key = l.src_key "
            "JOIN assets d ON d.key = l.dst_key AND d.status = 'active' WHERE l.relation = 'writes' "
            "GROUP BY l.dst_key LIMIT 20"):
        picks["writes"].append((f"Which scripts write {p.name(r[0])}?", r[1].replace(",", ", ")))
    for m in list_metrics(cat)[:10]:
        if m.get("definition"):
            picks["metric"].append((f"What does {m['name']} mean?", m["definition"] +
                                    (f" Formula: {m['formula']}" if m.get("formula") else "")))
    info = all_item_info(cat)
    for key, i in info.items():
        if i.get("lifecycle") in ("deprecated", "retired") and i.get("replaced_by"):
            picks["replace"].append((f"What replaces {p.name(key)}?", f"{i['replaced_by']}"))
        if i.get("grain") and key in p.names:
            picks["grain"].append((f"What is one row of {p.name(key)}?", i["grain"]))
    for r in cat.conn.execute("SELECT * FROM relationships WHERE state IN ('declared', 'confirmed') LIMIT 20"):
        cols = zip(json.loads(r["src_columns"]), json.loads(r["dst_columns"]))
        picks["join"].append((f"How do I join {p.name(r['src_key'])} to {p.name(r['dst_key'])}?",
                              " AND ".join(f"{p.name(r['src_key'])}.{a} = {p.name(r['dst_key'])}.{b}" for a, b in cols)))
    for r in cat.conn.execute("SELECT asset_key, column_name, description, sap_name FROM column_info WHERE "
                              "(description IS NOT NULL AND description != '') OR (sap_name IS NOT NULL AND "
                              "sap_name != '') LIMIT 40"):
        if r["asset_key"] not in p.names:
            continue
        if r["description"]:
            picks["column"].append((f"What does {r['column_name']} in {p.name(r['asset_key'])} mean?",
                                    r["description"]))
        if r["sap_name"]:
            picks["sap"].append((f"Which SAP field is {r['column_name']} in {p.name(r['asset_key'])}?", r["sap_name"]))
    for k in list_knowledge(cat)[:10]:
        if k["body"]:
            picks["rule"].append((f"What should I know about {k['title']}?", k["body"].split("\n\n", 1)[0][:400]))
    for q in list_questions(cat)[:10]:
        if q["answer"] or q["sql"]:
            picks["question"].append((q["question"], q["answer"] or q["sql"]))
    # take one of each kind in turn so the list covers different things
    while len(out) < limit and any(picks.values()):
        for kind, items in picks.items():
            if items and len(out) < limit:
                add(*items.pop(0), kind)
    return out


def test_file(p: Pack) -> str:
    qs = test_questions(p.cat)
    if not qs:
        return ""
    out = ["# Test questions", "",
           "Questions to check the agent with. Each has the answer the other files hold. This file is for the "
           "person setting the agent up; the agent shouldn't quote it as a source.", ""]
    for i, q in enumerate(qs, 1):
        out += [f"{i}. **{q['question']}**", f"   Expected: {q['answer']}", ""]
    return "\n".join(out)
