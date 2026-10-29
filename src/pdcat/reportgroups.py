"""Versions of Power BI reports and Excel workbooks, grouped automatically (version 2.17).

A folder of reports often holds the same report many times over: "Sales report v2", "Sales report 2023-04
FINAL", "Sales report - copy", "Sales report March". Reports in the same folder whose names start the
same are grouped as versions of one report without waiting for a yes, and only the current one shows in
search, the Find lists, the Power BI map and measures across reports ("+3 older"). Each of those has a
"Show older versions" switch.

Names that are the same apart from version marks at the end (v2, a date, FINAL, copy, a month) are grouped.
When one name only starts like another ("Weekly KPI pack" and "Weekly KPI pack Bob changes"), the shared start
must say something: two real words, not just "report" or "dashboard". Names that share less than that, or share
a start and then go different ways ("Sales report North", "Sales report South"), are grouped only when the
reports also hold mostly the same pages, measures and sources; and two reports that hold nothing in common are
never grouped on a name that only starts the same.

Everything stays reversible on the group's page: make another report current (kept on later scans), take one
out, or break the group up. What you take out isn't grouped again until you allow it on the item's page.
Grouping across folders, or by content alone, is still only suggested (reportversions.py).
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any

from . import groups
from .reportversions import MIN_ITEMS, OLD_FOLDER_WORDS, _content, pick_main, similarity
from .store import Catalogue
from .versions import base_name

KINDS = ("report", "workbook")
TIEBREAK = 0.4   # content in common that lets names which only partly start the same count as versions
UNRELATED = 0.1  # below this much content in common, two thick reports aren't versions whatever their names say
MIN_LETTERS = 5  # letters the shared start of two names must have
STRONG_LETTERS = 8

GENERIC = {"report", "reports", "dashboard", "dash", "pbi", "powerbi", "power", "bi", "workbook", "book", "file",
           "template", "model", "analysis", "data", "sheet", "summary", "overview", "the", "a", "an", "of", "and",
           "for", "by", "to", "in", "on", "with", "my", "our", "new", "copy", "draft", "final", "test", "version"}
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "january",
          "february", "march", "april", "june", "july", "august", "september", "october", "november", "december")
# Words at the end of a name that mark a version rather than a different report.
VERSION_WORD = re.compile(
    r"(v|ver|version|rev|r)?\d+[a-z]?|fy\d+|q[1-4]|h[12]|wk\d*|"
    r"old|new|final|latest|backup|bak|orig|original|draft|test|testing|wip|prev|previous|working|temp|tmp|"
    r"fixed|updated|update|clean|archive|copy|v|ver|version|rev|edit|edited|changes|amended|"
    + "|".join(MONTHS), re.I)


# ---- names -----------------------------------------------------------------------------------------

def name_words(stem: str) -> list[str]:
    """'Sales report 2023-04 FINAL' -> ['sales', 'report']: lower-case words with version and date marks taken
    off the end (a lone word is kept)."""
    base, _ = base_name(stem)
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", base or stem)
    words = re.findall(r"[a-z0-9]+", spaced.lower())
    while len(words) > 1 and VERSION_WORD.fullmatch(words[-1]):
        words.pop()
    return words


def _real(word: str) -> bool:
    return len(word) >= 3 and word not in GENERIC and not word.isdigit()


def meaningful(words: list[str]) -> bool:
    """A shared start worth grouping on: enough letters, and a word that isn't generic ('report') or a number."""
    return sum(len(w) for w in words) >= MIN_LETTERS and any(_real(w) for w in words)


def name_match(a: list[str], b: list[str]) -> str:
    """How two names relate: 'same' (the same name apart from version marks), 'prefix' (one name is the start
    of the other, on a long enough start), 'partial' (they share a meaningful start, then differ), or ''."""
    k = 0
    while k < min(len(a), len(b)) and a[k] == b[k]:
        k += 1
    if k == 0:
        return ""
    if len(a) == len(b) == k:
        return "same"
    stem = a[:k]
    if not meaningful(stem):
        return ""
    if k == min(len(a), len(b)):
        # "Weekly KPI pack" starting "Weekly KPI pack Bob" is enough; "Sales report" starting "Sales report North"
        # needs the content to agree too.
        strong = sum(_real(w) for w in stem) >= 2 and sum(len(w) for w in stem) >= STRONG_LETTERS
        return "prefix" if strong else "partial"
    if k >= 2 and 2 * k >= min(len(a), len(b)):
        return "partial"
    return ""


# ---- finding the groups -----------------------------------------------------------------------------

def find_groups(cat: Catalogue, skip: set[str] = frozenset()) -> list[dict[str, Any]]:
    """Groups of reports (and of workbooks) in one folder whose names start the same. Each is
    {"members": {key: label}, "main_key", "why", "matched_by", "name", "kind"}. Items in `skip` are left out."""
    rows = [dict(r) for r in cat.conn.execute(
        "SELECT key, name, source, kind, attrs FROM assets WHERE kind IN ('report', 'workbook') AND status = 'active'")]
    by_folder: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r["key"] in skip:
            continue
        path = PurePosixPath(r["name"].replace("\\", "/"))
        r["attrs"] = json.loads(r["attrs"] or "{}")
        r["file"] = path.name
        r["words"] = name_words(path.stem)
        _, label = base_name(path.stem)
        r["label"] = label or "plain"
        r["old_place"] = any(part.lower() in OLD_FOLDER_WORDS for part in path.parent.parts)
        by_folder[(r["source"], str(path.parent).lower(), r["kind"])].append(r)
    found = []
    for members in by_folder.values():
        if len(members) > 1:
            found += _group_folder(members)
    return found


