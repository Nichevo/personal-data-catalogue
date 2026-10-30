"""The Copilot pack: what the catalogue knows, written as plain documents an AI
assistant can search, for Microsoft 365 Copilot.

Put the folder on OneDrive or SharePoint and add it as knowledge to a Copilot
agent (in Copilot Studio, or "Create an agent" in Microsoft 365 Copilot).
agent-instructions.md holds a description, instructions and starter prompts to
paste into the agent.

The pack has a start-here overview, business rules (your knowledge notes),
a glossary (metrics and company-wide keys), example questions with their SQL,
one file per database, folder or project describing every table and file
(purpose, grain, refresh, status, columns, keys, joins, notes, what loads and
reads it), Power BI reports and Excel workbooks, the ML feature dictionary,
and the code.

No data values are included unless you ask for them, and never for sensitive
columns. Server names and file locations are included (they help Copilot write
queries that run); switch them off with hide_locations when the pack goes
outside the team.

Markdown by default; Word (.docx) if you prefer, which every Copilot reads.
"""
from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from collections import defaultdict
from typing import Any

from .store import Catalogue, now

TABLE_KINDS = ("table", "view", "data_file", "file_series", "workbook")
CODE_KINDS = ("script", "notebook", "sql_script", "procedure")
MAX_FILE_CHARS = 400_000  # split bigger files so each stays easy for Copilot to index

log = logging.getLogger(__name__)


class Pack:
    def __init__(self, cat: Catalogue, *, hide_locations: bool = False, include_values: bool = False):
        from .curation import all_item_info, sensitive_columns
        self.cat = cat
        self.hide = hide_locations
        self.values = include_values
        self.info = all_item_info(cat)
        self.sensitive = sensitive_columns(cat)
        self.col_info = defaultdict(dict)
        for r in cat.conn.execute("SELECT * FROM column_info"):
            self.col_info[r["asset_key"]][r["column_name"]] = dict(r)
        self.names = {r["key"]: r["name"] for r in cat.conn.execute("SELECT key, name FROM assets")}
        self.problems: list[str] = []  # parts left out because something in them couldn't be read
        from .register import rows_by_report
        self.register = self.safely("report register", rows_by_report, cat, default={})

    def safely(self, what: str, fn, *args, default: Any = None) -> Any:
        """Run one part of the pack; if it fails, note it and carry on so the rest of the pack is still made."""
        try:
            return fn(*args)
        except Exception as e:  # one odd item must never stop the whole export
            log.warning("Copilot pack: %s left out", what, exc_info=True)
            self.problems.append(f"{what}: {type(e).__name__}: {e}")
            return default

    # -- helpers --
    def loc(self, text: str | None) -> str:
        """A server name or path, or nothing when locations are hidden."""
        return "" if self.hide or not text else str(text)

    def name(self, key: Any) -> str:
        if isinstance(key, dict):  # a table entry from the SQL library: {"name", "key", ...}
            return self.name(key["key"]) if key.get("key") else str(key.get("name") or "")
        if not isinstance(key, str):
            return "" if key is None else str(key)
        a = self.names.get(key)
        if a is None:
            return key
        if key.startswith("sql://"):
            parts = key.split("/")
            return f"{parts[3]}.{a}" if len(parts) > 3 and "." in a else a
        return a

    def badges(self, key: str) -> list[str]:
        i = self.info.get(key, {})
        out = []
        if i.get("lifecycle") in ("deprecated", "retired"):
            out.append(f"**{i['lifecycle'].upper()}**" + (f": use {i['replaced_by']} instead" if i.get("replaced_by")
                                                          else ""))
        elif i.get("lifecycle"):
            out.append(i["lifecycle"])
        if i.get("trust") == "do not use":
            out.append("**DO NOT USE**")
        elif i.get("trust"):
            out.append(i["trust"])
        return out


def _md_escape(s: Any) -> str:
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")


