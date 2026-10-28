"""Screenshots of report pages and visuals, kept next to the catalogue file on your machine.

They're stored in a folder beside the catalogue (`<catalogue>.files/screenshots`), not inside it, so
share files and exports leave them out unless you tick them in: they can show real numbers.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .store import Catalogue, now

SUFFIXES = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}


def folder(cat: Catalogue) -> Path:
    return Path(str(cat.path) + ".files") / "screenshots"


def add(cat: Catalogue, report_key: str, page: str, data: bytes, mime: str = "image/png", caption: str = "") -> str:
    name = hashlib.sha1(data).hexdigest()[:16] + SUFFIXES.get(mime, ".png")
    d = folder(cat)
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(data)
    index = cat.setting("screenshots", {})
    shots = index.setdefault(report_key, {}).setdefault(page, [])
    if not any(s["file"] == name for s in shots):
        shots.append({"file": name, "caption": caption, "added": now(), "by": cat.author()})
    cat.set_setting("screenshots", index)
    return name


def for_page(cat: Catalogue, report_key: str, page: str) -> list[dict[str, Any]]:
    shots = cat.setting("screenshots", {}).get(report_key, {}).get(page, [])
    return [{**s, "path": folder(cat) / s["file"]} for s in shots if (folder(cat) / s["file"]).is_file()]


def remove(cat: Catalogue, report_key: str, page: str, name: str) -> None:
    index = cat.setting("screenshots", {})
    shots = index.get(report_key, {}).get(page, [])
    index.setdefault(report_key, {})[page] = [s for s in shots if s["file"] != name]
    cat.set_setting("screenshots", index)
    still = any(s["file"] == name for pages in index.values() for ss in pages.values() for s in ss)
    if not still:
        (folder(cat) / name).unlink(missing_ok=True)


def all_files(cat: Catalogue) -> list[Path]:
    d = folder(cat)
    return sorted(d.glob("*")) if d.is_dir() else []
