"""Checks on the SQL in the query library: things that can give wrong results, run slowly, or are risky.

Each check is a simple pattern on the SQL text (comments and string contents are ignored where it
matters), so it can be wrong; a finding you mark "fine here" is not raised again for that query.
Every check says why it matters and links to the matching SQL Server article in Microsoft's docs
(the SQL Server good-practice starter pack in Knowledge carries the same topics).

Wrong-result and risky findings go on the To review list, one item per query; slow-pattern
findings show on the query's page.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from .sqlrefs import strip_comments
from .store import Catalogue, now

DOCS = "https://learn.microsoft.com/en-us/sql/"
SEVERITY_ICONS = {"wrong": "❗", "risky": "⚠️", "slow": "🐢"}
SEVERITY_LABELS = {"wrong": "may give wrong results", "risky": "risky", "slow": "may be slow"}

# rule -> (severity, title, why, article)
RULES: dict[str, tuple[str, str, str, str]] = {
    "nolock": ("wrong", "Reads with NOLOCK",
               "NOLOCK (READ UNCOMMITTED) can read rows twice, skip rows, or read changes that are later rolled "
               "back, so totals can be off while the table is being written.",
               DOCS + "t-sql/queries/hints-transact-sql-table"),
    "null_compare": ("wrong", "Compares with = NULL",
                     "`= NULL` and `<> NULL` are never true. Use IS NULL or IS NOT NULL.",
                     DOCS + "t-sql/queries/is-null-transact-sql"),
    "not_in_subquery": ("wrong", "NOT IN with a subquery",
                        "If the subquery returns any NULL, NOT IN returns no rows at all. NOT EXISTS is safer.",
                        DOCS + "t-sql/language-elements/in-transact-sql"),
    "left_join_where": ("wrong", "LEFT JOIN filtered in WHERE",
                        "A WHERE condition on the right-hand table of a LEFT JOIN drops the rows with no match, "
                        "so it behaves like an INNER JOIN. Move the condition into the ON clause if you meant "
                        "to keep them.", DOCS + "relational-databases/performance/joins"),
    "top_without_order": ("wrong", "TOP without ORDER BY",
                          "Without ORDER BY, which rows TOP returns can change from run to run.",
                          DOCS + "t-sql/queries/top-transact-sql"),
    "between_datetime": ("wrong", "BETWEEN on dates",
                         "BETWEEN '2026-01-01' AND '2026-01-31' misses anything after midnight on the last day "
                         "when the column has times. `>= start AND < next day` is safer.",
                         DOCS + "t-sql/language-elements/between-transact-sql"),
    "comma_join": ("wrong", "Old-style comma join",
                   "FROM a, b with the join in WHERE is easy to get wrong: a missing condition multiplies rows. "
                   "Use explicit JOIN ... ON.", DOCS + "relational-databases/performance/joins"),
    "distinct_join": ("wrong", "SELECT DISTINCT over joins",
                      "DISTINCT can hide a join that multiplies rows. Check the join keys give one match.",
                      DOCS + "t-sql/queries/select-transact-sql"),
    "join_no_condition": ("wrong", "JOIN without ON",
                          "A join with no join condition pairs every row with every row. Add ON, or write CROSS "
                          "JOIN if that's what you mean.", DOCS + "relational-databases/performance/joins"),
    "divide_no_guard": ("wrong", "Division without a zero guard",
                        "Dividing by a column fails (or gives NULL with ANSI settings off) when it's 0. "
                        "NULLIF(x, 0) avoids the error.", DOCS + "t-sql/functions/nullif-transact-sql"),
    "non_unique_join": ("wrong", "Join on columns that aren't unique",
                        "Neither side of the join is unique (from the profiles), so rows multiply.",
                        DOCS + "relational-databases/performance/joins"),
    "type_mismatch": ("wrong", "Join compares different types",
                      "The joined columns have different data types, so SQL Server converts one side: values can "
                      "fail to match, and indexes may not be used.",
                      DOCS + "t-sql/data-types/data-type-conversion-database-engine"),
    "missing_object": ("wrong", "Table not found",
                       "This table isn't in the scanned database any more (or never was). It may have been dropped "
                       "or renamed.", DOCS + "t-sql/statements/drop-table-transact-sql"),
    "deprecated_object": ("wrong", "Uses a table marked deprecated",
                          "The table is marked deprecated or retired in the catalogue; see its page for what replaces "
                          "it.", DOCS + "t-sql/statements/drop-table-transact-sql"),
    "union_not_all": ("slow", "UNION without ALL",
                      "UNION removes duplicates, which means sorting everything. UNION ALL is faster when there "
                      "can't be duplicates or they don't matter.", DOCS + "t-sql/language-elements/set-operators-union-transact-sql"),
    "hard_coded_date": ("risky", "Hard-coded date",
                        "A fixed date in the SQL goes stale. Use a parameter or a date worked out from today.",
                        DOCS + "t-sql/functions/date-and-time-data-types-and-functions-transact-sql"),
    "missing_schema": ("risky", "Table without a schema name",
                       "Without the schema (dbo.), SQL Server looks the name up in the user's default schema first, "
                       "which can pick a different table and stops plans being reused.",
                       DOCS + "relational-databases/security/authentication-access/ownership-and-user-schema-separation"),
    "function_on_column": ("slow", "Function on a column in WHERE or JOIN",
                           "Wrapping a column in a function (YEAR(), CONVERT(), ISNULL()...) stops SQL Server using "
                           "an index on it. Compare the bare column with a range instead.",
                           DOCS + "relational-databases/sql-server-index-design-guide"),
    "leading_wildcard": ("slow", "LIKE starting with %",
                         "LIKE '%text' can't use an index, so the whole table is read.",
                         DOCS + "t-sql/language-elements/like-transact-sql"),
    "select_star": ("slow", "SELECT *",
                    "Reads every column, including ones you don't need, and changes shape when the table does.",
                    DOCS + "t-sql/queries/select-transact-sql"),
    "cursor": ("slow", "Cursor",
               "Cursors work row by row. A set-based query is usually much faster.",
               DOCS + "t-sql/language-elements/declare-cursor-transact-sql"),
    "built_from_variables": ("risky", "SQL built from variables",
                             "The SQL text is put together from variables (an f-string, + or .format). If any value "
                             "can come from outside, that's SQL injection; pass values as parameters instead.",
                             DOCS + "relational-databases/security/sql-injection"),
    "dynamic_sql": ("risky", "Dynamic SQL (EXEC of a string)",
                    "EXEC(@sql) or sp_executesql with joined-up text runs whatever the string holds. "
                    "Use parameters for values.", DOCS + "relational-databases/security/sql-injection"),
    "no_where_change": ("risky", "UPDATE or DELETE without WHERE",
                        "Changes every row in the table.", DOCS + "t-sql/statements/delete-transact-sql"),
    "drop_or_truncate": ("risky", "Drops or empties a table",
                         "DROP TABLE or TRUNCATE TABLE on a permanent table removes its data.",
                         DOCS + "t-sql/statements/truncate-table-transact-sql"),
    "linked_server": ("risky", "Uses a linked server (four-part name)",
                      "Queries across servers depend on the link and can pull whole tables across the network.",
                      DOCS + "relational-databases/linked-servers/linked-servers-database-engine"),
}

_STRINGS = re.compile(r"'(?:[^']|'')*'")


def _code(sql: str) -> str:
    """SQL without comments, with string contents blanked, on one line, lower case."""
    text = strip_comments(sql or "")
    text = _STRINGS.sub("''", text)
    return re.sub(r"\s+", " ", text).lower()


@lru_cache(maxsize=4096)
def check_sql(sql: str) -> tuple[tuple[str, str], ...]:
    """(rule, about) pairs found in one piece of SQL. `about` says what it's about (a table, a column)."""
    raw = re.sub(r"\s+", " ", strip_comments(sql or "")).lower()
    code = _code(sql)
    found: list[tuple[str, str]] = []
    if re.search(r"\bwith\s*\(\s*nolock\s*\)|\(\s*nolock\s*\)|\bread\s*uncommitted\b", code):
        found.append(("nolock", ""))
    if re.search(r"(?<![<>!])(=|<>|!=)\s*null\b", code):
        found.append(("null_compare", ""))
    if re.search(r"\bnot\s+in\s*\(\s*select\b", code):
        found.append(("not_in_subquery", ""))
    for alias in _left_join_aliases(code):
        where = re.search(r"\bwhere\b(.*?)(?:\bgroup\s+by\b|\border\s+by\b|\bhaving\b|\bunion\b|$)", code)
        if where:
            cond = where.group(1)
            for m in re.finditer(rf"\b{re.escape(alias)}\.(\w+)\s*(=|<>|!=|>=|<=|>|<|\blike\b|\bin\b)", cond):
                found.append(("left_join_where", f"{alias}.{m.group(1)}"))
                break
    if re.search(r"\bselect\s+(distinct\s+)?top\s*\(?\s*\d+", code) and not re.search(r"\border\s+by\b", code):
        found.append(("top_without_order", ""))
    if re.search(r"\bbetween\s+'\d{4}-?\d{2}-?\d{2}'\s+and\s+'\d{4}-?\d{2}-?\d{2}'", raw):
        found.append(("between_datetime", ""))
    if re.search(r"\bfrom\s+[\w\[\]\.]+(\s+(as\s+)?\w+)?\s*,\s*[\w\[\]\.]+", code) and " join " not in code:
        found.append(("comma_join", ""))
    if re.search(r"\bselect\s+distinct\b", code) and re.search(r"\bjoin\b", code):
        found.append(("distinct_join", ""))
    for m in re.finditer(r"\b(?:where|and|or|on)\s+(year|month|day|convert|cast|isnull|coalesce|upper|lower|"
                         r"ltrim|rtrim|trim|left|right|substring|datepart|format)\s*\(", code):
        found.append(("function_on_column", m.group(1).upper()))
        break
    if re.search(r"\blike\s+n?'%", raw):
        found.append(("leading_wildcard", ""))
    if re.search(r"\bselect\s+(distinct\s+)?(top\s*\(?\s*\d+\s*\)?\s+)?(\w+\.)?\*", code) \
            and not re.search(r"\bexists\s*\(\s*select\s+\*", code):
        found.append(("select_star", ""))
    if re.search(r"\bcursor\s+(local\s+|global\s+|fast_forward\s+|static\s+|forward_only\s+)*for\b", code):
        found.append(("cursor", ""))
    if re.search(r"\bexec(ute)?\s*\(\s*@\w+|\bsp_executesql\b", code):
        found.append(("dynamic_sql", ""))
    for st in re.split(r";|\bgo\b", code):
        st = st.strip()
        m = re.match(r"(?:with\b.*?\)\s*)?(update\s+[\w\[\]\.#@]+\s+set\b|delete\s+(?:from\s+)?[\w\[\]\.#@]+)(.*)", st)
        if m and " where " not in f" {m.group(2)} " and not re.search(r"(update|delete\s+(from\s+)?)\s*[#@]", st):
            found.append(("no_where_change", m.group(1).split()[-2 if m.group(1).endswith(" set") else -1]))
    for m in re.finditer(r"\b(drop\s+table(?:\s+if\s+exists)?|truncate\s+table)\s+([\w\[\]\.]+)", code):
        if not m.group(2).lstrip("[").startswith("#"):
            found.append(("drop_or_truncate", m.group(2).strip("[]")))
    if re.search(r"\b(?:from|join)\s+(?:\[?\w+\]?\.){3}\[?\w+\]?", code):
        found.append(("linked_server", ""))
    for m in re.finditer(r"(?<!cross )(?<!natural )\bjoin\s+[\w\[\]\.#@]+(?:\s+(?:as\s+)?(?!on\b)\w+)?\s+"
                         r"(join|left|right|inner|full|where|group|order|union|$)", code):
        found.append(("join_no_condition", ""))
        break
    if re.search(r"\bunion\s+(?!all\b)", code):
        found.append(("union_not_all", ""))
    for m in re.finditer(r"(?<![/*])/\s*(?!\s*nullif\b)(\(?\s*(?:[a-z_\[][\w\].]*))", code):
        operand = m.group(1).strip("( ")
        if operand and not operand[0].isdigit() and not operand.startswith("nullif"):
            found.append(("divide_no_guard", operand))
            break
    if re.search(r"(=|<|>|between|and)\s*'(?:19|20)\d\d-?[01]\d-?[0-3]\d", raw):
        found.append(("hard_coded_date", ""))
    return tuple(dict.fromkeys(found))


