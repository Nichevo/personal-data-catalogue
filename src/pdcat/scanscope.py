"""Which sources a scan run covers.

`pdcat scan` with no names scans everything, unless a scope is saved in Settings (Scan on a schedule): then it
scans only those sources, so the weekly task can leave a slow server or a huge archive folder out. Named
sources (--source) and --all always win. `pdcat scan --ask` shows the sources as a numbered list and waits
for a choice; after the timeout it goes ahead with the saved scope (or everything), so a scan started by hand
still runs when nobody is at the keyboard.
"""
from __future__ import annotations

import sys
import threading
from typing import Any

from .store import Catalogue

SETTING = "scan_scope"
ASK_SECONDS = 60


def saved(cat: Catalogue) -> list[str] | None:
    """The saved scope: source names, or None for everything."""
    scope = cat.setting(SETTING)
    return list(scope) if scope else None


def save(cat: Catalogue, names: list[str] | None) -> None:
    cat.set_setting(SETTING, sorted(set(names)) if names else None, quiet=True)


def apply(cat: Catalogue, all_names: list[str], wanted: set[str], *, everything: bool = False) -> set[str]:
    """The names a run should scan: what was asked for, else the saved scope, else all of them."""
    if wanted:
        return wanted
    if everything:
        return set(all_names)
    scope = saved(cat)
    if scope:
        return {n for n in all_names if n in scope} or set(all_names)
    return set(all_names)


def _read_line(timeout: float, stream: Any = None) -> str | None:
    """A line typed within `timeout` seconds, else None. Works on Windows too (a thread reads stdin)."""
    stream = stream or sys.stdin
    box: list[str] = []

    def read() -> None:
        try:
            box.append(stream.readline())
        except Exception:  # stdin closed or not a terminal
            box.append("")
    t = threading.Thread(target=read, daemon=True)
    t.start()
    t.join(timeout)
    return box[0].strip() if box else None


def ask(cat: Catalogue, all_names: list[str], timeout: float = ASK_SECONDS, *, out: Any = None,
        stream: Any = None) -> set[str]:
    """Print a numbered list and read a choice ("1 3", "all", or Enter for the default); after `timeout`
    seconds with no answer, use the saved scope or everything."""
    out = out or sys.stdout
    default = apply(cat, all_names, set())
    print("Sources:", file=out)
    for i, n in enumerate(all_names, 1):
        print(f"  {i}. {n}" + ("  (in the saved scope)" if n in default and default != set(all_names) else ""),
              file=out)
    label = "everything" if default == set(all_names) else ", ".join(sorted(default))
    print(f"Which to scan? Numbers separated by spaces, 'all', or Enter for {label}. "
          f"Going ahead with that in {int(timeout)}s.", file=out, flush=True)
    line = _read_line(timeout, stream)
    if line is None:
        print(f"No answer: scanning {label}.", file=out)
        return default
    if not line:
        return default
    if line.lower() in ("all", "*", "a"):
        return set(all_names)
    picked = set()
    for tok in line.replace(",", " ").split():
        if tok.isdigit() and 1 <= int(tok) <= len(all_names):
            picked.add(all_names[int(tok) - 1])
        elif tok in all_names:
            picked.add(tok)
    return picked or default
