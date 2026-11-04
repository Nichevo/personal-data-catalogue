"""Gold standards (version 2.6): mark a Power BI report, a measure or a piece of SQL as the one to trust, and
see where related items differ from it.

What is compared with a gold standard:

* a **measure**: measures with the same name in other reports, and measures with the same DAX under another
  name. Same DAX is checked a step further down too: "[Units] / [Days]" in two reports only gives the same
  number when [Units] and [Days] are the same in both.
* a **report**: its other versions (the Versions group) and copies that share most of its pages, measures,
  tables and sources. Every measure in a gold report also counts as a gold measure, so the same-named measure
  in any other report is checked against it. A measure you marked yourself wins over a gold report's.
* **SQL** (a query from the SQL library, a view or a procedure): other SQL with the same name you gave it, or
  that reads mostly the same tables and reads alike.

A difference you are happy with can be marked "OK to differ". That holds until the other item changes again.
Each gold standard keeps a copy of what it looked like when marked, so a change to the standard itself is
flagged too, until you accept it as the new standard.

Which reports a gold standard is compared with (version 2.27) is its "scope":

* ``current`` (the default): every report except old ones: older versions in a Versions group, reports you
  marked old (🗄️ Mark old) or not relevant, and reports in a folder whose role is archive.
* ``all``: old ones too.
* ``picked``: only the reports you pick.

In the first two, reports you leave out ("skip") are not compared either. For a measure the scope is about the
report the other measure is in; for SQL, about the query or view itself.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
from typing import Any

from .store import Catalogue, now
from .textclean import as_text

KINDS = {"report": "report", "measure": "measure", "view": "sql", "procedure": "sql"}
KIND_LABELS = {"report": "Power BI report", "measure": "measure", "sql": "SQL"}
REPORT_SIMILAR = 0.4    # share of pages, measures, tables and sources for a report to count as a copy
SQL_TABLES = 0.5        # share of tables two queries must read in common
SQL_ALIKE = 0.5         # how alike their text must read, as well
SQL_VERY_ALIKE = 0.8    # this alike counts whatever tables they read
MAX_TOKENS = 4000       # longer SQL is compared on its first this-many words
DIFF_CONTEXT = 2
SCOPES = {"current": "Current reports only (old versions left out)", "all": "All reports, old versions too",
          "picked": "Only the reports I pick"}


# ---- marking --------------------------------------------------------------------------------------

def kind_of(cat: Catalogue, key: str) -> str | None:
    """'report', 'measure' or 'sql' when the item can be a gold standard, else None."""
    if key.startswith("query://"):
        return "sql"
    r = cat.conn.execute("SELECT kind FROM assets WHERE key = ?", (key,)).fetchone()
    return KINDS.get(r[0]) if r else None


def get(cat: Catalogue, key: str) -> dict[str, Any] | None:
    r = cat.conn.execute("SELECT * FROM gold_standards WHERE key = ?", (key,)).fetchone()
    return dict(r) if r else None


def all_golds(cat: Catalogue) -> list[dict[str, Any]]:
    return [dict(r) for r in cat.conn.execute("SELECT * FROM gold_standards ORDER BY kind, key")]


def mark(cat: Catalogue, key: str, note: str = "", *, record_edit: bool = True) -> None:
    """Make an item a gold standard (or change its note)."""
    kind = kind_of(cat, key)
    if kind is None:
        raise ValueError(f"{key} can't be a gold standard: only reports, measures and SQL can.")
    old = get(cat, key)
    item = load(cat, key)
    snap = json.dumps(_snapshot(item)) if item else None
    with cat.conn:
        if old:
            cat.conn.execute("UPDATE gold_standards SET note = ? WHERE key = ?", (note.strip() or None, key))
        else:
            cat.conn.execute("INSERT INTO gold_standards (key, kind, note, snapshot, marked_at, author) "
                             "VALUES (?, ?, ?, ?, ?, ?)", (key, kind, note.strip() or None, snap, now(), cat.author()))
        if record_edit:
            from .edits import record
            record(cat, key, "gold standard", "yes" if old else "", "yes" + (f": {note.strip()}" if note.strip() else ""),
                   {"type": "gold", "key": key, "was": old})
    cat.changed()


def unmark(cat: Catalogue, key: str, *, record_edit: bool = True) -> None:
    old = get(cat, key)
    if old is None:
        return
    # the "OK to differ" answers go with the mark, and come back with it on undo
    oks = [dict(r) for r in cat.conn.execute("SELECT * FROM gold_ok WHERE gold_key = ?", (key,))]
    with cat.conn:
        cat.conn.execute("DELETE FROM gold_standards WHERE key = ?", (key,))
        cat.conn.execute("DELETE FROM gold_ok WHERE gold_key = ?", (key,))
        if record_edit:
            from .edits import record
            record(cat, key, "gold standard", "yes", "", {"type": "gold", "key": key, "was": {**old, "ok": oks}})
    cat.changed()


def restore(cat: Catalogue, key: str, row: dict[str, Any] | None) -> None:
    """Put a gold standard back as it was (for undo); None takes the mark off."""
    with cat.conn:
        cat.conn.execute("DELETE FROM gold_standards WHERE key = ?", (key,))
        if row:
            cat.conn.execute("INSERT INTO gold_standards (key, kind, note, snapshot, marked_at, author, scope) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?)", (row["key"], row["kind"], row.get("note"), row.get("snapshot"),
                                                              row.get("marked_at"), row.get("author"), row.get("scope")))
            for ok in row.get("ok") or []:
                cat.conn.execute("INSERT OR REPLACE INTO gold_ok (gold_key, item_key, fingerprint, note, at, author) "
                                 "VALUES (?, ?, ?, ?, ?, ?)", (ok["gold_key"], ok["item_key"], ok.get("fingerprint"),
                                                               ok.get("note"), ok.get("at"), ok.get("author")))
    cat.changed()


# ---- which reports it is compared with (version 2.27) ----------------------------------------------

def scope_of(row: dict[str, Any] | None) -> dict[str, Any]:
    """{"mode": "current" | "all" | "picked", "skip": [keys], "picked": [keys]} for a gold standard row."""
    try:
        sc = json.loads((row or {}).get("scope") or "{}")
    except (TypeError, ValueError):
        sc = {}
    sc = sc if isinstance(sc, dict) else {}
    mode = sc.get("mode") if sc.get("mode") in SCOPES else "current"
    return {"mode": mode, "skip": [k for k in sc.get("skip") or [] if isinstance(k, str)],
            "picked": [k for k in sc.get("picked") or [] if isinstance(k, str)]}


def set_scope(cat: Catalogue, key: str, mode: str = "current", skip: list[str] | tuple = (),
              picked: list[str] | tuple = (), *, record_edit: bool = True) -> None:
    """Choose which reports (or, for SQL, which queries) a gold standard is compared with."""
    if mode not in SCOPES:
        raise ValueError(f"mode must be one of {', '.join(SCOPES)}")
    old = get(cat, key)
    if old is None:
        return
    new = {"mode": mode, "skip": sorted(set(skip)), "picked": sorted(set(picked))}
    text = None if new == {"mode": "current", "skip": [], "picked": []} else json.dumps(new)
    with cat.conn:
        cat.conn.execute("UPDATE gold_standards SET scope = ? WHERE key = ?", (text, key))
        if record_edit:
            from .edits import record
            record(cat, key, "gold standard compared with", scope_label(scope_of(old)), scope_label(new),
                   {"type": "gold", "key": key, "was": old})
    cat.changed()


def leave_out(cat: Catalogue, gold_key: str, key: str) -> None:
    """Stop comparing one report (or SQL item) with a gold standard."""
    sc = scope_of(get(cat, gold_key))
    if sc["mode"] == "picked":
        set_scope(cat, gold_key, "picked", sc["skip"], [k for k in sc["picked"] if k != key])
    else:
        set_scope(cat, gold_key, sc["mode"], sc["skip"] + [key], sc["picked"])


def scope_label(sc: dict[str, Any]) -> str:
    if sc["mode"] == "picked":
        return f"only {len(sc['picked'])} picked report(s)"
    text = "all reports" if sc["mode"] == "all" else "current reports"
    return text + (f", {len(sc['skip'])} left out" if sc["skip"] else "")


def compared_key(item: dict[str, Any]) -> str:
    """What the scope is about for a related item: a measure's report, else the item itself."""
    return item.get("report_key") or item["key"] if item.get("kind") == "measure" else item["key"]