def _left_join_aliases(code: str) -> list[str]:
    out = []
    for m in re.finditer(r"\bleft\s+(?:outer\s+)?join\s+([\w\[\]\.]+)(?:\s+(?:as\s+)?(\w+))?", code):
        alias = m.group(2)
        if not alias or alias in ("on", "with", "where"):
            alias = m.group(1).strip("[]").split(".")[-1].strip("[]")
        out.append(alias)
    return out


def ok_marks(cat: Catalogue, uid: str | None = None) -> set[tuple[str, str, str]]:
    sql = "SELECT query_uid, rule, detail FROM sql_findings_ok" + (" WHERE query_uid = ?" if uid else "")
    return {(r[0], r[1], r[2]) for r in cat.conn.execute(sql, (uid,) if uid else ())}


def mark_ok(cat: Catalogue, uid: str, rule: str, about: str = "", *, ok: bool = True) -> None:
    """Mark a finding as fine for this query (or take that back)."""
    with cat.conn:
        if ok:
            cat.conn.execute("INSERT OR REPLACE INTO sql_findings_ok (query_uid, rule, detail, author, at) "
                             "VALUES (?, ?, ?, ?, ?)", (uid, rule, about, cat.author(), now()))
        else:
            cat.conn.execute("DELETE FROM sql_findings_ok WHERE query_uid = ? AND rule = ? AND detail = ?",
                             (uid, rule, about))
    cat.changed()


