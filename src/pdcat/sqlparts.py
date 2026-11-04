"""Find the subqueries inside a SQL query, for drawing them: CTEs (`WITH recent AS (SELECT ...)`), derived
tables (`FROM (SELECT ...) x`), and subqueries in WHERE, SELECT, IN and EXISTS.

Each part knows the part it sits inside, the tables it reads itself (not those of the parts inside it),
the CTEs it uses and the columns it gives back. It is read from the text, so it works the same with or
without sqlglot, and only names tables the query's reading already found. Read-only: nothing is run.
"""
from __future__ import annotations

import re
from typing import Any

from .sqlrefs import _IDENT, _KEYWORDS, normalise

MAX_PARTS = 60
MAX_COLUMNS = 15

_HIDDEN = re.compile(r"/\*[\s\S]*?\*/|--[^\n]*|'(?:[^']|'')*'")
_STARTS_QUERY = re.compile(r"\s*\(*\s*(?:select|with)\b", re.I)
_CTE_BEFORE = re.compile(r"(?:\bwith|,)\s*(?P<h>(?P<n>\[[^\]]+\]|\w+)\s*(?:\([^()]*\))?)\s+as\s*$", re.I)
_FROM_BEFORE = re.compile(r"\b(?:from|join|apply)\s*$", re.I)
_EXISTS_BEFORE = re.compile(r"\b(?P<n>not\s+)?exists\s*$", re.I)
_IN_BEFORE = re.compile(r"\b(?P<n>not\s+)?in\s*$", re.I)
_CLAUSE = re.compile(r"\b(select|where|having|on|set|values|when|then|else)\b", re.I)
_ALIAS_AFTER = re.compile(r"\s*(?:as\s+)?(?P<a>\[[^\]]+\]|\w+)", re.I)
_TABLE = re.compile(rf"\b(?:from|join|update|into|merge\s+(?:into\s+)?|using)\s+(?P<t>{_IDENT})", re.I)
_PLACES = {"select": "in SELECT", "where": "in WHERE", "having": "in HAVING", "on": "in a join",
           "set": "in SET", "values": "in VALUES", "when": "in CASE", "then": "in CASE", "else": "in CASE"}


def _mask(sql: str) -> str:
    """Comments and the text inside quotes become spaces, so their brackets and words aren't read."""
    def hide(m: re.Match) -> str:
        t = m.group()
        if t.startswith("'"):
            return "'" + " " * (len(t) - 2) + "'"
        return re.sub(r"[^\n]", " ", t)  # same length, so places in the text match places in the SQL
    return _HIDDEN.sub(hide, sql)


def _pairs(text: str) -> list[tuple[int, int]]:
    out, stack = [], []
    for i, ch in enumerate(text):
        if ch == "(":
            stack.append(i)
        elif ch == ")" and stack:
            out.append((stack.pop(), i))
    return sorted(out)


def _blank(text: str, start: int, spans: list[tuple[int, int]]) -> str:
    """text (which starts at `start` in the whole SQL) with the given spans replaced by spaces."""
    chars = list(text)
    for a, b in spans:
        for i in range(max(a - start, 0), min(b - start + 1, len(chars))):
            chars[i] = " "
    return "".join(chars)


def _unq(name: str) -> str:
    return name.strip().strip("[]\"")


def _split_top(text: str) -> list[str]:
    out, depth, cur = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def _gives(body: str) -> list[str]:
    """The column names a query's first SELECT list gives back."""
    m = re.search(r"\bselect\b(?:\s+distinct\b)?(?:\s+top\s*\(?\s*\d+\s*\)?(?:\s+percent)?)?(?P<s>.*?)"
                  r"(?:\bfrom\b|\binto\b|$)", body, re.I | re.S)
    if not m:
        return []
    out: list[str] = []
    for item in _split_top(m["s"]):
        item = " ".join(item.split())
        if not item:
            continue
        name = None
        if a := re.search(r"\bas\s+(\[[^\]]+\]|\"[^\"]+\"|'[^']*'|\w+)\s*$", item, re.I):
            name = a.group(1)
        elif a := re.match(r"(\[[^\]]+\]|\w+)\s*=(?!=)", item):  # Qty = SUM(Units)
            name = a.group(1)
        elif re.fullmatch(r"(?:[\w\[\]]+\.)*(?:\[[^\]]+\]|\w+|\*)", item):
            name = item.split(".")[-1]
        elif a := re.search(r"\s(\[[^\]]+\]|\w+)$", item):  # SUM(Units) qty
            if a.group(1).lower() not in _KEYWORDS and not a.group(1).isdigit():
                name = a.group(1)
        if name:
            name = _unq(name).strip("'")
            if name not in out and not name.isdigit():
                out.append(name)
        if len(out) >= MAX_COLUMNS:
            break
    return out