def _keep(sc: dict[str, Any], key: str, old: set[str]) -> bool:
    if sc["mode"] == "picked":
        return key in sc["picked"]
    if key in sc["skip"]:
        return False
    return not (sc["mode"] == "current" and key in old)


def old_reports(cat: Catalogue) -> set[str]:
    """Reports left out by the "current" scope: older versions, marked old or not relevant, archive folders."""
    return _cached(cat, "old_reports", _read_old_reports)


def _read_old_reports(cat: Catalogue) -> set[str]:
    from .curation import hidden_keys
    from .folderroles import archived
    from .groups import older_versions
    return older_versions(cat) | hidden_keys(cat, old=True) | archived(cat)


def accept_change(cat: Catalogue, key: str) -> None:
    """The gold standard changed on purpose: what it looks like now becomes the standard."""
    item = load(cat, key)
    if item is None:
        return
    with cat.conn:
        cat.conn.execute("UPDATE gold_standards SET snapshot = ?, marked_at = ? WHERE key = ?",
                         (json.dumps(_snapshot(item)), now(), key))
    cat.changed()


def ok_to_differ(cat: Catalogue, gold_key: str, item_key: str, note: str = "") -> None:
    """This item differs on purpose. Holds until the item changes again."""
    item = load(cat, item_key)
    if item is None:
        return
    with cat.conn:
        cat.conn.execute("INSERT INTO gold_ok (gold_key, item_key, fingerprint, note, at, author) VALUES (?, ?, ?, ?, ?, ?) "
                         "ON CONFLICT(gold_key, item_key) DO UPDATE SET fingerprint = excluded.fingerprint, "
                         "note = excluded.note, at = excluded.at, author = excluded.author",
                         (gold_key, item_key, fingerprint(item), note.strip() or None, now(), cat.author()))
    cat.changed()


def clear_ok(cat: Catalogue, gold_key: str, item_key: str) -> None:
    with cat.conn:
        cat.conn.execute("DELETE FROM gold_ok WHERE gold_key = ? AND item_key = ?", (gold_key, item_key))
    cat.changed()