def findings_for(cat: Catalogue, q: dict[str, Any], marks: set | None = None) -> list[dict[str, Any]]:
    """Findings for one library query (as from sqllibrary.get), with the ones you marked fine flagged ok."""
    marks = ok_marks(cat, q["uid"]) if marks is None else marks
    pairs = list(check_sql(q["sql"]))
    places = q.get("places") or []
    if any(p.get("built_from_variables") for p in places):
        pairs.append(("built_from_variables", ""))
    pairs += _catalogue_checks(cat, q)
    out = []
    for rule, about in pairs:
        sev, title, why, article = RULES[rule]
        out.append({"rule": rule, "about": about, "severity": sev, "title": title + (f": {about}" if about else ""),
                    "detail": why, "article": article, "ok": (q["uid"], rule, about) in marks})
    order = {"wrong": 0, "risky": 1, "slow": 2}
    return sorted(out, key=lambda f: (f["ok"], order[f["severity"]]))


def _catalogue_checks(cat: Catalogue, q: dict[str, Any]) -> list[tuple[str, str]]:
    """Checks that need what's catalogued: missing or deprecated tables, schema names, and joins
    compared with the columns' types and profiles."""
    out: list[tuple[str, str]] = []
    tables = q.get("tables") or []
    analysis = q.get("analysis") or {}
    ctes = {c.lower() for c in re.findall(r"(?:\bwith|,)\s*(\w+)\s*(?:\([^)]*\))?\s+as\s*\(", q["sql"] or "", re.I)}
    resolved = [t for t in tables if t.get("key")]
    for t in tables:
        name = t["name"]
        if "." not in name and not name.startswith(("#", "@")) and name not in ctes and t.get("relation") != "calls":
            out.append(("missing_schema", name))
    if resolved:
        from .curation import item_info
        for t in resolved:
            if (item_info(cat, t["key"]) or {}).get("lifecycle") in ("deprecated", "retired"):
                out.append(("deprecated_object", t["name"]))
        # a table named in the same database as ones we know, but not found there
        known_dbs = {k.rsplit("/", 1)[0] for k in (t["key"] for t in resolved)}
        for t in tables:
            if not t.get("key") and t.get("relation") != "calls" and not t["name"].startswith(("#", "@")) \
                    and t["name"] not in ctes and t["name"].count(".") <= 1 and len(known_dbs) == 1:
                if not cat.match_table(t["name"]):
                    out.append(("missing_object", t["name"]))
    keys = {t["name"]: t.get("key") for t in tables}
    for j in analysis.get("joins", []):
        a, b = keys.get(j["left_table"]), keys.get(j["right_table"])
        if not a or not b:
            continue
        pa, pb = _profile(cat, a, j["left_column"]), _profile(cat, b, j["right_column"])
        if pa and pb and _not_unique(pa) and _not_unique(pb):
            out.append(("non_unique_join", f"{j['left_column']} = {j['right_column']}"))
        ta, tb = _col_type(cat, a, j["left_column"]), _col_type(cat, b, j["right_column"])
        if ta and tb and _type_family(ta) != _type_family(tb):
            out.append(("type_mismatch", f"{j['left_column']} ({ta}) = {j['right_column']} ({tb})"))
    return out


