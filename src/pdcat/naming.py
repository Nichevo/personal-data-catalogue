"""Naming rules for a database or a Python project: pasted as text, or worked out from existing names.

Paste the rules as written (from an email, say). The text is kept as it is, and each line the app
recognises becomes a check you confirm or edit: prefixes (`vw_` for views), case (PascalCase,
snake_case), endings (`...ID`, date columns ending in `Date`), no spaces, no reserved words,
maximum length, which schema things belong in. Lines it can't turn into a check stay as text.

The audit runs each confirmed rule over the database's tables, views, procedures and columns (or a
project's scripts and the files they write): how many pass, which don't, with agreed exceptions.
New objects that break a rule go on To review, and the results export to CSV.

Rules can also be worked out from what's there: the habits most names follow ("92% of views start
with vw_") are suggested as rules, with how many follow them and examples of the ones that don't.
"""
from __future__ import annotations

import csv
import io
import json
import re
import uuid
from collections import Counter
from typing import Any

from . import suggestions
from .store import Catalogue, now

APPLIES = ("table", "view", "procedure", "column", "script", "file")
LABELS = {"table": "tables", "view": "views", "procedure": "procedures", "column": "columns", "script": "scripts",
          "file": "output files"}
CASES = {
    "pascal": (re.compile(r"^[A-Z][A-Za-z0-9]*$"), "PascalCase"),
    "camel": (re.compile(r"^[a-z]+[A-Z0-9][A-Za-z0-9]*$|^[a-z][a-z0-9]*$"), "camelCase"),
    "snake": (re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$"), "snake_case"),
    "upper_snake": (re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$"), "UPPER_SNAKE_CASE"),
}
RESERVED = {
    "add", "all", "alter", "and", "any", "as", "asc", "backup", "begin", "between", "by", "case", "check", "column",
    "commit", "constraint", "create", "cross", "current", "database", "default", "delete", "desc", "distinct",
    "drop", "else", "end", "exec", "execute", "exists", "file", "for", "foreign", "from", "full", "function",
    "grant", "group", "having", "identity", "in", "index", "inner", "insert", "into", "is", "join", "key", "left",
    "like", "merge", "not", "null", "of", "on", "open", "or", "order", "outer", "percent", "plan", "primary",
    "procedure", "public", "read", "references", "right", "rule", "schema", "select", "set", "table", "then", "to",
    "top", "transaction", "trigger", "union", "unique", "update", "user", "values", "view", "when", "where",
    "while", "with",
}
DATE_TYPES = ("date", "datetime", "datetime2", "smalldatetime", "datetimeoffset", "timestamp")
MIN_NAMES = 5        # habits are only worked out from at least this many names
HABIT_SHARE = 0.8    # ...and when at least this share follow them

_OBJECT_WORDS = [
    ("procedure", r"stored\s+procedures?|procedures?|procs?|sprocs?|\bsps?\b"),
    ("view", r"views?"),
    ("column", r"columns?|fields?"),
    ("table", r"tables?"),
    ("script", r"scripts?|python\s+files?|\.py\b|modules?"),
    ("file", r"output\s+files?|files?|csvs?|extracts?"),
]


# ---- reading pasted rules ----------------------------------------------

def _objects(line: str) -> list[str]:
    low = line.lower()
    found = []
    for name, rx in _OBJECT_WORDS:
        if re.search(rf"\b(?:{rx})", low) and name not in found:
            found.append(name)
            if name in ("procedure", "view") and "table" in found:
                found.remove("table")
    # "date columns", "ID fields" are about columns even though "table" may appear too.
    if "column" in found:
        return ["column"]
    return found


def parse_rules(text: str) -> list[dict[str, Any]]:
    """Turn pasted rules into checks: [{"applies_to", "check_type", "value", "text"}]. Lines that
    aren't recognised come back with check_type "text" so they're kept as written."""
    out = []
    for raw in re.split(r"[\r\n]+|(?<=[.;])\s+(?=[A-Z])", text or ""):
        line = raw.strip(" \t-*•·").strip()
        if not line or len(line) < 4:
            continue
        objs = _objects(line)
        low = line.lower()
        rules: list[tuple[str, str, str | None]] = []
        token = re.search(r"[`'\"“‘]([^`'\"”’]+)[`'\"”’]", line)
        tok = token.group(1).strip() if token else None
        prefix_word = re.search(r"\b([A-Za-z]{1,6}_)(?=\W|$)", line)
        if re.search(r"\b(prefix(?:ed|es)?|start(?:s|ing)?\s+with|begin(?:s|ning)?\s+with)\b", low) or (
                prefix_word and re.search(r"\b(for|on)\s+(?:all\s+)?(views?|tables?|procedures?|procs?)", low)):
            value = tok or (prefix_word.group(1) if prefix_word else None)
            if value:
                rules.append(("prefix", value, None))
        m = re.search(r"\b(?:end(?:s|ing)?\s+(?:with|in)|suffix(?:ed)?\s+(?:with|by)?)\s+[`'\"“]?([A-Za-z_]+)", line)
        if m:
            value = tok or m.group(1)
            if re.search(r"\bdates?\b", low) and "column" in objs or re.search(r"\bdate\s+(columns?|fields?)", low):
                rules.append(("type_suffix", f"date:{value}", "column"))
            elif re.search(r"\b(id|key)\s+(columns?|fields?)\b", low):
                rules.append(("id_suffix", value, "column"))
            else:
                rules.append(("suffix", value, None))
        for case, (_, label) in CASES.items():
            words = {"pascal": r"pascal\s*case", "camel": r"camel\s*case", "snake": r"snake[\s_]*case",
                     "upper_snake": r"upper[\s_]*(?:snake[\s_]*)?case|all\s+caps|capitals"}[case]
            if re.search(words, low):
                rules.append(("case", case, None))
                break
        else:
            if re.search(r"\blower\s*case\b", low):
                rules.append(("case", "snake", None))
        if re.search(r"\bno\s+spaces\b|\bwithout\s+spaces\b|\bspaces\s+(?:are\s+)?not\s+allowed", low):
            rules.append(("no_spaces", "", None))
        if re.search(r"\breserved\s+(?:key)?words?\b", low):
            rules.append(("no_reserved", "", None))
        m = re.search(r"\b(?:max(?:imum)?|no\s+(?:longer|more)\s+than|at\s+most|up\s+to)\s*(?:of\s+)?(\d{1,3})\s*"
                      r"(?:char|letter)", low)
        if m:
            rules.append(("max_length", m.group(1), None))
        m = re.search(r"\b(?:in|under|belong\s+(?:in|to))\s+(?:the\s+)?[`'\"“]?(\w+)[`'\"”]?\s+schema\b|\bschema\s+"
                      r"[`'\"“]?(\w+)", line, re.I)
        if m and "schema" in low:
            rules.append(("schema", (m.group(1) or m.group(2)).lower(), None))
        if not rules:
            out.append({"applies_to": objs[0] if objs else "table", "check_type": "text", "value": "", "text": line})
            continue
        for check, value, forced in rules:
            default = ["table", "view", "procedure"] if check == "schema" else ["table"]
            targets = [forced] if forced else (objs or default)
            for applies in targets:
                if check == "schema" and applies in ("column", "script", "file"):
                    continue
                out.append({"applies_to": applies, "check_type": check, "value": value, "text": line})
    return out


def describe(rule: dict[str, Any]) -> str:
    what, check, value = LABELS.get(rule["applies_to"], rule["applies_to"]), rule["check_type"], rule.get("value")
    if check == "prefix":
        return f"{what.capitalize()} start with `{value}`"
    if check == "suffix":
        return f"{what.capitalize()} end with `{value}`"
    if check == "type_suffix":
        return f"Date {what} end with `{value.split(':', 1)[1]}`"
    if check == "id_suffix":
        return f"ID {what} end with `{value}`"
    if check == "case":
        return f"{what.capitalize()} are {CASES[value][1]}"
    if check == "no_spaces":
        return f"No spaces in {what}"
    if check == "no_reserved":
        return f"No reserved words as {what}"
    if check == "max_length":
        return f"{what.capitalize()} are at most {value} characters"
    if check == "schema":
        return f"{what.capitalize()} are in the `{value}` schema"
    return rule.get("text") or ""


# ---- stored rules -------------------------------------------------------

def save_text(cat: Catalogue, scope: str, text: str) -> list[dict[str, Any]]:
    """Keep the pasted text as written and add the checks it holds as proposed rules (to confirm)."""
    texts = cat.setting("naming_text", {})
    texts[scope] = text
    cat.set_setting("naming_text", texts)
    found = parse_rules(text)
    have = {(r["applies_to"], r["check_type"], r["value"] or "") for r in rules(cat, scope, all_states=True)}
    added = []
    with cat.conn:
        for r in found:
            if (r["applies_to"], r["check_type"], r["value"] or "") in have:
                continue
            have.add((r["applies_to"], r["check_type"], r["value"] or ""))
            uid = add_rule(cat, scope, r["applies_to"], r["check_type"], r["value"], r["text"],
                           state="text" if r["check_type"] == "text" else "proposed", commit=False)
            added.append({**r, "uid": uid})
    cat.changed()
    return added


def pasted_text(cat: Catalogue, scope: str) -> str:
    return cat.setting("naming_text", {}).get(scope, "")


def add_rule(cat: Catalogue, scope: str, applies_to: str, check_type: str, value: str | None, text: str = "",
             *, state: str = "active", commit: bool = True) -> str:
    uid = uuid.uuid4().hex[:12]
    cat.conn.execute("INSERT INTO naming_rules (uid, scope, applies_to, check_type, value, text, state, created_at, "
                     "author) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (uid, scope, applies_to, check_type, value or "", text, state, now(), cat.author()))
    if commit:
        cat.conn.commit()
        cat.changed()
    return uid


def update_rule(cat: Catalogue, uid: str, **fields: Any) -> None:
    allowed = {k: v for k, v in fields.items() if k in ("applies_to", "check_type", "value", "text", "state")}
    if not allowed:
        return
    with cat.conn:
        cat.conn.execute(f"UPDATE naming_rules SET {', '.join(f'{k} = ?' for k in allowed)} WHERE uid = ?",
                         (*allowed.values(), uid))
    cat.changed()


def delete_rule(cat: Catalogue, uid: str) -> None:
    with cat.conn:
        cat.conn.execute("DELETE FROM naming_rules WHERE uid = ?", (uid,))
        cat.conn.execute("DELETE FROM naming_exceptions WHERE rule_uid = ?", (uid,))
    cat.changed()


def rules(cat: Catalogue, scope: str | None = None, *, all_states: bool = False) -> list[dict[str, Any]]:
    where, params = [], []
    if scope:
        where.append("scope = ?")
        params.append(scope)
    if not all_states:
        where.append("state = 'active'")
    return [dict(r) for r in cat.conn.execute(
        "SELECT * FROM naming_rules" + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY applies_to, check_type, created_at", params)]


def scopes(cat: Catalogue) -> list[str]:
    return [r[0] for r in cat.conn.execute("SELECT DISTINCT scope FROM naming_rules WHERE state = 'active'")]


def add_exception(cat: Catalogue, rule_uid: str, asset_key: str, column: str = "", reason: str = "") -> None:
    with cat.conn:
        cat.conn.execute("INSERT OR REPLACE INTO naming_exceptions (rule_uid, asset_key, column_name, reason) "
                         "VALUES (?, ?, ?, ?)", (rule_uid, asset_key, column or "", reason))
    cat.changed()


def remove_exception(cat: Catalogue, rule_uid: str, asset_key: str, column: str = "") -> None:
    with cat.conn:
        cat.conn.execute("DELETE FROM naming_exceptions WHERE rule_uid = ? AND asset_key = ? AND column_name = ?",
                         (rule_uid, asset_key, column or ""))
    cat.changed()


# ---- what the rules are checked against -------------------------------------

def names_in(cat: Catalogue, scope: str) -> dict[str, list[dict[str, Any]]]:
    """applies_to -> [{"key", "name", "column", "schema", "data_type", "first_seen"}] in a database or project."""
    out: dict[str, list[dict[str, Any]]] = {a: [] for a in APPLIES}
    from .curation import hidden_keys
    hidden = hidden_keys(cat)
    rows = cat.conn.execute(
        "SELECT key, kind, name, attrs, first_seen FROM assets WHERE status = 'active' AND (parent = ? OR "
        "substr(key, 1, ?) = ?)", (scope, len(scope) + 1, scope + "/")).fetchall()
    keys = []
    for r in rows:
        if r["key"] in hidden:
            continue
        a = json.loads(r["attrs"])
        if r["kind"] in ("table", "view", "procedure"):
            name = r["name"].split(".")[-1].strip("[]")
            out[r["kind"]].append({"key": r["key"], "name": name, "column": "", "schema": a.get("schema", ""),
                                   "first_seen": r["first_seen"]})
            keys.append(r["key"])
        elif r["kind"] in ("script", "notebook"):
            stem = r["name"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
            out["script"].append({"key": r["key"], "name": stem, "column": "", "first_seen": r["first_seen"]})
    if keys:
        marks = ", ".join("?" * len(keys))
        for c in cat.conn.execute(f"SELECT c.asset_key, c.name, c.data_type, a.first_seen FROM columns c JOIN assets a "
                                  f"ON a.key = c.asset_key WHERE c.asset_key IN ({marks}) AND a.kind != 'procedure'",
                                  keys):
            out["column"].append({"key": c["asset_key"], "name": c["name"], "column": c["name"],
                                  "data_type": (c["data_type"] or "").lower(), "first_seen": c["first_seen"]})
    if scope.startswith("py://"):
        for r in cat.conn.execute(
                "SELECT DISTINCT a.key, a.name, a.first_seen FROM links l JOIN assets a ON a.key = l.dst_key WHERE "
                "l.relation = 'writes' AND substr(l.src_key, 1, ?) = ? AND a.status = 'active'",
                (len(scope) + 1, scope + "/")):
            stem = r["name"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
            out["file"].append({"key": r["key"], "name": stem, "column": "", "first_seen": r["first_seen"]})
    return out


def _strip_prefixes(name: str, prefixes: list[str]) -> str:
    for p in sorted(prefixes, key=len, reverse=True):
        if name.lower().startswith(p.lower()):
            return name[len(p):]
    return name


def passes(rule: dict[str, Any], item: dict[str, Any], prefixes: list[str] | None = None) -> bool | None:
    """Whether one name follows a rule; None when the rule doesn't apply to it."""
    name, check, value = item["name"], rule["check_type"], rule.get("value") or ""
    if check == "prefix":
        return name.lower().startswith(value.lower())
    if check == "suffix":
        return name.endswith(value)  # case matters: "StoreID" doesn't follow "...Id"
    if check == "type_suffix":
        end = value.split(":", 1)[1] if ":" in value else value
        if not any(t in (item.get("data_type") or "") for t in DATE_TYPES):
            return None
        return name.endswith(end)
    if check == "id_suffix":
        if not re.search(r"id$", name, re.I):
            return None
        return name.endswith(value)
    if check == "case":
        bare = _strip_prefixes(name, prefixes or [])
        return bool(CASES[value][0].match(bare)) if bare else True
    if check == "no_spaces":
        return " " not in name
    if check == "no_reserved":
        return name.lower() not in RESERVED
    if check == "max_length":
        return len(name) <= int(value or 128)
    if check == "schema":
        return item["schema"].lower() == value if "schema" in item else None
    return None


def audit(cat: Catalogue, scope: str) -> list[dict[str, Any]]:
    """Each active rule with how many names pass, the ones that don't (minus exceptions), and the exceptions."""
    names = names_in(cat, scope)
    active = rules(cat, scope)
    exceptions: dict[str, set[tuple[str, str]]] = {}
    for e in cat.conn.execute("SELECT rule_uid, asset_key, column_name FROM naming_exceptions"):
        exceptions.setdefault(e[0], set()).add((e[1], e[2]))
    out = []
    for r in active:
        prefixes = [x["value"] for x in active if x["check_type"] == "prefix" and x["applies_to"] == r["applies_to"]]
        ok, bad, excused = 0, [], []
        for item in names.get(r["applies_to"], []):
            result = passes(r, item, prefixes)
            if result is None:
                continue
            if result:
                ok += 1
            elif (item["key"], item["column"]) in exceptions.get(r["uid"], set()):
                excused.append(item)
            else:
                bad.append(item)
        out.append({**r, "label": describe(r), "passed": ok, "failed": bad, "exceptions": excused,
                    "checked": ok + len(bad) + len(excused)})
    return out


def audit_csv(cat: Catalogue, scope: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["rule", "rule as written", "object", "column", "result"])
    for r in audit(cat, scope):
        for item in r["failed"]:
            w.writerow([r["label"], r["text"], item["key"], item["column"], "breaks the rule"])
        for item in r["exceptions"]:
            w.writerow([r["label"], r["text"], item["key"], item["column"], "agreed exception"])
        w.writerow([r["label"], r["text"], "", "", f"{r['passed']} of {r['checked']} pass"])
    return buf.getvalue()


def review_tasks(cat: Catalogue, task) -> list:
    """New objects (first seen after the rule was added) that break a rule: one item per rule."""
    out = []
    for scope in scopes(cat):
        for r in audit(cat, scope):
            new = [i for i in r["failed"] if (i.get("first_seen") or "") > (r["created_at"] or "")]
            if not new:
                continue
            key = new[0]["key"] if len({i["key"] for i in new}) == 1 else scope
            names = sorted({f"{i['name']}" for i in new})
            out.append(task("naming", key, f"{len(new)} new name(s) break “{r['label']}”",
                            ", ".join(names[:8]) + (" …" if len(names) > 8 else "")
                            + ". Rename them, or mark them as agreed exceptions in the naming rules audit.",
                            version=f"{r['uid']}:{len(new)}"))
    return out


# ---- habits worked out from existing names ------------------------------

def _case_of(name: str) -> str | None:
    for case in ("snake", "upper_snake", "pascal", "camel"):
        if CASES[case][0].match(name):
            return case
    return None


def habits(names: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Rules most names already follow, with how many follow them and examples of the ones that don't."""
    found = []
    for applies, items in names.items():
        if len(items) < MIN_NAMES:
            continue
        total = len(items)
        prefixes: list[str] = []
        if applies in ("table", "view", "procedure"):
            heads = Counter(m.group(1).lower() for i in items if (m := re.match(r"^([A-Za-z]{1,5}_)", i["name"])))
            if heads:
                head, n = heads.most_common(1)[0]
                if n / total >= HABIT_SHARE:
                    sample = next(i["name"][:len(head)] for i in items if i["name"].lower().startswith(head))
                    prefixes.append(sample)
                    found.append(_habit(applies, "prefix", sample, items, prefixes))
        cases = Counter(_case_of(_strip_prefixes(i["name"], prefixes)) for i in items)
        cases.pop(None, None)
        if cases:
            case, n = cases.most_common(1)[0]
            # snake and camel overlap on single lower-case words; only count a habit that's clearly there
            if n / total >= HABIT_SHARE:
                found.append(_habit(applies, "case", case, items, prefixes))
        if applies == "column":
            dates = [i for i in items if any(t in (i.get("data_type") or "") for t in DATE_TYPES)]
            if len(dates) >= MIN_NAMES:
                ends = Counter(m.group(0) for i in dates if (m := re.search(r"(Date|_date|_dt|Dt|DATE)$", i["name"])))
                if ends:
                    end, n = ends.most_common(1)[0]
                    if n / len(dates) >= HABIT_SHARE:
                        found.append(_habit(applies, "type_suffix", f"date:{end}", items, prefixes))
            ids = [i for i in items if re.search(r"id$", i["name"], re.I)]
            if len(ids) >= MIN_NAMES:
                ends = Counter(m.group(0) for i in ids if (m := re.search(r"(_id|_ID|ID|Id|id)$", i["name"])))
                end, n = ends.most_common(1)[0]
                if n / len(ids) >= HABIT_SHARE:
                    found.append(_habit(applies, "id_suffix", end, items, prefixes))
    return found


def _habit(applies: str, check: str, value: str, items: list[dict[str, Any]], prefixes: list[str]) -> dict[str, Any]:
    rule = {"applies_to": applies, "check_type": check, "value": value}
    results = [(i, passes(rule, i, prefixes)) for i in items]
    checked = [(i, ok) for i, ok in results if ok is not None]
    follows = sum(1 for _, ok in checked if ok)
    return {**rule, "follows": follows, "total": len(checked),
            "examples": [i["name"] for i, ok in checked if not ok][:8]}


def derive_all(cat: Catalogue) -> int:
    """Suggest the habits each database and Python project's names follow as naming rules."""
    found = []
    targets = [r[0] for r in cat.conn.execute("SELECT key FROM assets WHERE kind IN ('database', 'project') "
                                              "AND status = 'active'")]
    for scope in targets:
        have = {(r["applies_to"], r["check_type"], r["value"]) for r in rules(cat, scope, all_states=True)}
        for h in habits(names_in(cat, scope)):
            if (h["applies_to"], h["check_type"], h["value"]) in have:
                continue
            share = round(100 * h["follows"] / h["total"]) if h["total"] else 0
            title = f"{describe(h)} ({share}% do)"
            detail = f"{h['follows']} of {h['total']} {LABELS[h['applies_to']]} follow this."
            if h["examples"]:
                detail += " Ones that don't: " + ", ".join(h["examples"])
            found.append({"id": suggestions.make_id("naming", scope, f"{h['applies_to']}:{h['check_type']}:{h['value']}"),
                          "subject": scope, "title": title, "detail": detail, "score": share,
                          "payload": {"scope": scope, **{k: h[k] for k in ("applies_to", "check_type", "value")}}})
    return suggestions.sync(cat, "naming", found)


@suggestions.handler("naming")
def _accept(cat: Catalogue, p: dict[str, Any]) -> None:
    add_rule(cat, p["scope"], p["applies_to"], p["check_type"], p.get("value"),
             f"Worked out from existing names: {describe(p)}")