def _oks(cat: Catalogue) -> dict[tuple[str, str], dict[str, Any]]:
    return {(r["gold_key"], r["item_key"]): dict(r) for r in cat.conn.execute("SELECT * FROM gold_ok")}


# ---- reading items --------------------------------------------------------------------------------

def load(cat: Catalogue, key: str) -> dict[str, Any] | None:
    """What a report, measure or piece of SQL holds, in one shape: {"key", "kind", "name", "where", ...}."""
    if key.startswith("query://"):
        from .sqllibrary import get as get_query
        q = get_query(cat, key.removeprefix("query://"))
        if q is None:
            return None
        where = ", ".join(sorted({p["item"].rsplit("/", 1)[-1] for p in q["places"]})[:3])
        return {"key": key, "kind": "sql", "name": q["name"], "where": where, "sql": q["sql"],
                "tables": q["tables"], "analysis": q.get("analysis") or {}, "missing": q["status"] == "missing",
                "named_by": q["named_by"], "uid": q["uid"]}
    a = cat.get_asset(key)
    if a is None:
        return None
    kind = KINDS.get(a["kind"])
    attrs = a["attrs"]
    base = {"key": key, "name": a["name"], "missing": a["status"] == "missing"}
    if kind == "measure":
        report = cat.conn.execute("SELECT name FROM assets WHERE key = ?", (a["parent"],)).fetchone()
        return {**base, "kind": "measure", "name": (as_text(attrs.get("name")) or a["name"]).strip(),
                "table": as_text(attrs.get("table")), "expression": as_text(attrs.get("expression")).strip(),
                "format": as_text(attrs.get("format")), "report_key": a["parent"],
                "where": report["name"] if report else ""}
    if kind == "report":
        return {**base, "kind": "report", "attrs": attrs, "where": attrs.get("path") or a["source"]}
    if kind == "sql":
        sql = as_text(attrs.get("definition"))
        from .sqllibrary import uid_for
        uid = uid_for(sql) if sql else None
        q = cat.conn.execute("SELECT tables, analysis, name, named_by FROM sql_queries WHERE uid = ?", (uid,)).fetchone() \
            if uid else None
        return {**base, "kind": "sql", "sql": sql, "where": f"{a['kind']} in {attrs.get('database') or a['source']}",
                "tables": json.loads(q["tables"]) if q else [], "analysis": json.loads(q["analysis"] or "{}") if q else {},
                "named_by": q["named_by"] if q else "auto", "uid": uid}
    return None


def dax_key(expression: str) -> str:
    from .measures import _dax_key
    return _dax_key(expression)


def fingerprint(item: dict[str, Any]) -> str:
    """Changes when what matters about the item changes (not its spacing, comments or case)."""
    if item["kind"] == "measure":
        text = dax_key(item["expression"]) + "|" + item.get("format", "")
    elif item["kind"] == "sql":
        from .sqllibrary import normalise_sql
        text = normalise_sql(item["sql"])
    else:
        text = json.dumps(_report_content(item["attrs"]), sort_keys=True)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _snapshot(item: dict[str, Any]) -> dict[str, Any]:
    if item["kind"] == "measure":
        return {"expression": item["expression"], "format": item.get("format", ""), "fingerprint": fingerprint(item)}
    if item["kind"] == "sql":
        return {"sql": item["sql"], "fingerprint": fingerprint(item)}
    return {"content": _report_content(item["attrs"]), "fingerprint": fingerprint(item)}


def _report_content(attrs: dict[str, Any]) -> dict[str, dict[str, str]]:
    """The parts of a report that are compared: pages, measures (DAX without spacing), tables, sources."""
    from .reportversions import facets
    f = facets(attrs, "report")
    f.pop("visuals", None)
    f["measures"] = {n: dax_key(d) for n, d in f.get("measures", {}).items()}
    return f


# ---- comparing ------------------------------------------------------------------------------------

def _text_diff(gold: str, other: str, gold_label: str, other_label: str, sql: bool = False) -> str:
    a, b = _lines(gold, sql), _lines(other, sql)
    return "\n".join(difflib.unified_diff(a, b, fromfile=f"gold standard: {gold_label}", tofile=other_label,
                                          lineterm="", n=DIFF_CONTEXT))


_SQL_BREAK = re.compile(r"\s+(?=(?:select|from|(?:left |right |inner |full |cross |outer )*join|where|and|or|"
                        r"group by|order by|having|union|on)\b)", re.I)


def _lines(text: str, sql: bool) -> list[str]:
    """Lines to diff. Long one-line SQL or DAX is broken at keywords or commas, so a diff points at the part that
    differs rather than the whole line."""
    lines = (text or "").strip().splitlines()
    out = []
    for line in lines:
        if len(line) <= 100:
            out.append(line.rstrip())
        elif sql:
            out += [p.strip() for p in _SQL_BREAK.split(line) if p.strip()]
        else:
            out += [p.strip() for p in re.split(r"(?<=,)\s*", line) if p.strip()]
    return out


def _section(title: str, gold: set[str] | list[str], other: set[str] | list[str],
             changed: list[str] | None = None) -> dict[str, Any] | None:
    g, o = set(gold), set(other)
    s = {"title": title, "missing": sorted(g - o, key=str.lower), "extra": sorted(o - g, key=str.lower),
         "changed": sorted(changed or [], key=str.lower)}
    return s if s["missing"] or s["extra"] or s["changed"] else None