def find_parts(sql: str, tables: list[str]) -> dict[str, Any]:
    """{"parts": [{"id", "kind" ("cte" | "derived" | "exists" | "in" | "subquery"), "name", "label", "parent",
    "tables", "uses" (CTE names), "gives" (columns), "links" (a derived table's joins: {"column", "other" alias,
    "other_column"}), "sql"}], "outer": {"tables", "uses"}}.
    `tables` are the names the query's reading found; only those are named as tables. Never raises."""
    try:
        return _find(sql or "", tables)
    except Exception:  # a drawing aid: never a reason to show nothing
        return {"parts": [], "outer": {"tables": [], "uses": []}}


def _find(sql: str, tables: list[str]) -> dict[str, Any]:
    text = _mask(sql)
    known = {t.lower() for t in tables}
    by_last: dict[str, list[str]] = {}
    for t in known:
        by_last.setdefault(t.split(".")[-1], []).append(t)
    found = []
    for a, b in _pairs(text):
        if not _STARTS_QUERY.match(text, a + 1):
            continue
        before = text[max(0, a - 300):a]
        if found and found[-1]["span"][0] < a and b < found[-1]["span"][1] and \
                text[found[-1]["span"][0] + 1:a].strip("( \t\r\n") == "":
            continue  # ((SELECT ...)): the outer brackets already count
        kind, name, head = "subquery", None, None
        if m := _CTE_BEFORE.search(before):
            off = a - len(before)
            kind, name, head = "cte", _unq(m["n"]), " ".join(sql[off + m.start("h"):off + m.end("h")].split())
        elif _FROM_BEFORE.search(before):
            kind = "derived"
            m = _ALIAS_AFTER.match(text, b + 1)
            if m and m["a"].lower() not in _KEYWORDS:
                name = _unq(m["a"])
        elif m := _EXISTS_BEFORE.search(before):
            kind, name = "exists", "NOT EXISTS" if m["n"] else "EXISTS"
        elif m := _IN_BEFORE.search(before):
            kind, name = "in", "NOT IN" if m["n"] else "IN"
        found.append({"span": (a, b), "kind": kind, "name": name, "head": head})
        if len(found) >= MAX_PARTS:
            break
    for i, p in enumerate(found):
        p["id"] = f"p{i + 1}"
        inside = [q for q in found if q["span"][0] < p["span"][0] and p["span"][1] < q["span"][1]]
        p["parent"] = max(inside, key=lambda q: q["span"][0])["id"] if inside else None
    ctes = {p["name"].lower() for p in found if p["kind"] == "cte" and p["name"]}

    def children(pid: str | None) -> list[tuple[int, int]]:
        return [q["span"] for q in found if q["parent"] == pid]

    def scope(body: str) -> tuple[list[str], list[str]]:
        names, uses = [], []
        for m in _TABLE.finditer(body):
            raw = m["t"]
            if "." not in raw and _unq(raw).lower() in ctes:
                if _unq(raw) not in uses:
                    uses.append(_unq(raw))
                continue
            name = normalise(raw) or normalise(raw, dynamic=True)
            if not name:
                continue
            hit = name if name in known else None
            if hit is None and len(by_last.get(name.split(".")[-1], [])) == 1 and "." not in name:
                hit = by_last[name.split(".")[-1]][0]
            if hit and hit not in names:
                names.append(hit)
        return names, uses

    bodies = {None: _blank(text, 0, children(None))}
    for p in found:
        bodies[p["id"]] = _blank(text[p["span"][0] + 1:p["span"][1]], p["span"][0] + 1, children(p["id"]))

    def links(name: str | None, where: str | None) -> list[dict[str, str]]:
        """x.Material = p.Material beside a derived table x: how it joins to the rest of its query."""
        if not name or not re.fullmatch(r"\w+", name):
            return []
        col = r"\[?(\w+)\]?"
        out = []
        for m in re.finditer(rf"(?<![\w.\]])\[?{re.escape(name)}\]?\.{col}\s*=\s*{col}\.{col}"
                             rf"|{col}\.{col}\s*=\s*\[?{re.escape(name)}\]?\.{col}(?![\w])", bodies[where], re.I):
            mine, other, theirs = (m[1], m[2], m[3]) if m[1] else (m[6], m[4], m[5])
            link = {"column": mine, "other": other, "other_column": theirs}
            if other.lower() != name.lower() and link not in out:
                out.append(link)
        return out

    parts = []
    for p in found:
        a, b = p["span"]
        body = bodies[p["id"]]
        names, uses = scope(body)
        if p["kind"] == "subquery":
            words = _CLAUSE.findall(_blank(text[:a], 0, [q["span"] for q in found if q["span"][1] < a]))
            place = _PLACES.get(words[-1].lower()) if words else None
            p["name"] = None
            label = "Subquery " + (place or "")
        else:
            label = {"cte": "CTE", "derived": "Derived table", "exists": "", "in": ""}[p["kind"]]
            label = f"{label} {p['name'] or ''}".strip() if p["kind"] in ("cte", "derived") else \
                f"{p['name']} subquery"
        parts.append({"id": p["id"], "kind": p["kind"], "name": p["name"], "label": label.strip(),
                      "parent": p["parent"], "tables": names, "uses": [u for u in uses if u.lower() !=
                                                                      (p["name"] or "").lower()],
                      "gives": _gives(body), "links": links(p["name"], p["parent"]) if p["kind"] == "derived" else [],
                      "sql": " ".join(sql[a + 1:b].split())[:2000], "span": [a + 1, b], "head": p["head"]})
    seen: dict[str, int] = {}
    for p in parts:  # two "IN subquery" parts: the second is "IN subquery (2)"
        seen[p["label"]] = seen.get(p["label"], 0) + 1
        if seen[p["label"]] > 1:
            p["label"] += f" ({seen[p['label']]})"
    outer_names, outer_uses = scope(bodies[None])
    return {"parts": parts, "outer": {"tables": outer_names, "uses": outer_uses}}