def _table(rows: list[list[Any]], header: list[str]) -> str:
    if not rows:
        return ""
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(_md_escape(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


# ---- sections -----------------------------------------------------------------------------

def start_here(p: Pack, files: list[str]) -> str:
    cat = p.cat
    stats = cat.stats()
    lines = ["# Data catalogue: start here", "",
             f"Made {now()[:10]} from the team's data catalogue. It describes the data we use (SQL Server tables "
             "and views, data files, Power BI reports, Excel workbooks), the code that builds it, and what the team "
             "knows about it: business rules, definitions, example questions and known issues.", "",
             "## How to use it when answering questions", "",
             "- Check **business-rules** first: they override anything a table or column name suggests.",
             "- Use the **glossary** for the meaning of business terms, metrics and company-wide keys.",
             "- Look for a similar question in **example-questions** and reuse its query and caveats.",
             "- Only use tables marked DEPRECATED or DO NOT USE if asked about them, and say so.",
             "- Join tables on the joins listed for each table, or on company-wide keys.",
             "- Say which tables you used and quote any caveats or known issues that apply.", "",
             "## What's here", ""]
    lines += [f"- {f}" for f in files]
    lines += ["", "## Sources", ""]
    rows = []
    for r in cat.conn.execute("SELECT source, kind, attrs FROM assets WHERE kind IN ('database', 'folder', 'project') "
                              "AND status = 'active' ORDER BY source"):
        a = json.loads(r["attrs"])
        what = {"database": "SQL Server database", "folder": "data folder", "project": "Python project"}[r["kind"]]
        where = p.loc(a.get("server")) + (f" / {a['database']}" if a.get("database") else "") if r["kind"] == \
            "database" else p.loc(a.get("path"))
        rows.append([r["source"], what, where])
    lines.append(_table(rows, ["source", "kind", "where"]))
    kinds = stats["kinds"]
    lines += ["", "In numbers: " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in sorted(kinds.items())) + "."]
    return "\n".join(lines) + "\n"


def business_rules(p: Pack) -> str:
    from .knowledge import list_knowledge
    notes = list_knowledge(p.cat)
    if not notes:
        return ""
    out = ["# Business rules and team knowledge", "",
           "Things the team knows that aren't obvious from the data. Apply them whenever a question touches the "
           "subject.", ""]
    by_section: dict[str, list] = defaultdict(list)
    for k in notes:
        by_section[k["section"] or "General"].append(k)
    for section, items in by_section.items():
        out += [f"## {section}", ""]
        for k in items:
            out += [f"### {k['title']}", "", (k["body"] or "").strip(), ""]
            if k["triggers"]:
                out += [f"Applies when a question mentions: {k['triggers']}.", ""]
            if k["pinned"]:
                out += ["Applies to: " + ", ".join(p.name(x) for x in k["pinned"]) + ".", ""]
    return "\n".join(out)


def glossary(p: Pack) -> str:
    from .keys import key_columns, list_keys
    from .knowledge import list_metrics
    from .metricvalues import usual_values
    out = ["# Glossary", ""]
    metrics = list_metrics(p.cat)
    if metrics:
        out += ["## Metrics", ""]
        for m in metrics:
            out += [f"### {m['name']}", ""]
            if m["definition"]:
                out += [m["definition"], ""]
            if m["formula"]:
                out += ["How it's worked out:", "", "```", m["formula"], "```", ""]
            if m["sources"]:
                out += ["Comes from: " + ", ".join(p.name(s) for s in m["sources"]) + ".", ""]
            if m["owner"]:
                out += [f"Owner: {m['owner']}.", ""]
            if m["caveats"]:
                out += [f"Caveats: {m['caveats']}", ""]
            usual = p.safely(f"usual values of {m['name']}", usual_values, p.cat, m, default=None)
            if usual:
                label = {"yours": "Usual values", "measured": "Usual values (from the data)",
                         "estimated": "Usual values (estimated from the data, not measured)",
                         "inputs": "Usual values of the inputs"}.get(usual["source"], "Usual values")
                out += [f"{label}: {usual['text']}." + (f" {usual['note']}" if usual["note"] else ""), ""]
    keys = list_keys(p.cat)
    if keys:
        out += ["## Company-wide keys", "",
                "These identify the same thing everywhere they appear, so tables can be joined on them.", ""]
        for k in keys:
            out += [f"### {k['name']}", ""]
            if k["description"]:
                out += [k["description"], ""]
            if k["format"]:
                out += [f"Format: {k['format']}.", ""]
            if k["aliases"]:
                out += ["Also called: " + ", ".join(k["aliases"]) + ".", ""]
            if k.get("master_key"):
                out += [f"Master list: {p.name(k['master_key'])}" + (f".{k['master_column']}" if k.get(
                    "master_column") else "") + ".", ""]
            places = key_columns(p.cat, k["name"], "confirmed")
            if places:
                out += ["Found in: " + ", ".join(f"{p.name(c['asset_key'])}.{c['column_name']}" for c in places[:60])
                        + ".", ""]
    return "\n".join(out) if len(out) > 2 else ""


def example_questions(p: Pack) -> str:
    from .knowledge import list_questions
    qs = list_questions(p.cat)
    if not qs:
        return ""
    out = ["# Example questions", "", "Questions the team has answered, with how and the query used.", ""]
    for q in qs:
        out += [f"## {q['question']}", ""]
        if q["answer"]:
            out += [q["answer"], ""]
        if q["sql"]:
            out += ["```sql", q["sql"].strip(), "```", ""]
        if q["tables"]:
            out += ["Uses: " + ", ".join(p.name(t) for t in q["tables"]) + ".", ""]
        if q["caveats"]:
            out += [f"Caveats: {q['caveats']}", ""]
    return "\n".join(out)


def _item(p: Pack, a: dict[str, Any]) -> list[str]:
    """One table, view, file or workbook."""
    from .curation import is_sensitive
    from .keys import columns_with_keys, join_hints
    from .relationships import joins_for
    cat = p.cat
    key = a["key"]
    attrs = json.loads(a["attrs"])
    info = p.info.get(key, {})
    title = p.name(key)
    out = [f"## {title}", ""]
    facts = [a["kind"].replace("_", " ")]
    if attrs.get("database"):
        facts.append(f"database {attrs['database']}")
    if attrs.get("server") and p.loc(attrs["server"]):
        facts.append(f"server {attrs['server']}")
    if isinstance(attrs.get("row_count"), (int, float)):
        facts.append(f"{attrs['row_count']:,} rows")
    elif attrs.get("row_count"):
        facts.append(f"{attrs['row_count']} rows")
    if a["kind"] == "file_series":
        facts.append(f"{attrs.get('frequency', '')} files {attrs.get('first', '')} to {attrs.get('last', '')}".strip())
    if attrs.get("path") and p.loc(attrs["path"]):
        facts.append(f"location {attrs['path']}")
    facts += p.badges(key)
    out += ["- " + " · ".join(f for f in facts if f)]
    for label, field in (("Purpose", "purpose"), ("One row per", "grain"), ("Refreshed", "refresh"),
                         ("Owner", "owner")):
        if info.get(field):
            out.append(f"- {label}: {info[field]}")
    if a["description"]:
        out.append(f"- Description: {a['description']}")
    if is_sensitive(p.sensitive, key):
        out.append("- Contains sensitive data.")
    out.append("")
    cols = cat.columns(key)
    if cols:
        keyed = columns_with_keys(cat, key)
        profile = {r["column_name"]: r for r in cat.profile(key)} if p.values else {}
        rows = []
        for c in cols:
            desc = (p.col_info[key].get(c["name"], {}).get("description") or c["description"] or "")
            extra = []
            if c["name"] in keyed and keyed[c["name"]]["state"] == "confirmed":
                extra.append(f"key {keyed[c['name']]['key']}")
            if is_sensitive(p.sensitive, key, c["name"]):
                extra.append("sensitive")
            elif p.values and c["name"] in profile:
                pr = profile[c["name"]]
                if pr["min_value"] is not None:
                    extra.append(f"{pr['min_value']} to {pr['max_value']}")
                top = json.loads(pr["top_values"] or "[]")
                if top and pr["distinct_count"] and pr["distinct_count"] <= 20:
                    extra.append("values: " + ", ".join(str(t[0] if isinstance(t, list) else t.get("value", t))
                                                         for t in top[:10]))
            rows.append([c["name"], c["data_type"] or "", desc, "; ".join(extra)])
        out += [_table(rows, ["column", "type", "meaning", "notes"]), ""]
    joins = []
    if a["kind"] in ("table", "view"):
        for r in joins_for(cat, key):
            if r["state"] in ("declared", "confirmed"):
                other, mine, theirs = (r["dst_key"], r["src_columns"], r["dst_columns"]) if r["src_key"] == key \
                    else (r["src_key"], r["dst_columns"], r["src_columns"])
                joins.append(f"{p.name(other)} on {mine} = {theirs}")
    joins += [f"{p.name(h['other'])} on {h['column']} = {h['other_column']} (key {h['key']})"
              for h in join_hints(cat, key)[:10]]
    if joins:
        out += ["Joins to: " + "; ".join(dict.fromkeys(joins)) + ".", ""]
    made = sorted({p.name(l["src_key"]) for l in cat.links_to(key) if l["relation"] == "writes"})
    read = sorted({p.name(l["src_key"]) for l in cat.links_to(key) if l["relation"] != "writes"})
    if made:
        out += ["Created or loaded by: " + ", ".join(made) + ".", ""]
    if read:
        out += ["Used by: " + ", ".join(read[:30]) + ".", ""]
    notes = [n for n in cat.notes(key) if not n["archived"] and n["note_type"] != "example"]
    if notes:
        out += ["Notes:", ""]
        for n in notes:
            col = n["asset_key"].partition("#")[2]
            prefix = "Known issue" if n["note_type"] == "issue" and n["status"] == "open" else \
                ("Fixed issue" if n["note_type"] == "issue" else "Note")
            out.append(f"- {prefix}{f' ({col})' if col else ''}: {n['body'].strip()}")
        out.append("")
    return out


def data_files(p: Pack) -> dict[str, str]:
    """One document per database, folder or project, split by schema when large."""
    cat = p.cat
    marks = ",".join(f"'{k}'" for k in TABLE_KINDS)
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in cat.conn.execute(f"SELECT * FROM assets WHERE status = 'active' AND kind IN ({marks}) ORDER BY key"):
        a = dict(r)
        attrs = json.loads(a["attrs"])
        if a["kind"] in ("table", "view"):
            group = f"{a['source']}-{attrs.get('database', '')}"
        else:
            group = a["source"]
        groups[group].append(a)
    out = {}
    for group, items in groups.items():
        slug = re.sub(r"[^a-z0-9]+", "-", group.lower()).strip("-")
        text = [f"# {group}", "", f"{len(items)} items.", ""]
        body: list[str] = []
        for a in items:
            body += p.safely(f"{a['name']} ({a['kind']})", _item, p, a, default=[])
        doc = "\n".join(text + body)
        if len(doc) <= MAX_FILE_CHARS:
            out[f"data/{slug}.md"] = doc
            continue
        # Split by schema (tables) or first folder (files).
        parts: dict[str, list[str]] = defaultdict(list)
        for a in items:
            attrs = json.loads(a["attrs"])
            part = attrs.get("schema") or (a["name"].split("/")[0] if "/" in a["name"] else "other")
            parts[str(part)] += p.safely(f"{a['name']} ({a['kind']})", _item, p, a, default=[])
        for part, lines in parts.items():
            out[f"data/{slug}-{re.sub(r'[^a-z0-9]+', '-', part.lower())}.md"] = "\n".join(
                [f"# {group}: {part}", ""] + lines)
    return out


def _page(pg: dict[str, Any]) -> str:
    visuals = [str(v.get("title") or v.get("type") or "visual") for v in pg.get("visuals") or [] if isinstance(v, dict)]
    return f"{pg.get('name') or ''} ({', '.join(visuals)})"


def reports(p: Pack) -> str:
    cat = p.cat
    rows = cat.conn.execute("SELECT * FROM assets WHERE status = 'active' AND kind = 'report' ORDER BY name").fetchall()
    if not rows:
        return ""
    out = ["# Power BI reports", ""]
    for r in rows:
        a = json.loads(r["attrs"])
        out += [f"## {r['name']}", ""]
        if p.loc(a.get("path")):
            out += [f"Location: {a['path']}", ""]
        info = p.info.get(r["key"], {})
        if info.get("purpose"):
            out += [f"Purpose: {info['purpose']}", ""]
        reg = p.register.get(r["key"]) if p.register else None
        if reg:
            from .register import strip_line
            out += ["Register: " + "; ".join(f"{k} {v}" for k, v in strip_line(reg)), ""]
        if a.get("pages"):
            out += ["Pages: " + "; ".join(_page(pg) for pg in a["pages"] if isinstance(pg, dict)), ""]
        reads = sorted({p.name(l["dst_key"]) for l in cat.links_from(r["key"]) if l["dst_key"]})
        if reads:
            out += ["Loads: " + ", ".join(reads) + ".", ""]
        for m in cat.children(r["key"]):
            if m["kind"] != "measure":
                continue
            ma = json.loads(m["attrs"])
            out += [f"### Measure {m['name']}", ""]
            if m["description"]:
                out += [m["description"], ""]
            if ma.get("expression"):
                out += ["```", str(ma["expression"]), "```", ""]
            if ma.get("used_on"):
                out += ["Shown on: " + ", ".join(str(u) for u in ma["used_on"]) + ".", ""]
    return "\n".join(out)


def features(p: Pack) -> str:
    from .mlfeatures import list_features
    feats = list_features(p.cat)
    if not feats:
        return ""
    out = ["# Machine learning feature dictionary", ""]
    by_project: dict[str, list] = defaultdict(list)
    for f in feats:
        by_project[f["project"] or "Other"].append(f)
    for project, items in by_project.items():
        out += [f"## {project}", ""]
        rows = [[f["name"], f["description"] or "", f["window"] or "", f["known_at_prediction"] or "",
                 ", ".join(p.name(s.partition("#")[0]) + (f".{s.partition('#')[2]}" if "#" in s else "")
                           for s in f["sources"]), f["expression"] or ""] for f in items]
        out += [_table(rows, ["feature", "meaning", "lag/window", "known at prediction", "from", "built as"]), ""]
    return "\n".join(out)


def code(p: Pack) -> str:
    cat = p.cat
    marks = ",".join(f"'{k}'" for k in CODE_KINDS)
    rows = cat.conn.execute(f"SELECT * FROM assets WHERE status = 'active' AND kind IN ({marks}) ORDER BY source, "
                            "name").fetchall()
    if not rows:
        return ""
    out = ["# Code that builds the data", "", "Scripts, notebooks, SQL files and stored procedures, and what each "
                                             "reads and writes.", ""]
    for r in rows:
        reads = sorted({p.name(l["dst_key"]) for l in cat.links_from(r["key"]) if l["dst_key"] and l["relation"] ==
                        "reads"})
        writes = sorted({p.name(l["dst_key"]) for l in cat.links_from(r["key"]) if l["dst_key"] and l["relation"] ==
                         "writes"})
        info = p.info.get(r["key"], {})
        if not (reads or writes or r["description"] or info.get("purpose")):
            continue
        out += [f"## {r['source']} / {r['name']}", ""]
        if info.get("purpose") or r["description"]:
            out += [(info.get("purpose") or r["description"]).strip(), ""]
        if reads:
            out += ["Reads: " + ", ".join(reads) + ".", ""]
        if writes:
            out += ["Writes: " + ", ".join(writes) + ".", ""]
    return "\n".join(out)


def agent_instructions(p: Pack) -> str:
    from .knowledge import list_questions
    qs = [q["question"] for q in list_questions(p.cat)][:6]
    top = [r["name"] for r in p.cat.conn.execute(
        "SELECT a.name, COUNT(*) n FROM links l JOIN assets a ON a.key = l.dst_key WHERE a.kind IN ('table', 'view') "
        "GROUP BY a.key ORDER BY n DESC LIMIT 4")]
    starters = qs + [f"What is {t} and how do I use it?" for t in top]
    starters = starters[:6] or ["Which table has daily sales by store?", "How do I join sales to stores?"]
    return "\n".join([
        "# Copilot agent setup", "",
        "Create an agent in Microsoft 365 Copilot (Create an agent) or in Copilot Studio, add this folder (on "
        "OneDrive or SharePoint) as its knowledge, and paste the text below.", "",
        "## Name", "", "Data catalogue helper", "",
        "## Description", "",
        "Answers questions about the team's data: which table or file to use, what columns mean, how to join "
        "them, business rules, and example queries.", "",
        "## Instructions", "",
        "You help analysts find and use the team's data. Answer only from the knowledge files.",
        "1. Read 00-start-here first. Business rules in business-rules override what names suggest.",
        "2. When asked for data, name the table or file (with its database and schema), say what one row is, "
        "and list the columns to use with their meaning.",
        "3. Write SQL for SQL Server (T-SQL). Use the joins listed for each table or company-wide keys. "
        "Reuse queries from example-questions when one fits.",
        "4. Never suggest a table marked DEPRECATED or DO NOT USE unless asked; point to its replacement.",
        "5. Quote known issues and caveats that apply. If the files don't say, say you don't know and suggest "
        "who owns the data.",
        "6. Keep answers short. Show the query in a code block.", "",
        "## Starter prompts", "",
        *[f"- {s}" for s in starters], ""])


def build(cat: Catalogue, *, hide_locations: bool = False, include_values: bool = False,
          include_documents: bool = False) -> dict[str, str]:
    """{file name: Markdown} for the whole pack."""
    p = Pack(cat, hide_locations=hide_locations, include_values=include_values)
    files: dict[str, str] = {}
    parts = [("business-rules.md", business_rules), ("glossary.md", glossary),
             ("example-questions.md", example_questions), ("reports.md", reports),
             ("ml-features.md", features), ("code.md", code), ("naming-rules.md", naming_rules),
             ("sap.md", sap), ("sql-queries.md", sql_queries)]
    if include_documents:
        parts.append(("reference-documents.md", documents))
    parts.append(("test-questions.md", test_file))
    for name, fn in parts:
        text = p.safely(name, fn, p, default="")
        if text.strip():
            files[name] = text
    files.update(p.safely("data files", data_files, p, default={}))
    files["agent-instructions.md"] = agent_instructions(p)
    start = p.safely("00-start-here.md", start_here, p, ["00-start-here.md", *files],
                     default="# Data catalogue: start here\n")
    if p.problems:
        start += ("\n## Left out of this pack\n\nThese couldn't be read when the pack was made, so they are "
                  "missing. Everything else is complete.\n\n" + "\n".join(f"- {x}" for x in p.problems) + "\n")
    files = {"00-start-here.md": start, **files}
    return files


def to_zip(cat: Catalogue, *, hide_locations: bool = False, include_values: bool = False, fmt: str = "md",
           include_documents: bool = False) -> bytes:
    files = build(cat, hide_locations=hide_locations, include_values=include_values,
                  include_documents=include_documents)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            if fmt == "docx":
                from .docx import markdown_to_docx
                z.writestr(name[:-3] + ".docx", markdown_to_docx(text))
            else:
                z.writestr(name, text)
    return buf.getvalue()


def write(cat: Catalogue, out, *, hide_locations: bool = False, include_values: bool = False, fmt: str = "md",
          include_documents: bool = False):
    from pathlib import Path
    out = Path(out)
    files = build(cat, hide_locations=hide_locations, include_values=include_values,
                  include_documents=include_documents)
    written = []
    for name, text in files.items():
        path = out / (name[:-3] + ".docx" if fmt == "docx" else name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "docx":
            from .docx import markdown_to_docx
            path.write_bytes(markdown_to_docx(text))
        else:
            path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


# Naming rules, SAP meanings, the SQL query library, reference documents (when ticked) and test questions
# built from your own catalogue, with the answer the pack holds.

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
        names = [n for n in dict.fromkeys(p.name(t) for t in tables) if n]
        if names:
            out += ["Uses: " + ", ".join(names) + ".", ""]
        out += ["```sql", str(r["sql"] or "").strip()[:4000], "```", ""]
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
                                                "question", "sqlscript")}
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
    # SQL scripts and pasted SQL: a good agent answers from the tables the script touches, not from guesswork.
    for r in cat.conn.execute(
            "SELECT a.key, a.name, a.description, "
            "group_concat(DISTINCT CASE WHEN l.relation = 'reads' THEN COALESCE(l.dst_key, l.dst_ref) END) AS reads, "
            "group_concat(DISTINCT CASE WHEN l.relation = 'writes' THEN COALESCE(l.dst_key, l.dst_ref) END) AS writes "
            "FROM assets a JOIN links l ON l.src_key = a.key WHERE a.kind IN ('sql_script', 'sql_snippet') "
            "AND a.status = 'active' GROUP BY a.key LIMIT 10"):
        bits = []
        for label, keys in (("reads", r["reads"]), ("writes", r["writes"])):
            names = [p.name(k) if k in p.names else k.removeprefix("table:") for k in (keys or "").split(",") if k]
            if names:
                bits.append(f"{label} {', '.join(sorted(set(names)))}")
        if bits:
            answer = f"{r['name']} {'; '.join(bits)}." + (f" {r['description']}" if r["description"] else "")
            picks["sqlscript"].append((f"What does the SQL script {r['name']} read and write?", answer))
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