def summary(sections: list[dict[str, Any]]) -> str:
    """'2 measures missing, 1 measure changed, filters differ' from a comparison's sections."""
    bits = []
    for s in sections:
        word = s["title"].lower()
        if s.get("text"):
            bits.append(s["text"])
            continue
        for which, label in (("missing", "missing"), ("extra", "extra"), ("changed", "changed")):
            if s.get(which):
                bits.append(f"{len(s[which])} {_singular(word) if len(s[which]) == 1 else word} {label}")
    return ", ".join(bits)


def _singular(word: str) -> str:
    return {"filters": "filter", "joins": "join", "columns": "column", "tables": "table", "measures": "measure",
            "pages": "page", "sources": "source", "queries": "query"}.get(word, word)


def compare_measures(gold: dict[str, Any], other: dict[str, Any], lookup: dict[str, dict[str, dict]] | None = None
                     ) -> dict[str, Any]:
    """{"same": bool, "sections": [...], "text_diff": str, "summary": str} for two measures. `lookup` maps a
    report key to {measure name (lower): measure}, to check the measures each builds on."""
    sections: list[dict[str, Any]] = []
    same_dax = dax_key(gold["expression"]) == dax_key(other["expression"])
    text = ""
    if not same_dax:
        sections.append({"title": "DAX", "text": "calculated differently"})
        text = _text_diff(gold["expression"], other["expression"], f"{gold['where']} › {gold['name']}",
                          f"{other['where']} › {other['name']}")
    elif lookup is not None:
        under = _underneath(gold, other, lookup)
        if under:
            sections.append({"title": "Builds on", "text": "same DAX, but " + ", ".join(f"[{n}]" for n in under)
                             + (" differs" if len(under) == 1 else " differ") + " underneath", "changed": under})
    if (gold.get("format") or "") != (other.get("format") or "") and gold.get("format") and other.get("format"):
        sections.append({"title": "Format", "text": f"format {other['format']!r} instead of {gold['format']!r}"})
    if gold["name"].lower() != other["name"].lower():
        sections.append({"title": "Name", "text": f"named {other['name']!r} instead of {gold['name']!r}"})
    return {"same": not sections, "sections": sections, "text_diff": text, "summary": summary(sections),
            "name_only": bool(sections) and all(s["title"] == "Name" for s in sections)}


def _underneath(gold: dict, other: dict, lookup: dict[str, dict[str, dict]], depth: int = 0,
                seen: set[str] | None = None) -> list[str]:
    """Measures used by both that are worked out differently in the two reports, followed down a few steps."""
    from .measures import dax_refs
    seen = seen if seen is not None else set()
    g_report, o_report = lookup.get(gold.get("report_key"), {}), lookup.get(other.get("report_key"), {})
    if not g_report or not o_report or depth > 4:
        return []
    names = {n: f"{m.get('table', '')}[{m['name']}]" for n, m in g_report.items()}
    used, _ = dax_refs(gold["expression"], names)
    out = []
    for label in used:
        n = label.split("[", 1)[1][:-1].lower()
        if n in seen:
            continue
        seen.add(n)
        g, o = g_report.get(n), o_report.get(n)
        if not g or not o:
            continue
        if dax_key(g["expression"]) != dax_key(o["expression"]):
            out.append(g["name"])
        else:
            out += _underneath(g, o, lookup, depth + 1, seen)
    return out


