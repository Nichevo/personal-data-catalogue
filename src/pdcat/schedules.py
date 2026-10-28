"""Scheduled jobs from a CSV: a Task Scheduler export (`schtasks /query /fo csv /v`), a job list, or a
sheet kept by hand. You say which columns hold the job name, the command and the timing. Each job is
matched to the scripts it runs:

  * a clear match (the script's path in the command, `python -m` with its module, a .bat file that
    runs it) is linked
  * a likely one (a script, folder or project name somewhere in the row) is a suggestion to confirm
  * anything else is listed as unmatched, to link by hand

Script pages then show when they run. Your confirmations and rejections survive a re-import.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any

from .store import Catalogue, now

NAME_COLS = ("taskname", "task name", "job", "job name", "jobname", "name", "task", "process")
COMMAND_COLS = ("task to run", "command", "action", "actions", "program", "script", "path", "command line", "run",
                "executable", "arguments")
TIMING_COLS = ("schedule", "schedule type", "frequency", "trigger", "triggers", "next run time", "start time",
               "days", "when", "timing", "cron", "time")
CODE_KINDS = ("script", "notebook", "sql_script")


def read_csv(data: bytes) -> tuple[list[str], list[dict[str, str]]]:
    """Columns and rows of a CSV, whatever its delimiter and encoding (Excel's CSVs are often cp1252)."""
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            if enc == "utf-16" and "\x00" in text:
                continue
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:5000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows = [{(k or "").strip(): (v or "").strip() for k, v in r.items() if k is not None} for r in reader]
    # schtasks repeats its header line for every folder of tasks
    cols = [c.strip() for c in (reader.fieldnames or [])]
    rows = [r for r in rows if [r.get(c, "") for c in cols] != cols]
    return cols, rows


def guess_columns(columns: list[str]) -> dict[str, str | None]:
    low = {c.lower().strip(): c for c in columns}

    def pick(options):
        for o in options:
            if o in low:
                return low[o]
        for o in options:
            for k, c in low.items():
                if o in k:
                    return c
        return None
    timing = [low[o] for o in TIMING_COLS if o in low]
    return {"name": pick(NAME_COLS), "command": pick(COMMAND_COLS), "timing": ", ".join(timing[:3]) or None}


def import_rows(cat: Catalogue, rows: list[dict[str, str]], mapping: dict[str, Any], file_name: str = "") -> int:
    """Store the jobs (re-importing the same list updates them) and match them to scripts."""
    name_col, cmd_col = mapping.get("name"), mapping.get("command")
    timing_cols = [c.strip() for c in (mapping.get("timing") or "").split(",") if c.strip()]
    ts = now()
    n = 0
    with cat.conn:
        for r in rows:
            name = (r.get(name_col) if name_col else "") or ""
            command = (r.get(cmd_col) if cmd_col else "") or ""
            if not (name or command) or command.lower() in ("com handler", "disabled"):
                continue
            timing = " · ".join(f"{r[c]}" for c in timing_cols if r.get(c) and r[c].upper() != "N/A")
            uid = hashlib.sha1(f"{name}|{command}".lower().encode()).hexdigest()[:16]
            cat.conn.execute(
                "INSERT INTO schedules (uid, name, command, timing, raw, import_file, imported_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(uid) DO UPDATE SET timing = excluded.timing, "
                "raw = excluded.raw, import_file = excluded.import_file, imported_at = excluded.imported_at",
                (uid, name.lstrip("\\") or command[:80], command, timing, json.dumps(r), file_name, ts))
            n += 1
    match_all(cat)
    cat.changed()
    return n


def _norm(s: str) -> str:
    return s.replace("\\", "/").lower()


def _scripts(cat: Catalogue) -> list[dict[str, Any]]:
    out = []
    for r in cat.conn.execute(f"SELECT key, name, source, parent, attrs FROM assets WHERE status = 'active' AND "
                              f"kind IN ({', '.join('?' * len(CODE_KINDS))})", CODE_KINDS):
        a = json.loads(r["attrs"])
        out.append({"key": r["key"], "rel": _norm(r["name"]), "source": r["source"],
                    "full": _norm(a.get("full_path") or ""), "modules": a.get("modules", []),
                    "file": _norm(r["name"]).rsplit("/", 1)[-1]})
    return out


def _commands_inside_batch(path: str) -> str:
    """The text of a .bat/.cmd/.ps1 file the job runs, when it can be read from here."""
    try:
        p = Path(path.strip('"'))
        if p.suffix.lower() in (".bat", ".cmd", ".ps1") and p.is_file() and p.stat().st_size < 200_000:
            return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return ""


def match(cat: Catalogue, job: dict[str, Any], scripts: list[dict[str, Any]] | None = None,
          projects: dict[str, str] | None = None) -> list[tuple[str, str, str]]:
    """[(asset key, "confirmed" | "suggested", how)] for one job."""
    scripts = scripts if scripts is not None else _scripts(cat)
    command = _norm(job.get("command") or "")
    batch = " ".join(_commands_inside_batch(w) for w in re.findall(r'"[^"]+"|\S+', job.get("command") or ""))
    text = command + " " + _norm(batch)
    found: dict[str, tuple[str, str]] = {}
    for s in scripts:
        if s["full"] and s["full"] in text:
            found[s["key"]] = ("confirmed", "its path is in the command" + (" (inside the batch file)" if s["full"]
                                                                               not in command else ""))
    if not found:
        for m in re.finditer(r"-m\s+([\w.]+)", text):
            for s in scripts:
                if m.group(1) in s["modules"]:
                    found[s["key"]] = ("confirmed", f"python -m {m.group(1)}")
    if not found:
        files = re.findall(r"[\w./ -]*?([\w.-]+\.(?:py|ipynb|sql))\b", text)
        for f in dict.fromkeys(files):
            same = [s for s in scripts if s["file"] == f.lower()]
            for s in same:
                if s["rel"] in text:
                    found[s["key"]] = ("confirmed", f"{s['rel']} is in the command")
            if not any(s["key"] in found for s in same):
                for s in same:
                    found[s["key"]] = ("confirmed" if len(same) == 1 else "suggested",
                                       f"{f} is in the command" + ("" if len(same) == 1 else f" ({len(same)} files have this name)"))
    if not found:
        row = _norm(" ".join([job.get("name") or "", job.get("command") or "", " ".join(
            json.loads(job.get("raw") or "{}").values())]))
        words = set(re.findall(r"[a-z][a-z0-9_]{3,}", row))
        for s in scripts:
            stem = s["file"].rsplit(".", 1)[0]
            if len(stem) >= 4 and stem in words:
                found[s["key"]] = ("suggested", f"'{stem}' appears in the job")
        for pkey, pname in (projects or {}).items():
            if len(pname) >= 4 and pname.lower() in row:
                found.setdefault(pkey, ("suggested", f"the project name '{pname}' appears in the job"))
    return [(k, st, how) for k, (st, how) in found.items()]


def match_all(cat: Catalogue) -> dict[str, int]:
    scripts = _scripts(cat)
    projects = {r[0]: r[1] for r in cat.conn.execute("SELECT key, name FROM assets WHERE kind = 'project' AND "
                                                       "status = 'active'")}
    jobs = [dict(r) for r in cat.conn.execute("SELECT * FROM schedules")]
    counts = {"linked": 0, "suggested": 0, "unmatched": 0}
    with cat.conn:
        # What you decided stays; automatic matches are worked out again.
        cat.conn.execute("DELETE FROM schedule_links WHERE how NOT LIKE 'you%'")
        mine = {(r[0], r[1]) for r in cat.conn.execute("SELECT schedule_uid, asset_key FROM schedule_links")}
        for j in jobs:
            got = match(cat, j, scripts, projects)
            for key, state, how in got:
                if (j["uid"], key) not in mine:
                    cat.conn.execute("INSERT OR IGNORE INTO schedule_links (schedule_uid, asset_key, state, how) "
                                     "VALUES (?, ?, ?, ?)", (j["uid"], key, state, how))
            states = {s for _, s, _ in got} | {r[0] for r in cat.conn.execute(
                "SELECT state FROM schedule_links WHERE schedule_uid = ? AND how LIKE 'you%'", (j["uid"],))}
            counts["linked" if "confirmed" in states else "suggested" if "suggested" in states else "unmatched"] += 1
    return counts


def decide(cat: Catalogue, schedule_uid: str, asset_key: str, state: str) -> None:
    """Confirm, reject or add a link by hand. Kept through re-imports."""
    if state not in ("confirmed", "rejected"):
        raise ValueError(state)
    with cat.conn:
        cat.conn.execute("INSERT INTO schedule_links (schedule_uid, asset_key, state, how) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT(schedule_uid, asset_key) DO UPDATE SET state = excluded.state, how = excluded.how",
                         (schedule_uid, asset_key, state, f"you ({cat.author()})"))
    cat.changed()


def jobs(cat: Catalogue) -> list[dict[str, Any]]:
    out = []
    for j in cat.conn.execute("SELECT * FROM schedules ORDER BY name"):
        d = dict(j)
        d["links"] = [dict(r) for r in cat.conn.execute(
            "SELECT l.asset_key, l.state, l.how, a.name, a.kind FROM schedule_links l LEFT JOIN assets a "
            "ON a.key = l.asset_key WHERE l.schedule_uid = ? AND l.state != 'rejected' ORDER BY l.state, a.name",
            (j["uid"],))]
        out.append(d)
    return out


def schedules_for(cat: Catalogue, key: str) -> list[dict[str, Any]]:
    """The jobs that run this script (confirmed links only)."""
    return [dict(r) for r in cat.conn.execute(
        "SELECT s.uid, s.name, s.timing, s.command, l.how FROM schedule_links l JOIN schedules s ON s.uid = l.schedule_uid "
        "WHERE l.asset_key = ? AND l.state = 'confirmed' ORDER BY s.name", (key,))]


def delete_job(cat: Catalogue, uid: str) -> None:
    with cat.conn:
        cat.conn.execute("DELETE FROM schedule_links WHERE schedule_uid = ?", (uid,))
        cat.conn.execute("DELETE FROM schedules WHERE uid = ?", (uid,))
    cat.changed()


def scheduled_keys(cat: Catalogue) -> set[str]:
    return {r[0] for r in cat.conn.execute("SELECT DISTINCT asset_key FROM schedule_links WHERE state = 'confirmed'")}
