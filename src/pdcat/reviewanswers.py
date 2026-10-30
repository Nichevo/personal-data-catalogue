"""Answers that stick: what you said once about a kind of review item, applied everywhere.

The To review list raises one item per table for data problems (data types that don't fit,
possibly sensitive columns, odd values). Each item can be answered for the table at hand, and
some answers are worth keeping for every table:

  * a data type hint that is simply how your data is kept ("whole numbers that look like dates"
    when every date is a YYYYMMDD integer) is marked standard and never raised again;
  * a column name answered "not sensitive" three times stops being suggested as sensitive, and
    one answered "sensitive" three times is offered first next time;
  * a value that means "nothing" in your data (0, -1, 9999, N/A) is a placeholder, so a column
    that holds only that value isn't odd;
  * a server or database your code names but you haven't catalogued, on purpose, is ignored;
  * review rules you don't want yet are switched off in Settings (naming and old-item checks
    start off; everything else starts on).

All of it lives in settings, so it travels with the catalogue and survives rescans.
"""
from __future__ import annotations

from typing import Any

from .store import Catalogue

TYPE_HINTS_OK = "type_hints_ok"                 # hint codes that are the standard here
TYPE_HINTS_DISMISSED = "type_hints_dismissed"   # [[key, column, code], ...] by design on one column
PLACEHOLDERS = "placeholder_values"
SENSITIVE_NAMES = "sensitive_name_answers"      # {column name: {"yes": n, "no": n}}
SENSITIVE_TABLES_NO = "sensitive_tables_no"     # tables answered "none of these are sensitive"
UNMATCHED_IGNORED = "unmatched_ignored"         # server or database prefixes not catalogued on purpose
RULES_OFF = "review_rules_off"
DEFAULT_RULES_OFF = ("naming", "old_item")
DEFAULT_PLACEHOLDERS = ("0", "-1", "9999", "99999", "999999", "N/A", "NA", "n/a", "#", "*", "-", "?", "NULL", "null",
                        "none", "None", "00000000", "1900-01-01", "9999-12-31", "0001-01-01")
NAME_ANSWERS_NEEDED = 3   # the same answer this many times makes it the default for that column name


# ---- rules on and off ---------------------------------------------------------------------

def rules_off(cat: Catalogue) -> set[str]:
    stored = cat.setting(RULES_OFF)
    return set(DEFAULT_RULES_OFF) if stored is None else set(stored)


def set_rule(cat: Catalogue, rule: str, on: bool) -> None:
    off = rules_off(cat)
    off.discard(rule) if on else off.add(rule)
    cat.set_setting(RULES_OFF, sorted(off))


# ---- data type hints ----------------------------------------------------------------------

def hints_ok(cat: Catalogue) -> set[str]:
    return set(cat.setting(TYPE_HINTS_OK, []))


def mark_hint_standard(cat: Catalogue, code: str, standard: bool = True) -> None:
    """"Whole numbers that look like dates" is how dates are kept here: never raise it again (or do again)."""
    ok = hints_ok(cat)
    ok.add(code) if standard else ok.discard(code)
    cat.set_setting(TYPE_HINTS_OK, sorted(ok))


def hints_dismissed(cat: Catalogue) -> set[tuple[str, str, str]]:
    return {tuple(x) for x in cat.setting(TYPE_HINTS_DISMISSED, [])}


def dismiss_hints(cat: Catalogue, key: str, columns: list[tuple[str, str]]) -> None:
    """By design for these (column, code) pairs of one table."""
    have = hints_dismissed(cat)
    have |= {(key, col, code) for col, code in columns}
    cat.set_setting(TYPE_HINTS_DISMISSED, sorted(list(x) for x in have))


# ---- placeholder values --------------------------------------------------------------------

def placeholders(cat: Catalogue) -> list[str]:
    stored = cat.setting(PLACEHOLDERS)
    return list(DEFAULT_PLACEHOLDERS) if stored is None else list(stored)