def compare_reports(gold: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    g, o = _report_content(gold["attrs"]), _report_content(other["attrs"])
    sections = []
    for facet, title in (("measures", "Measures"), ("tables", "Tables"), ("sources", "Sources"), ("pages", "Pages")):
        a, b = g.get(facet, {}), o.get(facet, {})
        changed = [n for n in set(a) & set(b) if a[n] != b[n]] if facet == "measures" else []
        s = _section(title, a, b, changed)
        if s:
            sections.append(s)
    text = ""
    gm = {m.get("name", ""): as_text(m.get("expression")) for m in gold["attrs"].get("measures", [])}
    om = {m.get("name", ""): as_text(m.get("expression")) for m in other["attrs"].get("measures", [])}
    for n in sorted(set(gm) & set(om), key=str.lower):
        if dax_key(gm[n]) != dax_key(om[n]):
            text += _text_diff(gm[n], om[n], f"{gold['name']} › {n}", f"{other['name']} › {n}") + "\n"
    return {"same": not sections, "sections": sections, "text_diff": text.strip(), "summary": summary(sections),
            "name_only": False}


_WHERE = re.compile(r"\bwhere\b(.*?)(?=\bgroup\s+by\b|\border\s+by\b|\bhaving\b|\bunion\b|\bwhere\b|$)", re.I | re.S)


def filters(sql: str) -> list[str]:
    """The conditions in a query's WHERE clauses, tidied: ["s.plant = '1000'", "s.qty > 0"]. A simple reading:
    good enough to say which filters one query has and another hasn't."""
    from .sqllibrary import normalise_sql
    out = []
    for m in _WHERE.finditer(normalise_sql(sql)):
        for part in re.split(r"\band\b", m.group(1)):
            p = part.strip().strip("()").strip()
            p = re.sub(r"\s*([=<>!]+)\s*", r" \1 ", p)
            p = re.sub(r"\s+", " ", p)
            if p and p not in out:
                out.append(p)
    return out


def _table_names(item: dict[str, Any]) -> set[str]:
    return {(t.get("name") or "").lower() for t in item.get("tables") or [] if t.get("name")}


def _columns(item: dict[str, Any]) -> set[str]:
    return {f"{(t.get('name') or '').split('.')[-1]}.{c}".lower() for t in item.get("tables") or []
            for c in t.get("columns") or []}


def _joins(item: dict[str, Any]) -> set[str]:
    out = set()
    for j in (item.get("analysis") or {}).get("joins") or []:
        a = f"{(j.get('left_table') or '').split('.')[-1]}.{j.get('left_column', '')}".lower()
        b = f"{(j.get('right_table') or '').split('.')[-1]}.{j.get('right_column', '')}".lower()
        out.add(" = ".join(sorted([a, b])))
    return out


def compare_sql(gold: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    from .sqllibrary import normalise_sql
    if normalise_sql(gold["sql"]) == normalise_sql(other["sql"]):
        return {"same": True, "sections": [], "text_diff": "", "summary": "", "name_only": False}
    sections = [s for s in (
        _section("Tables", _table_names(gold), _table_names(other)),
        _section("Columns", _columns(gold), _columns(other)),
        _section("Joins", _joins(gold), _joins(other)),
        _section("Filters", filters(gold["sql"]), filters(other["sql"])),
    ) if s]
    if not sections:
        sections.append({"title": "Text", "text": "written differently"})
    text = _text_diff(gold["sql"], other["sql"], gold["name"], other["name"], sql=True)
    return {"same": False, "sections": sections, "text_diff": text, "summary": summary(sections), "name_only": False}


def compare(gold: dict[str, Any], other: dict[str, Any], lookup: dict | None = None) -> dict[str, Any]:
    if gold["kind"] == "measure":
        return compare_measures(gold, other, lookup)
    if gold["kind"] == "report":
        return compare_reports(gold, other)
    return compare_sql(gold, other)


def changed_since_marked(cat: Catalogue, gold_row: dict[str, Any], item: dict[str, Any] | None = None
                         ) -> dict[str, Any] | None:
    """How the gold standard itself changed since it was marked; None when it hasn't."""
    item = item or load(cat, gold_row["key"])
    snap = json.loads(gold_row.get("snapshot") or "null")
    if item is None or not snap or snap.get("fingerprint") == fingerprint(item):
        return None
    if item["kind"] == "measure":
        before = {**item, "expression": snap.get("expression", ""), "format": snap.get("format", ""), "where": "when marked"}
        result = compare_measures(before, {**item, "where": "now"})
    elif item["kind"] == "sql":
        result = {"sections": [{"title": "SQL", "text": "SQL changed"}],
                  "text_diff": _text_diff(snap.get("sql", ""), item["sql"], "when marked", "now", sql=True)}
    else:
        before, after = snap.get("content", {}), _report_content(item["attrs"])
        sections = []
        for facet in ("measures", "tables", "sources", "pages"):
            a, b = before.get(facet, {}), after.get(facet, {})
            s = _section(facet.title(), a, b, [n for n in set(a) & set(b) if a[n] != b[n]])
            if s:
                sections.append(s)
        result = {"sections": sections, "text_diff": ""}
    result["summary"] = summary(result["sections"]) or "changed"
    result["marked_at"] = gold_row.get("marked_at")
    return result


# ---- finding related items ------------------------------------------------------------------------

_CACHE: dict[tuple[str, str], tuple[int, Any]] = {}


def _cached(cat: Catalogue, name: str, make):
    """`make(cat)`, worked out once per catalogue revision: a measure or report page asks for every measure on
    each rerun, and reading them all again each time is slow on a big catalogue."""
    rev, where = cat.revision(), str(getattr(cat, "path", id(cat)))
    hit = _CACHE.get((where, name))
    if hit and hit[0] == rev:
        return hit[1]
    value = make(cat)
    _CACHE[(where, name)] = (rev, value)
    return value


def _measures(cat: Catalogue) -> list[dict[str, Any]]:
    """Every active measure in an active report, in load()'s shape (read once per catalogue revision)."""
    return _cached(cat, "measures", _read_measures)


def _read_measures(cat: Catalogue) -> list[dict[str, Any]]:
    reports = {r["key"]: r["name"] for r in cat.conn.execute(
        "SELECT key, name FROM assets WHERE kind = 'report' AND status = 'active'")}
    out = []
    for r in cat.conn.execute("SELECT key, name, parent, attrs FROM assets WHERE kind = 'measure' AND status = 'active'"):
        if r["parent"] not in reports:
            continue
        a = json.loads(r["attrs"] or "{}")
        expr = as_text(a.get("expression")).strip()
        if not expr:
            continue
        out.append({"key": r["key"], "kind": "measure", "name": (as_text(a.get("name")) or r["name"]).strip(),
                    "table": as_text(a.get("table")), "expression": expr, "format": as_text(a.get("format")),
                    "report_key": r["parent"], "where": reports[r["parent"]], "missing": False})
    return out


def _lookup(measures: list[dict[str, Any]]) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for m in measures:
        out.setdefault(m["report_key"], {})[m["name"].lower()] = m
    return out


def measure_standards(cat: Catalogue, measures: list[dict[str, Any]] | None = None
                      ) -> dict[str, list[dict[str, Any]]]:
    """measure name (lower) -> the gold measures with that name: the ones you marked, else those in gold
    reports. Each carries "gold_key" (the gold standard it comes from) and "via" ("marked" or "in a gold report")."""
    golds = all_golds(cat)
    measures = measures if measures is not None else _measures(cat)
    by_key = {m["key"]: m for m in measures}
    marked: dict[str, list[dict]] = {}
    via_report: dict[str, list[dict]] = {}
    for g in golds:
        if g["kind"] == "measure" and g["key"] in by_key:
            m = by_key[g["key"]]
            marked.setdefault(m["name"].lower(), []).append({**m, "gold_key": g["key"], "via": "marked"})
        elif g["kind"] == "report":
            for m in measures:
                if m["report_key"] == g["key"]:
                    via_report.setdefault(m["name"].lower(), []).append({**m, "gold_key": g["key"],
                                                                         "via": "in a gold report"})
    return {n: marked.get(n) or via_report.get(n, []) for n in set(marked) | set(via_report)}


def _verdict(result: dict[str, Any], ok: dict[str, Any] | None, other: dict[str, Any]) -> str:
    if result["same"]:
        return "same"
    if ok and ok["fingerprint"] == fingerprint(other):
        return "ok"
    return "name" if result.get("name_only") else "differs"


def related(cat: Catalogue, key: str, *, everything: bool = False) -> dict[str, Any]:
    """Everything compared with one gold standard: {"gold": item, "row": gold row, "changed": change or None,
    "related": [{"item", "result", "verdict" (same | differs | name | ok), "why", "ok"}], "measures": [...]
    (for a gold report: same-named measures elsewhere, compared with its own), "conflicts": [...],
    "scope": its scope, "left_out": {report keys related but outside the scope}}. `everything` ignores the
    scope (to list what can be picked)."""
    row = get(cat, key)
    gold = load(cat, key)
    sc = {"mode": "all", "skip": [], "picked": []} if everything else scope_of(row)
    out: dict[str, Any] = {"gold": gold, "row": row, "changed": None, "related": [], "measures": [], "conflicts": [],
                           "scope": sc, "left_out": set()}
    if gold is None or row is None:
        return out
    out["changed"] = changed_since_marked(cat, row, gold)
    oks = _oks(cat)
    old = old_reports(cat) if sc["mode"] == "current" else set()

    def keep(k: str) -> bool:
        if _keep(sc, k, old):
            return True
        out["left_out"].add(k)
        return False

    rel: list[dict[str, Any]] = []
    if gold["kind"] == "measure" and not gold["expression"]:
        pass  # a measure only seen in a visual: nothing to compare with
    elif gold["kind"] == "measure":
        measures = _measures(cat)
        lookup = _lookup(measures)
        gd = dax_key(gold["expression"])
        for m in measures:
            if m["key"] == key or m["report_key"] == gold["report_key"]:
                continue
            if m["name"].lower() == gold["name"].lower():
                why = "same name"
            elif dax_key(m["expression"]) == gd:
                why = "same DAX"
            else:
                continue
            if not keep(m["report_key"]):
                continue
            rel.append({"item": m, "why": why, "result": compare_measures(gold, m, lookup)})
    elif gold["kind"] == "report":
        rel = _related_reports(cat, gold, keep)
        out["measures"], out["conflicts"] = _report_measures(cat, gold, oks, keep)
    else:
        rel = [r for r in _related_sql(cat, gold) if keep(r["item"]["key"])]
    for r in rel:
        r["ok"] = oks.get((key, r["item"]["key"]))
        r["verdict"] = _verdict(r["result"], r["ok"], r["item"])
    order = {"differs": 0, "name": 1, "ok": 2, "same": 3}
    rel.sort(key=lambda r: (order[r["verdict"]], r["item"].get("where", "").lower(), r["item"]["name"].lower()))
    out["related"] = rel
    return out


def _related_reports(cat: Catalogue, gold: dict[str, Any], keep) -> list[dict[str, Any]]:
    from .groups import for_item
    from .reportversions import similarity
    versions = {m["asset_key"] for g in for_item(cat, gold["key"]) if g and g["type"] == "versions"
                for m in g["members"]}
    out = []
    for r in cat.conn.execute("SELECT key FROM assets WHERE kind = 'report' AND status = 'active' AND key != ?",
                              (gold["key"],)):
        k = r["key"]
        other = load(cat, k)
        if other is None:
            continue
        if k in versions:
            why = "another version"
        elif similarity(gold["attrs"], other["attrs"], "report") >= REPORT_SIMILAR:
            why = "looks like a copy"
        else:
            continue
        if keep(k):
            out.append({"item": other, "why": why, "result": compare_reports(gold, other)})
    return out


def _report_measures(cat: Catalogue, gold: dict[str, Any], oks: dict, keep
                     ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """For a gold report: measures with the same name as one of its measures in other reports, compared with
    the standard for that name; and names where gold standards disagree with each other."""
    measures = _measures(cat)
    lookup = _lookup(measures)
    standards = measure_standards(cat, measures)
    mine = {m["name"].lower() for m in measures if m["report_key"] == gold["key"]}
    rows, conflicts = [], []
    for n in sorted(mine):
        std = standards.get(n) or []
        if len({dax_key(s["expression"]) for s in std}) > 1:
            conflicts.append({"name": std[0]["name"], "standards": std})
    for m in measures:
        n = m["name"].lower()
        if n not in mine or m["report_key"] == gold["key"]:
            continue
        std = [s for s in standards.get(n, []) if s["report_key"] != m["report_key"]]
        if not std or m["key"] in {s["key"] for s in std} or not keep(m["report_key"]):
            continue
        res, verdict, s0 = _best(std, m, lookup, oks)
        rows.append({"item": m, "standard": s0, "result": res, "verdict": verdict, "why": "same name",
                     "ok": oks.get((s0["gold_key"], m["key"]))})
    rows.sort(key=lambda r: ({"differs": 0, "name": 1, "ok": 2, "same": 3}[r["verdict"]], r["item"]["name"].lower()))
    return rows, conflicts


def _best(std: list[dict], m: dict, lookup: dict, oks: dict) -> tuple[dict, str, dict]:
    """Compare with each standard; the first that matches wins, else the first standard."""
    first = None
    for s in std:
        res = compare_measures(s, m, lookup)
        verdict = _verdict(res, oks.get((s["gold_key"], m["key"])), m)
        if verdict in ("same", "ok"):
            return res, verdict, s
        if first is None:
            first = (res, verdict, s)
    return first  # type: ignore[return-value]


def _tokens(sql: str) -> list[str]:
    from .sqllibrary import normalise_sql
    return re.findall(r"[a-z_][\w.$#@]*|\d+|'[^']*'|[^\s\w]", normalise_sql(sql))[:MAX_TOKENS]


def alike(a_tokens: list[str], b_tokens: list[str]) -> float:
    """How alike two pieces of SQL read, 0 to 1 (word by word, ignoring case, spacing and comments)."""
    if not a_tokens or not b_tokens:
        return 0.0
    sa, sb = set(a_tokens), set(b_tokens)
    if len(sa & sb) / len(sa | sb) < 0.3:  # quick check first: too few words in common
        return 0.0
    return difflib.SequenceMatcher(None, a_tokens, b_tokens, autojunk=False).ratio()


def _sql_candidates(cat: Catalogue) -> list[dict[str, Any]]:
    """Every active query in the SQL library, shown as its view or procedure when that is the only place it's in."""
    from .sqllibrary import all_with_places
    out = []
    for q in all_with_places(cat):
        items = {p["item"] for p in q["places"]}
        key, name, where = f"query://{q['uid']}", q["name"], ", ".join(sorted(i.rsplit("/", 1)[-1] for i in items)[:3])
        if len(items) == 1 and q["places"][0]["location"].endswith((" definition",)):
            only = next(iter(items))
            a = cat.conn.execute("SELECT name, kind, source, attrs FROM assets WHERE key = ?", (only,)).fetchone()
            if a and a["kind"] in ("view", "procedure"):
                key, name = only, a["name"]
                where = f"{a['kind']} in {json.loads(a['attrs'] or '{}').get('database') or a['source']}"
        out.append({"key": key, "kind": "sql", "name": name, "where": where, "sql": q["sql"], "tables": q["tables"],
                    "analysis": q.get("analysis") or {}, "missing": False, "named_by": q["named_by"], "uid": q["uid"]})
    return out


def sql_related(gold: dict[str, Any], other: dict[str, Any], gold_tokens: list[str] | None = None) -> str | None:
    """Why a piece of SQL counts as related to a gold one ("same name", "same tables, reads alike", "reads alike"),
    or None."""
    if other.get("uid") and other.get("uid") == gold.get("uid"):
        return None
    if gold.get("named_by") == "you" and other["name"].strip().lower() == gold["name"].strip().lower():
        return "same name"
    gt, ot = _table_names(gold), _table_names(other)
    share = len(gt & ot) / len(gt | ot) if gt and ot else 0.0
    score = alike(gold_tokens if gold_tokens is not None else _tokens(gold["sql"]), _tokens(other["sql"]))
    if share >= SQL_TABLES and score >= SQL_ALIKE:
        return "same tables, reads alike"
    if score >= SQL_VERY_ALIKE:
        return "reads alike"
    return None


def _related_sql(cat: Catalogue, gold: dict[str, Any]) -> list[dict[str, Any]]:
    tokens = _tokens(gold["sql"])
    out = []
    for c in _sql_candidates(cat):
        if c["key"] == gold["key"]:
            continue
        why = sql_related(gold, c, tokens)
        if why:
            out.append({"item": c, "why": why, "result": compare_sql(gold, c)})
    return out


# ---- for one item's page --------------------------------------------------------------------------

def against(cat: Catalogue, key: str) -> list[dict[str, Any]]:
    """The gold standards an item (not itself gold) is compared with, and how it compares:
    [{"gold": gold item, "gold_key", "via", "why", "result", "verdict", "ok"}]."""
    golds = all_golds(cat)
    if not golds:
        return []
    item = load(cat, key)
    if item is None or (item["kind"] == "measure" and not item["expression"]):
        return []  # a measure only seen in a visual: its DAX isn't known
    oks = _oks(cat)
    rows = {g["key"]: g for g in golds}
    old = old_reports(cat)

    def in_scope(gold_key: str) -> bool:
        return _keep(scope_of(rows.get(gold_key)), compared_key(item), old)

    out = []
    if item["kind"] == "measure":
        measures = _measures(cat)
        lookup = _lookup(measures)
        standards = measure_standards(cat, measures).get(item["name"].lower(), [])
        std = [s for s in standards if s["report_key"] != item["report_key"] and in_scope(s["gold_key"])]
        if std and key not in {s["key"] for s in standards}:
            res, verdict, s0 = _best(std, item, lookup, oks)
            out.append({"gold": s0, "gold_key": s0["gold_key"], "via": s0["via"], "why": "same name", "result": res,
                        "verdict": verdict, "ok": oks.get((s0["gold_key"], key))})
        gd = dax_key(item["expression"])
        for g in golds:
            if g["kind"] != "measure" or g["key"] == key or any(o["gold_key"] == g["key"] for o in out) \
                    or not in_scope(g["key"]):
                continue
            gm = next((m for m in measures if m["key"] == g["key"]), None)
            if gm and gm["report_key"] != item["report_key"] and dax_key(gm["expression"]) == gd \
                    and gm["name"].lower() != item["name"].lower():
                res = compare_measures(gm, item, lookup)
                out.append({"gold": gm, "gold_key": g["key"], "via": "marked", "why": "same DAX", "result": res,
                            "verdict": _verdict(res, oks.get((g["key"], key)), item), "ok": oks.get((g["key"], key))})
    elif item["kind"] == "report":
        from .groups import for_item
        from .reportversions import similarity
        versions = {m["asset_key"] for g in for_item(cat, key) if g and g["type"] == "versions" for m in g["members"]}
        for g in golds:
            if g["kind"] != "report" or g["key"] == key or not in_scope(g["key"]):
                continue
            gr = load(cat, g["key"])
            if gr is None:
                continue
            if g["key"] in versions:
                why = "another version"
            elif similarity(gr["attrs"], item["attrs"], "report") >= REPORT_SIMILAR:
                why = "looks like a copy"
            else:
                continue
            res = compare_reports(gr, item)
            out.append({"gold": gr, "gold_key": g["key"], "via": "marked", "why": why, "result": res,
                        "verdict": _verdict(res, oks.get((g["key"], key)), item), "ok": oks.get((g["key"], key))})
    else:
        for g in golds:
            if g["kind"] != "sql" or g["key"] == key or not in_scope(g["key"]):
                continue
            gs = load(cat, g["key"])
            if gs is None or (gs.get("uid") and gs.get("uid") == item.get("uid")):
                continue
            why = sql_related(gs, item)
            if why:
                res = compare_sql(gs, item)
                out.append({"gold": gs, "gold_key": g["key"], "via": "marked", "why": why, "result": res,
                            "verdict": _verdict(res, oks.get((g["key"], key)), item), "ok": oks.get((g["key"], key))})
    return out


def gold_for(cat: Catalogue, key: str) -> dict[str, Any] | None:
    """For a measure in a gold report: that report's gold row (the measure counts as gold too)."""
    a = cat.conn.execute("SELECT kind, parent FROM assets WHERE key = ?", (key,)).fetchone()
    if a and a["kind"] == "measure" and a["parent"]:
        g = get(cat, a["parent"])
        if g and g["kind"] == "report":
            return g
    return None


# ---- the list of all gold standards ---------------------------------------------------------------

def overview(cat: Catalogue) -> list[dict[str, Any]]:
    """One row per gold standard: what it is, how many related items match and differ, whether it changed."""
    rows = []
    for g in all_golds(cat):
        r = related(cat, g["key"])
        item = r["gold"]
        rel = r["related"] + r["measures"]
        rows.append({"key": g["key"], "kind": g["kind"], "name": item["name"] if item else g["key"],
                     "where": item.get("where", "") if item else "", "note": g.get("note") or "",
                     "marked_at": g.get("marked_at"), "author": g.get("author"),
                     "gone": item is None or bool(item.get("missing")),
                     "related": len(rel), "differs": sum(x["verdict"] == "differs" for x in rel),
                     "name_only": sum(x["verdict"] == "name" for x in rel),
                     "ok": sum(x["verdict"] == "ok" for x in rel), "same": sum(x["verdict"] == "same" for x in rel),
                     "changed": r["changed"]["summary"] if r["changed"] else "", "conflicts": len(r["conflicts"]),
                     "scope": scope_label(r["scope"]), "left_out": len(r["left_out"]),
                     "details": r})
    rows.sort(key=lambda x: (-x["differs"], x["kind"], x["name"].lower()))
    return rows


def differences(cat: Catalogue) -> list[dict[str, Any]]:
    """Every item that differs from a gold standard (once per item), for the review list:
    [{"key", "name", "gold_key", "gold_name", "summary", "fingerprint"}]."""
    out: dict[str, dict[str, Any]] = {}
    for g in all_golds(cat):
        r = related(cat, g["key"])
        if r["gold"] is None:
            continue
        for x in r["related"] + r["measures"]:
            if x["verdict"] != "differs" or x["item"]["key"] in out:
                continue
            std = x.get("standard") or r["gold"]
            out[x["item"]["key"]] = {"key": x["item"]["key"], "name": x["item"]["name"],
                                     "where": x["item"].get("where", ""), "gold_key": std.get("gold_key", g["key"]),
                                     "gold_name": std["name"], "gold_where": std.get("where", ""),
                                     "summary": x["result"]["summary"], "fingerprint": fingerprint(x["item"])}
    return list(out.values())