_DEFINED_AFTER = re.compile(r"\)\s*(?:as\s+)?(\w+)", re.I)
_USED = re.compile(r"(?<![\w.\]\[])\[?(\w+)\]?\.\[?\w+\]?(?!\s*\.)")


def standalone(sql: str, tables: list[str], pid: str) -> dict[str, Any] | None:
    """One subquery as SQL of its own: its text, with the CTEs it uses (and the CTEs those use) put in front
    as a WITH, so it reads on its own. {"label", "sql", "ctes" (names put in front), "outer" (aliases of the
    outer query it still refers to, e.g. a correlated subquery's `p`), "part"}. None if there's no such part."""
    found = find_parts(sql or "", tables)
    parts = {p["id"]: p for p in found["parts"]}
    part = parts.get(pid)
    if part is None:
        return None
    ctes = {p["name"].lower(): p for p in found["parts"] if p["kind"] == "cte" and p["name"]}
    need: list[str] = []

    def add(names: list[str]) -> None:
        for n in names:
            c = ctes.get(n.lower())
            if c and c["id"] not in need and c["id"] != pid:
                add(c["uses"])
                need.append(c["id"])

    add(part["uses"])
    for p in found["parts"]:  # CTEs used by subqueries nested inside this one
        up = p["parent"]
        while up and up != pid:
            up = parts[up]["parent"]
        if up == pid:
            add(p["uses"])
    need.sort(key=lambda i: parts[i]["span"][0])
    body = sql[part["span"][0]:part["span"][1]].strip()
    body = "\n".join(line.rstrip() for line in body.splitlines())
    if need:
        head = "WITH " + ",\n".join(f"{parts[i]['head'] or parts[i]['name']} AS (\n    "
                                    + " ".join(sql[parts[i]['span'][0]:parts[i]['span'][1]].split()) + "\n)"
                                    for i in need)
        if re.match(r"\s*with\b", body, re.I):
            body = head + ",\n" + re.sub(r"^\s*with\b\s*", "", body, flags=re.I)
        else:
            body = head + "\n" + body
    masked = _mask(body)
    defined = set()
    for m in _TABLE.finditer(masked):
        defined |= {_unq(x).lower() for x in re.split(r"\s*\.\s*", m["t"])}
        if (a := _ALIAS_AFTER.match(masked, m.end())) and a["a"].lower() not in _KEYWORDS:
            defined.add(_unq(a["a"]).lower())
    defined |= {m[1].lower() for m in _DEFINED_AFTER.finditer(masked)}
    outer = []
    for m in _USED.finditer(masked):
        if m[1].lower() not in defined and m[1] not in outer and not m[1].isdigit():
            outer.append(m[1])
    return {"label": part["label"], "sql": body, "ctes": [parts[i]["name"] for i in need], "outer": outer,
            "part": part}
