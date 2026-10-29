"""The scan log: every scan with how long it took and what slowed it, so you can see whether the
scheduled rescans are getting slower before they become a problem."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from pdcat.scanprogress import gb
from pdcat.sqllibrary import reading_summary
from pdcat.store import Catalogue

from .common import page_title

GETTING_SLOWER = 1.5  # a scan this many times slower than the one before is flagged


def scan_log_page(cat: Catalogue) -> None:
    page_title("Scan log")
    st.caption("Every scan, newest first: how long it took, how much it read and which files were slow. "
               "Folder scans skip files that haven't changed, so a rescan is normally much quicker than the first "
               "scan of a folder. The weekly scheduled scan (`pdcat scan`) shows here too.")
    log = cat.scan_log()
    if not log:
        st.info("No scans yet.")
        return
    for note in slower_scans(log):
        st.warning(note, icon="🐢")
    rows = []
    for r in log:
        s = r["summary"]
        secs = s.get("seconds")
        if secs is None and r["finished_at"]:
            secs = _seconds_between(r["started_at"], r["finished_at"])
        items = {k: v for k, v in s.items() if isinstance(v, int) and k not in
                 ("seconds", "read_seconds", "files_found", "bytes", "skipped_folders", "errors")}
        rows.append({"started": r["started_at"][:16].replace("T", " "), "source": r["source"],
                     "took": _took(secs), "reading files": _took(s.get("read_seconds")),
                     "files": s.get("files_found"), "read": gb(s["bytes"]) if s.get("bytes") else "",
                     "errors": s.get("errors"), "items": ", ".join(f"{v} {k}" for k, v in sorted(items.items())),
                     "slow files": "; ".join(f"{name} {secs}s" for name, secs in s.get("slow", [])[:5]),
                     "note": s.get("warning", "")})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    runs = cat.setting("after_scan_runs", [])
    if runs:
        st.subheader("After each scan")
        st.caption("The suggestions and review list worked out after every scan, and the steps that took longest.")
        st.dataframe(pd.DataFrame([{
            "at": r["at"][:16].replace("T", " "), "took": _took(r.get("seconds")),
            "slowest steps": ", ".join(f"{n} {t}s" for n, t in r.get("slowest", [])[:3]),
            "SQL reading": reading_summary(r.get("sql_reading")),
            "failed": ", ".join(r.get("failed", []))} for r in reversed(runs)]), hide_index=True, width="stretch")
        latest = next((r["sql_reading"] for r in reversed(runs) if r.get("sql_reading")), None)
        if latest and (latest.get("partly") or latest.get("simple")) and latest.get("sqlglot"):
            st.caption("**SQL reading**: \"partly\" and \"simply\" mean sqlglot couldn't read some statements (it would "
                       "have read them as a generic Command), so the simple reader found their tables instead. Open "
                       "Find → SQL queries to see which ones; each query page says what was tidied first.")


def slower_scans(log: list[dict]) -> list[str]:
    """One note per source whose latest scan took much longer than the one before (both with timings)."""
    notes = []
    seen: set[str] = set()
    for r in log:
        if r["source"] in seen:
            continue
        seen.add(r["source"])
        earlier = next((e for e in log if e["source"] == r["source"] and e["id"] < r["id"]), None)
        now_s, then_s = r["summary"].get("seconds"), (earlier or {}).get("summary", {}).get("seconds")
        if now_s and then_s and then_s >= 10 and now_s >= GETTING_SLOWER * then_s:
            slow = r["summary"].get("slow") or []
            why = f" Slowest: {slow[0][0]} ({slow[0][1]}s)." if slow else ""
            notes.append(f"**{r['source']}** took {_took(now_s)} on {r['started_at'][:10]}, up from {_took(then_s)} "
                         f"the scan before.{why}")
    return notes


def _took(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = int(seconds)
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


def _seconds_between(a: str, b: str) -> float | None:
    try:
        return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()
    except ValueError:
        return None
