"""Spotting duplicates: the same file in two places, folders that are copies of
each other, sources that overlap, and files named like copies.

Paths are compared the Windows way and with mapped drives turned into their
network names, so S:\\Sales and \\\\fileserver\\shared\\Sales count as the same
folder. On Windows the mappings are read from `net use`; you can add your own
on the Duplicates page.

Files are matched by a fingerprint of their size and their first and last
megabyte, which is quick even on network drives.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

from .store import Catalogue

COPY_NAME = re.compile(r"(^copy of |\s*-\s*copy(\s*\(\d+\))?$|\s*\(\d+\)$|[_ -](copy|old|backup|bak|orig)(\d+)?$)",
                       re.I)
CHUNK = 1 << 20


def fingerprint(path: Path) -> str:
    size = path.stat().st_size
    h = hashlib.sha1(str(size).encode())
    with path.open("rb") as f:
        h.update(f.read(CHUNK))
        if size > 2 * CHUNK:
            f.seek(-CHUNK, os.SEEK_END)
            h.update(f.read(CHUNK))
    return h.hexdigest()


def mapped_drives() -> dict[str, str]:
    """{"S:": "\\\\server\\share"} from `net use` on Windows; empty elsewhere."""
    if os.name != "nt":
        return {}
    try:
        out = subprocess.run(["net", "use"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    found = {}
    for line in out.splitlines():
        m = re.search(r"\b([A-Z]:)\s+(\\\\\S+)", line)
        if m:
            found[m.group(1).upper()] = m.group(2)
    return found


def drive_aliases(cat: Catalogue) -> dict[str, str]:
    aliases = {k.upper().rstrip("\\/"): v for k, v in mapped_drives().items()}
    for a in cat.setting("drive_aliases", []):
        aliases[a["drive"].upper().rstrip("\\/")] = a["unc"]
    return aliases


def canonical(path: str, aliases: dict[str, str] | None = None) -> str:
    """One spelling for one place: forward slashes, lower case, mapped drives as network paths."""
    p = (path or "").strip().strip('"').replace("\\", "/")
    for drive, unc in (aliases or {}).items():
        if p.lower().startswith(drive.lower().replace("\\", "/")):
            p = unc.replace("\\", "/").rstrip("/") + p[len(drive):]
            break
    p = re.sub(r"/+", "/", p) if not p.startswith("//") else "//" + re.sub(r"/+", "/", p[2:])
    return p.rstrip("/").lower()


def _inside(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent + "/")


def report(cat: Catalogue) -> dict[str, list[dict[str, Any]]]:
    aliases = drive_aliases(cat)
    # Sources that point at the same place, or one inside another.
    roots = []
    for r in cat.conn.execute("SELECT key, kind, name, source, attrs FROM assets WHERE kind IN ('folder', 'project') "
                              "AND status = 'active'"):
        path = json.loads(r["attrs"]).get("path")
        if path:
            roots.append({"key": r["key"], "name": r["name"], "kind": r["kind"], "path": path,
                          "canonical": canonical(path, aliases)})
    overlapping = []
    for i, a in enumerate(roots):
        for b in roots[i + 1:]:
            if _inside(a["canonical"], b["canonical"]) or _inside(b["canonical"], a["canonical"]):
                same = a["canonical"] == b["canonical"]
                overlapping.append({"a": a["name"], "a_key": a["key"], "a_path": a["path"], "b": b["name"],
                                    "b_key": b["key"], "b_path": b["path"],
                                    "how": "the same folder" if same else "one is inside the other"})

    files = []
    for r in cat.conn.execute("SELECT key, kind, name, source, attrs FROM assets WHERE kind IN ('data_file', "
                              "'series_file') AND status = 'active'"):
        attrs = json.loads(r["attrs"])
        if attrs.get("checksum") and attrs.get("size_bytes"):
            files.append({"key": r["key"], "name": r["name"], "source": r["source"], "path": attrs.get("path", ""),
                          "checksum": attrs["checksum"], "size": attrs["size_bytes"], "modified": attrs.get("modified"),
                          "series": attrs.get("in_series")})
    # The same file in more than one place (ignoring the same path reached through two overlapping sources).
    by_sum: dict[str, list[dict]] = defaultdict(list)
    for f in files:
        by_sum[f["checksum"]].append(f)
    same_file = []
    for checksum, group in by_sum.items():
        places = {canonical(f["path"], aliases): f for f in group}
        if len(places) > 1:
            same_file.append({"checksum": checksum, "size": group[0]["size"],
                              "files": sorted(places.values(), key=lambda f: f["modified"] or "")})

    # Folders that are full or partial copies of each other.
    by_folder: dict[str, set[str]] = defaultdict(set)
    folder_path: dict[str, str] = {}
    for f in files:
        folder = canonical(str(Path(f["path"]).parent), aliases)
        by_folder[folder].add(f["checksum"])
        folder_path[folder] = str(Path(f["path"]).parent)
    copied = []
    names = sorted(by_folder)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            shared = by_folder[a] & by_folder[b]
            if len(shared) < 2:
                continue
            small = min(len(by_folder[a]), len(by_folder[b]))
            share = len(shared) / small
            if share >= 0.5:
                copied.append({"a": folder_path[a], "b": folder_path[b], "shared_files": len(shared),
                               "a_files": len(by_folder[a]), "b_files": len(by_folder[b]),
                               "how": "copy" if by_folder[a] == by_folder[b] else "partial copy"})

    # Files named like copies ("Copy of sales.csv", "sales - Copy.csv", "sales_old.csv").
    by_stem: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for f in files:
        p = Path(f["path"])
        by_stem[(canonical(str(p.parent), aliases), p.suffix.lower())].append(f)
    named = []
    for (folder, suffix), group in by_stem.items():
        stems = {Path(f["path"]).stem.lower(): f for f in group}
        for stem, f in stems.items():
            original = COPY_NAME.sub("", stem).strip()
            if original != stem:
                named.append({"copy": f["name"], "copy_key": f["key"], "copy_path": f["path"],
                              "original": stems[original]["name"] if original in stems else "",
                              "original_key": stems[original]["key"] if original in stems else ""})
    return {"overlapping_sources": overlapping, "same_file": same_file, "copied_folders": copied,
            "named_copies": named}


def find_duplicates(cat: Catalogue) -> int:
    """After a scan: count what was found (shown on the Duplicates page and the review list)."""
    r = report(cat)
    summary = {k: len(v) for k, v in r.items()}
    cat.set_setting("duplicates_summary", summary, quiet=True)
    return sum(summary.values())


def add_drive_alias(cat: Catalogue, drive: str, unc: str) -> None:
    drive = drive.strip().rstrip("\\/").upper()
    if not re.fullmatch(r"[A-Z]:", drive) or not unc.strip().startswith(("\\\\", "//")):
        raise ValueError("Give a drive like S: and a network path like \\\\server\\share")
    aliases = [a for a in cat.setting("drive_aliases", []) if a["drive"].upper() != drive]
    cat.set_setting("drive_aliases", [*aliases, {"drive": drive, "unc": unc.strip()}])