def _profile(cat: Catalogue, key: str, column: str):
    return cat.conn.execute("SELECT row_count, distinct_count, null_count FROM profiles WHERE asset_key = ? AND "
                            "lower(column_name) = ?", (key, column.lower())).fetchone()


def _not_unique(p) -> bool:
    rows, distinct = p["row_count"] or 0, p["distinct_count"] or 0
    return rows > 0 and distinct > 0 and distinct < rows - (p["null_count"] or 0)


def _col_type(cat: Catalogue, key: str, column: str) -> str | None:
    r = cat.conn.execute("SELECT data_type FROM columns WHERE asset_key = ? AND lower(name) = ?",
                         (key, column.lower())).fetchone()
    return (r[0] or "").lower().split("(")[0] if r and r[0] else None


def _type_family(t: str) -> str:
    if any(x in t for x in ("char", "text", "string", "object")):
        return "text"
    if any(x in t for x in ("date", "time")):
        return "date"
    if any(x in t for x in ("int", "dec", "num", "float", "real", "money", "bit")):
        return "number"
    return t


def check_all(cat: Catalogue) -> int:
    """Check every query in the library; returns how many have open findings. (Results are worked out
    when needed, so this just warms the cache and reports.)"""
    return sum(1 for q in _all(cat) if any(not f["ok"] for f in q["findings"]))