def _group_folder(rows: list[dict]) -> list[dict[str, Any]]:
    parent = {r["key"]: r["key"] for r in rows}
    by_content: set[str] = set()
    content = {r["key"]: _content(r["attrs"], r["kind"]) for r in rows}

    def find(k: str) -> str:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            how = name_match(a["words"], b["words"])
            if not how or find(a["key"]) == find(b["key"]):
                continue
            ca, cb = content[a["key"]], content[b["key"]]
            thick = min(len(ca), len(cb)) >= MIN_ITEMS
            if how == "prefix" and thick and len(ca & cb) / len(ca | cb) < UNRELATED:
                continue
            if how == "partial":
                if similarity(a["attrs"], b["attrs"], a["kind"]) < TIEBREAK:
                    continue
                by_content.update((a["key"], b["key"]))
            parent[find(a["key"])] = find(b["key"])
    clusters: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        clusters[find(r["key"])].append(r)
    out = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        main, why = pick_main(members)
        if main is None:  # no clear newest: still pick one, so the others fold under it
            best = max(members, key=lambda m: (m["attrs"].get("mtime") or 0, m["label"] == "plain", m["name"]))
            main, why = best["key"], "newest by date and name"
        start = _shared_start(members)
        out.append({
            "members": {m["key"]: _label(m, start) for m in sorted(members, key=lambda m: m["name"].lower())},
            "main_key": main, "why": why, "kind": members[0]["kind"],
            "name": next(m["file"] for m in members if m["key"] == main),
            "matched_by": "the start of their names" + (" and the same content" if by_content else "")})
    return out


def _shared_start(members: list[dict]) -> int:
    k = 0
    words = [m["words"] for m in members]
    while all(len(w) > k for w in words) and len({w[k] for w in words}) == 1:
        k += 1
    return k


def _label(m: dict, start: int) -> str:
    """What sets this version apart: its version mark ('v2', '2023-04 final'), or the words after the shared start."""
    if m["label"] != "plain":
        return m["label"]
    rest = re.findall(r"[A-Za-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", PurePosixPath(m["file"]).stem))[start:]
    return " ".join(rest) or "plain"


# ---- keeping the groups up to date ---------------------------------------------------------------

def auto_groups(cat: Catalogue) -> dict[str, dict[str, Any]]:
    """uid -> group, for the versions groups made by sync()."""
    out = {}
    for (uid,) in cat.conn.execute("SELECT uid FROM item_groups WHERE type = 'versions' "
                                   "AND json_extract(attrs, '$.auto')"):
        g = groups.get(cat, uid)
        if g:
            out[uid] = g
    return out


def sync(cat: Catalogue) -> int:
    """Make, update or remove the automatic report and workbook version groups. A current version you picked
    is kept; items you took out, and items in a group you made or accepted, are left alone. Returns how many
    groups there are."""
    from .features import on
    auto = auto_groups(cat)
    if not on(cat, "report_versions"):
        _remove(cat, list(auto))
        return 0
    others = {r[0] for r in cat.conn.execute(
        "SELECT m.asset_key FROM group_members m JOIN item_groups g ON g.uid = m.group_uid WHERE g.type = 'versions' "
        "AND NOT coalesce(json_extract(g.attrs, '$.auto'), 0)")}
    found = find_groups(cat, skip=others | groups.excluded(cat, "versions"))
    kept: set[str] = set()
    for f in found:
        keys = set(f["members"])
        overlap = sorted(((len(keys & {m["asset_key"] for m in g["members"]}), uid) for uid, g in auto.items()
                          if uid not in kept), reverse=True)
        uid = overlap[0][1] if overlap and overlap[0][0] else None
        old = auto.get(uid) if uid else None
        attrs = {"auto": True, "matched_by": f["matched_by"], "why": f["why"]}
        main = f["main_key"]
        if old:
            kept.add(uid)
            if old["attrs"].get("main_by") == "you" and old["main_key"] in keys:
                main, attrs["main_by"], attrs["why"] = old["main_key"], "you", "you picked it"
            labels = {m["asset_key"]: m["label"] for m in old["members"]}
            if {m["asset_key"] for m in old["members"]} == keys and old["main_key"] == main \
                    and old["attrs"].get("matched_by") == attrs["matched_by"]:
                continue  # nothing changed: leave it, so the catalogue doesn't count a change
            f["members"] = {k: labels.get(k) or v for k, v in f["members"].items()}
        new = groups.create(cat, "versions", f["name"], f["members"], main_key=main, attrs=attrs, uid=uid)
        kept.add(new)
    _remove(cat, [uid for uid in auto if uid not in kept])
    return len(found)


def _remove(cat: Catalogue, uids: list[str]) -> None:
    """Take away automatic groups that no longer hold (files gone, or grouping switched off). Unlike breaking
    a group up by hand, nothing is remembered: the items can be grouped again."""
    if not uids:
        return
    marks = ",".join("?" * len(uids))
    with cat.conn:
        cat.conn.execute(f"DELETE FROM group_members WHERE group_uid IN ({marks})", uids)
        cat.conn.execute(f"DELETE FROM item_groups WHERE uid IN ({marks})", uids)
    cat.changed()


def describe(g: dict[str, Any]) -> str:
    """One line for the group page on why these were grouped and why the current one is current."""
    bits = [f"Grouped automatically: same folder, and {g['attrs'].get('matched_by') or 'the start of their names'}."]
    if g["attrs"].get("why"):
        bits.append(f"Current: {g['attrs']['why']}.")
    return " ".join(bits)

