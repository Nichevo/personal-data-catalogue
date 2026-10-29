"""Tidy SQL before it is read, so the exact reader (sqlglot) understands more of it.

Real SQL is rarely a single clean statement. It comes from Python with placeholders (`{}` where an
f-string filled in a variable, `?`, `%s`, `%(start)s`), from dbt or Jinja templates (`{{ ref('sales') }}`,
`{% if %}`), from SQLCMD scripts (`$(Db)`, `:setvar`, `GO` between batches), and from stored procedures
with TRY/CATCH blocks, cursors and PRINT lines. sqlglot reads most of it as T-SQL but gives up on some
of it, falling back to a generic "Command" that holds no tables.

`prepare()` cleans a copy of the SQL (the original is never changed): templates are filled in where they
name a table, placeholders become plain names sqlglot can read, the lines that only steer a script
(TRY/CATCH, cursors, PRINT, THROW) are taken out, and the text is split into its GO batches. SQL kept
in string literals (`SET @sql = 'SELECT ... FROM dbo.Sales'; EXEC (@sql)`) is handed back as well, so
the tables it uses are found too.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .sqlrefs import expand_templates, looks_like_sql, strip_comments

MARK = "__pdcatvar"  # a placeholder, as a name sqlglot can read; turned back into "{}" afterwards
_LITERAL = re.compile(r"'(?:[^']|'')*'")
_MASKED = re.compile(r"'\x00(\d+)\x00'")
_ODBC_LITERAL = re.compile(r"\{\s*(?:ts|d|t)\s+('\x00\d+\x00')\s*\}", re.I)  # {ts '2024-01-01 00:00'}
_ODBC_CALL = re.compile(r"\{\s*(?:\?\s*=\s*)?call\s+([\w.\[\]]+)[^{}]*\}", re.I)  # {call dbo.Load(?, ?)}
_HINTS = re.compile(r"\boption\s*\((?:[^()]|\([^()]*\))*\)", re.I)  # OPTION (RECOMPILE, OPTIMIZE FOR ...)
_PYFORMAT = re.compile(r"%\(\w+\)s|%[sd](?!\w)")
_GO = re.compile(r"^[ \t]*go[ \t]*(?:\d+)?[ \t]*;?[ \t]*$", re.I | re.M)
_SQLCMD = re.compile(r"^[ \t]*:(?:setvar|r|on[ \t]+error|connect|out|error)\b.*$", re.I | re.M)
# Lines that only steer a script; the statements around and inside them stay.
_CONTROL = [
    (re.compile(r"\b(?:begin|end)\s+(?:try|catch)\b", re.I), ";"),
    (re.compile(r"\bdeclare\s+@?\w+\s+(?:insensitive\s+|scroll\s+)*cursor\b[\s\S]*?\bfor\b(?=\s*\(?\s*(?:select|with)\b)",
                re.I), ";"),
    (re.compile(r"\bfetch\s+(?:(?:next|prior|first|last|absolute\s+\S+|relative\s+\S+)\s+)?(?:from\s+)?"
                r"(?:global\s+)?@?\w+(?:\s+into\s+@\w+(?:\s*,\s*@\w+)*)?", re.I), ";"),
    (re.compile(r"^[ \t]*(?:open|close|deallocate)\s+(?:global\s+)?@?\w+[ \t]*;?[ \t]*$", re.I | re.M), ";"),
    (re.compile(r"^[ \t]*print\b[^\n;]*;?", re.I | re.M), ";"),
    (re.compile(r"\bthrow\b(?:\s+[^;\n]*)?;?", re.I), ";"),
    (re.compile(r"\bgoto\s+\w+", re.I), ";"),
    (re.compile(r"^[ \t]*[a-z_]\w*:(?=\s)", re.I | re.M), ";"),  # a GOTO label: "Done:"
    (re.compile(r"\breturn(?:\s+-?\d+|\s+@\w+)?(?=\s*(?:;|$|\bend\b))", re.I | re.M), ";"),
]


@dataclass
class Cleaned:
    batches: list[str]  # for sqlglot, one per GO batch; placeholders are MARK1, MARK2, ...
    text: str           # for the simple reader; placeholders are "{}"
    notes: list[str] = field(default_factory=list)    # what was tidied, for the query page and the scan log
    strings: list[str] = field(default_factory=list)  # SQL held in string literals (dynamic SQL)


def prepare(sql: str) -> Cleaned:
    notes: list[str] = []
    text, templated = expand_templates(sql or "")
    if templated:
        notes.append("templates")
    text = strip_comments(text)
    literals: list[str] = []

    def mask(m: re.Match) -> str:
        literals.append(m.group(0))
        return f"'\x00{len(literals) - 1}\x00'"

    text = _LITERAL.sub(mask, text)
    text = _ODBC_LITERAL.sub(r"\1", text)
    text = _ODBC_CALL.sub(r"EXEC \1", text)
    text = _HINTS.sub(" ", text)
    text, n = _PYFORMAT.subn("?", text)
    if n or "{}" in text or "?" in text:
        notes.append("variables")
    for pattern, repl in _CONTROL:
        text, n = pattern.subn(repl, text)
        if n and "control flow" not in notes:
            notes.append("control flow")
    text, n = _SQLCMD.subn(" ", text)
    if n and "templates" not in notes:
        notes.append("templates")
    parts = [p for p in _GO.split(text) if p.strip(" \t\r\n;")]
    if len(parts) > 1:
        notes.append("GO batches")

    def restore(t: str) -> str:
        return _MASKED.sub(lambda m: literals[int(m.group(1))], t)

    counter = iter(range(1, 1_000_000))
    batches = [restore(re.sub(r"\{\}", lambda _: f"{MARK}{next(counter)}", p)) for p in parts]
    strings = []
    for lit in literals:
        inner = lit[1:-1].replace("''", "'")
        if len(inner) > 15 and looks_like_sql(inner):
            strings.append(inner)
    if strings:
        notes.append("SQL in strings")
    return Cleaned(batches=batches, text=restore(";\n".join(parts)), notes=notes, strings=strings)


def unmark(text: str) -> str:
    """Placeholders back to "{}", for the simple reader."""
    return re.sub(rf"{MARK}\d+", "{}", text)


def split_statements(batch: str) -> list[str]:
    """A batch split at its semicolons (outside quotes), for reading statement by statement."""
    out, start, quote = [], 0, False
    for i, ch in enumerate(batch):
        if ch == "'":
            quote = not quote
        elif ch == ";" and not quote:
            out.append(batch[start:i])
            start = i + 1
    out.append(batch[start:])
    return [s for s in out if s.strip()]
