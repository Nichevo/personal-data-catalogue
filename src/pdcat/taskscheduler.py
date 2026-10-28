"""Schedule scans from the app: create or remove the weekly Windows Task Scheduler task, and read when it last
ran and whether it worked.

The task runs a small batch file the app writes next to your catalogue. It calls this Python (the one running
the app, so no conda paths to fill in) to run `pdcat scan` and then `pdcat profile --queue`, appending the
output to scan.log. Only on Windows; elsewhere the page shows the commands to use instead.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

TASK_NAME = "pdcat weekly scan"
DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def available() -> bool:
    return os.name == "nt"


def batch_text(python: str, config: str | None, log: str) -> str:
    cfg = f' --config "{config}"' if config else ""
    run = f'"{python}" -c "import sys; from pdcat.cli import main; sys.exit(main(sys.argv[1:]))"'
    return "\r\n".join([
        "@echo off",
        "rem Written by the data catalogue (Settings > Scan on a schedule). Safe to delete with the task.",
        f'set "LOG={log}"',
        'echo. >> "%LOG%"',
        'echo ===== %date% %time% scan ===== >> "%LOG%"',
        f'{run}{cfg} scan >> "%LOG%" 2>&1',
        'set "SCAN_RESULT=%errorlevel%"',
        'echo ===== %date% %time% profile --queue ===== >> "%LOG%"',
        f'{run}{cfg} profile --queue >> "%LOG%" 2>&1',
        "exit /b %SCAN_RESULT%", ""])


def write_batch(folder: Path, config: str | None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "pdcat-weekly-scan.bat"
    path.write_text(batch_text(sys.executable, config, str(folder / "scan.log")), encoding="utf-8")
    return path


def create_command(batch: Path, day: str = "MON", time: str = "07:00") -> list[str]:
    if day not in DAYS:
        raise ValueError(f"day must be one of {', '.join(DAYS)}")
    if not re.fullmatch(r"[0-2]\d:[0-5]\d", time):
        raise ValueError("time must look like 07:00")
    return ["schtasks", "/Create", "/F", "/SC", "WEEKLY", "/D", day, "/ST", time, "/TN", TASK_NAME,
            "/TR", f'"{batch}"']


def delete_command() -> list[str]:
    return ["schtasks", "/Delete", "/F", "/TN", TASK_NAME]


def query_command() -> list[str]:
    return ["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"]


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def create(folder: Path, config: str | None, day: str = "MON", time: str = "07:00") -> str:
    batch = write_batch(folder, config)
    r = _run(create_command(batch, day, time))
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip() or "schtasks failed")
    return str(batch)


def remove() -> None:
    r = _run(delete_command())
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip() or "schtasks failed")


def parse_query(text: str) -> dict[str, Any]:
    """Fields from `schtasks /Query /V /FO LIST`: next and last run, last result, schedule."""
    fields: dict[str, str] = {}
    for line in (text or "").splitlines():
        k, sep, v = line.partition(":")
        if sep:
            fields[k.strip().lower()] = v.strip()
    last = fields.get("last result", "")
    ok = None
    if last:
        ok = last in ("0", "0x0")
        if last in ("267011", "0x41303"):  # has not run yet
            ok = None
    return {"next_run": fields.get("next run time", ""), "last_run": fields.get("last run time", ""),
            "last_result": last, "worked": ok, "status": fields.get("status", ""),
            "schedule": " ".join(x for x in (fields.get("schedule type", ""), fields.get("days", ""),
                                             fields.get("start time", "")) if x)}


def status() -> dict[str, Any] | None:
    """The task's state, or None when there's no such task (or not on Windows)."""
    if not available():
        return None
    try:
        r = _run(query_command())
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return parse_query(r.stdout)


def log_tail(folder: Path, lines: int = 30) -> str:
    path = folder / "scan.log"
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""