def _all(cat: Catalogue) -> list[dict[str, Any]]:
    from .sqllibrary import all_with_places
    marks = ok_marks(cat)
    return [{**q, "findings": findings_for(cat, q, marks)} for q in all_with_places(cat)]


def review_tasks(cat: Catalogue, task) -> list:
    """One To review item per check (SELECT *, NOLOCK, a missing schema...) across every query with a wrong-result
    or risky finding, so a pattern you accept is answered once. Queries only found in old or not-relevant items
    are left out."""
    groups = findings_by_rule(cat)
    out = []
    for rule, g in groups.items():
        sev, title, why, _ = RULES[rule]
        queries = g["queries"]
        names = [q["name"] for q in queries[:5]]
        out.append(task("sql_check", f"sqlrule://{rule}",
                        f"SQL: {title} in {len(queries)} quer{'y' if len(queries) == 1 else 'ies'}",
                        f"{why} For example {', '.join(names)}{' …' if len(queries) > 5 else ''}. Open a query to fix "
                        "or mark each one fine, or accept this pattern everywhere.",
                        version=str(len(queries))))
    return out


def findings_by_rule(cat: Catalogue) -> dict[str, dict[str, Any]]:
    """rule -> {"queries": [{"uid", "name", "where", "abouts"}], "severity"} for every open wrong-result or risky
    finding, queries in old or not-relevant items left out."""
    from .curation import hidden_keys
    from .folderroles import archived
    from .groups import older_versions
    hidden = hidden_keys(cat, old=True) | archived(cat) | older_versions(cat)
    groups: dict[str, dict[str, Any]] = {}
    for q in _all(cat):
        if q["places"] and all(p["item"] in hidden for p in q["places"]):
            continue
        serious = [f for f in q["findings"] if not f["ok"] and f["severity"] in ("wrong", "risky")]
        if not serious:
            continue
        where = (cat.get_asset(q["places"][0]["item"]) or {}).get("name", "") if q["places"] else ""
        for rule in {f["rule"] for f in serious}:
            g = groups.setdefault(rule, {"queries": [], "severity": RULES[rule][0]})
            g["queries"].append({"uid": q["uid"], "name": q["name"], "where": where,
                                 "abouts": [f["about"] for f in serious if f["rule"] == rule]})
    return groups


def accept_rule_everywhere(cat: Catalogue, rule: str) -> int:
    """Mark every open finding of one check fine, in every query. Returns how many were marked."""
    n = 0
    for q in findings_by_rule(cat).get(rule, {}).get("queries", []):
        for about in q["abouts"]:
            mark_ok(cat, q["uid"], rule, about)
            n += 1
    return n


def summary(cat: Catalogue) -> dict[str, int]:
    """How many queries have each kind of finding (not counting ones marked fine)."""
    out = {"wrong": 0, "risky": 0, "slow": 0}
    for q in _all(cat):
        for sev in {f["severity"] for f in q["findings"] if not f["ok"]}:
            out[sev] += 1
    return out
