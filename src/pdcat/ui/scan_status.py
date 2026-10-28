"""A progress box for scans, so a long scan never looks stuck.

It shows which file is being read, a progress bar, how long the scan has run
and any file that took a long time. The same updates go to the terminal the
app was started from (every 30 seconds, plus slow files), so you can see a
scan is still going even with the browser closed.
"""
from __future__ import annotations

import time

import streamlit as st

from pdcat.scanprogress import printer
from pdcat.store import Catalogue


class ScanStatus:
    """Pass it to scanners as their `report` function."""

    def __init__(self, label: str) -> None:
        # Written to directly, not used as `with self.box:`, because leaving that block marks the box complete.
        self.box = st.status(label, expanded=True)
        self.line = self.box.empty()
        self.bar = self.box.progress(0.0)
        self.box.button("⏹️ Stop", key=f"stop-scan-{time.monotonic_ns()}",
                        help="Stops the scan. A folder or project being read is left as it was before this scan.")
        self.box.caption("Each file shows here as it is read. A big Power BI report or workbook can take a minute "
                         "or two on its own, especially on a network drive.")
        self.started = time.monotonic()
        self.terminal = printer()

    def phase(self, label: str) -> None:
        self.box.update(label=label, expanded=True)  # leaving out expanded would fold the box away
        self.bar.progress(0.0)

    def __call__(self, text: str, done: int | None = None, total: int | None = None) -> None:
        self.terminal(text, done, total)
        if text.startswith("  "):  # a slow file or step
            self.box.caption(f"🐢 {text.strip()}")
            return
        # Every update is shown: Streamlit only sends the latest one for each element, so this stays cheap,
        # and skipping one could leave the previous file's name on screen while a slow one is read.
        self.line.markdown(f"{text}  \n*{_elapsed(time.monotonic() - self.started)} so far*")
        if total:
            self.bar.progress(min((done or 0) / total, 1.0))

    def note(self, text: str) -> None:
        """Something that went wrong but didn't stop the scan."""
        self.terminal("  " + text.strip())
        self.box.caption(f"⚠️ {text.strip()}")

    def took(self) -> str:
        return _elapsed(time.monotonic() - self.started)

    def done(self, label: str, ok: bool = True) -> None:
        self.box.update(label=f"{label} ({self.took()})", state="complete" if ok else "error", expanded=not ok)


def _elapsed(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


def after_scan_with_status(cat: Catalogue, status: ScanStatus) -> dict[str, int]:
    """The suggestions and review list worked out after a scan, shown step by step."""
    from pdcat.postscan import after_scan
    status.phase("Working out suggestions and the review list …")
    return after_scan(cat, progress=status.note, report=status)