def add_placeholder(cat: Catalogue, value: Any) -> None:
    have = placeholders(cat)
    text = str(value).strip()
    if text and text not in have:
        cat.set_setting(PLACEHOLDERS, [*have, text])


def set_placeholders(cat: Catalogue, values: list[str]) -> None:
    cat.set_setting(PLACEHOLDERS, [v.strip() for v in values if v.strip()])


def is_placeholder(cat: Catalogue, value: Any, known: list[str] | None = None) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return text == "" or text in (placeholders(cat) if known is None else known)


# ---- sensitive columns ---------------------------------------------------------------------

def sensitive_answers(cat: Catalogue) -> dict[str, dict[str, int]]:
    return cat.setting(SENSITIVE_NAMES, {})


def _count_names(cat: Catalogue, columns: list[str], answer: str) -> None:
    answers = sensitive_answers(cat)
    for col in columns:
        entry = answers.setdefault(col.lower(), {"yes": 0, "no": 0})
        entry[answer] = entry.get(answer, 0) + 1
    cat.set_setting(SENSITIVE_NAMES, answers)


def answer_sensitive(cat: Catalogue, key: str, columns: list[str], *, sensitive: bool) -> None:
    """Mark these columns sensitive (values purged, never exported) or say they aren't, and remember the
    answer by column name so the same name elsewhere needs fewer questions."""
    from .curation import dismiss_sensitive_suggestion, set_column_info
    for col in columns:
        if sensitive:
            set_column_info(cat, key, col, sensitive=True, rebuild=False)
        else:
            dismiss_sensitive_suggestion(cat, key, col)
    if sensitive:
        cat.rebuild_search()
    _count_names(cat, columns, "yes" if sensitive else "no")


def answer_table_sensitive(cat: Catalogue, key: str, columns: list[str], *, sensitive: bool) -> None:
    """The whole table is sensitive (every value purged), or nothing in it is."""
    from .curation import set_item_info
    if sensitive:
        set_item_info(cat, key, sensitive=True)
        _count_names(cat, columns, "yes")
    else:
        tables = set(cat.setting(SENSITIVE_TABLES_NO, []))
        tables.add(key)
        cat.set_setting(SENSITIVE_TABLES_NO, sorted(tables))
        _count_names(cat, columns, "no")


def tables_not_sensitive(cat: Catalogue) -> set[str]:
    return set(cat.setting(SENSITIVE_TABLES_NO, []))


def names_usually(cat: Catalogue, answer: str) -> set[str]:
    """Column names (lower case) answered `answer` ("yes" or "no") at least NAME_ANSWERS_NEEDED times and never
    the other way."""
    other = "no" if answer == "yes" else "yes"
    return {name for name, n in sensitive_answers(cat).items()
            if n.get(answer, 0) >= NAME_ANSWERS_NEEDED and not n.get(other, 0)}


# ---- tables in code not found ----------------------------------------------------------------

def unmatched_prefix(table: str) -> str:
    """The server or database part of a name in code: "srv.db.dbo.T" -> "srv.db", "db.dbo.T" -> "db",
    "dbo.T" or "T" -> ""."""
    parts = table.split(".")
    if len(parts) >= 4:
        return ".".join(parts[:2]).lower()
    if len(parts) == 3:
        return parts[0].lower()
    return ""


def ignored_prefixes(cat: Catalogue) -> set[str]:
    return set(cat.setting(UNMATCHED_IGNORED, []))


def ignore_prefix(cat: Catalogue, prefix: str, ignore: bool = True) -> None:
    have = ignored_prefixes(cat)
    have.add(prefix.lower()) if ignore else have.discard(prefix.lower())
    cat.set_setting(UNMATCHED_IGNORED, sorted(have))


def is_temp_name(table: str) -> bool:
    """Temp tables, table variables and names built while the code runs: never worth a review item."""
    last = table.split(".")[-1]
    return last.startswith(("#", "@")) or "{" in table or "%" in table
