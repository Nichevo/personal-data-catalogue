"""Finding files for a folder scan, and saying how far the scan has got.

A folder is walked once, and the walk steps around folders that never hold
your data (.git, __pycache__, node_modules, Python and conda environments,
the recycle bin) and folders you marked "ignore". On a network drive the
walk itself can take a while, so it reports as it goes.

Scanners take a `report(text, done, total)` function. The app shows it as a
progress bar; the command line prints it. `done` and `total` are optional.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from pathlib import Path

Report = Callable[..., None]

# Folders that hold tools, caches or environments, never your own data files.
JUNK_DIRS = {".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv", "site-packages",
             ".ipynb_checkpoints", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".idea", ".vscode",
             "$recycle.bin", "system volume information"}
# A folder holding one of these is a Python or conda environment.
ENV_MARKERS = ("conda-meta", "pyvenv.cfg")


def quiet(text: str, done: int | None = None, total: int | None = None) -> None:
    """A report function that says nothing."""


def find_files(root: Path, suffixes: Iterable[str], recursive: bool = True, ignored: set[str] | None = None,
               report: Report = quiet) -> tuple[dict[str, list[Path]], int]:
    """Files under root with these suffixes, by suffix (lower case), sorted; and how many folders were skipped.

    `ignored` holds folders relative to root (as stored for the "ignore" role)."""
    wanted = {s.lower() for s in suffixes}
    ignored = {i.lower().strip("/") for i in ignored or ()}
    found: dict[str, list[Path]] = {s: [] for s in wanted}
    skipped = folders = files = 0
    last = time.monotonic()
    for folder, dirs, names in os.walk(root, onerror=lambda e: None):
        folders += 1
        rel = os.path.relpath(folder, root).replace("\\", "/")
        if rel != "." and any(m in dirs or m in names for m in ENV_MARKERS):
            skipped += 1  # a Python or conda environment
            dirs[:] = []
            continue
        keep = []
        for d in sorted(dirs):
            sub = d if rel == "." else f"{rel}/{d}"
            if sub.lower() in ignored or d.lower() in JUNK_DIRS:
                skipped += 1
            else:
                keep.append(d)
        dirs[:] = keep if recursive else []
        for n in names:
            files += 1
            suffix = os.path.splitext(n)[1].lower()
            if suffix in wanted and not n.startswith("~"):
                found[suffix].append(Path(folder) / n)
        if time.monotonic() - last > 0.5:
            last = time.monotonic()
            report(f"Looking for files… {files:,} files in {folders:,} folders so far (now in {rel})")
    for paths in found.values():
        paths.sort()
    return found, skipped


def has_files(root: Path, suffixes: Iterable[str]) -> bool:
    """Is there at least one such file under root (outside tool and environment folders)? Stops at the first."""
    wanted = {s.lower() for s in suffixes}
    for folder, dirs, names in os.walk(root, onerror=lambda e: None):
        if folder != str(root) and any(m in dirs or m in names for m in ENV_MARKERS):
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d.lower() not in JUNK_DIRS]
        if any(os.path.splitext(n)[1].lower() in wanted for n in names):
            return True
    return False


BIG_FILE = 200 * 1024 * 1024    # a file this size takes a while on its own
BIG_FOLDER = 2 * 1024 ** 3      # ...and so does a folder with this much to read
MANY_FILES = 2000


def size_up(paths: Iterable[Path]) -> dict:
    """{"files", "bytes", "big": [(name, bytes)]} for a list of files, to warn before a long scan."""
    files = total = 0
    big: list[tuple[str, int]] = []
    for p in paths:
        try:
            n = p.stat().st_size
        except OSError:
            continue
        files += 1
        total += n
        if n >= BIG_FILE:
            big.append((p.name, n))
    big.sort(key=lambda b: -b[1])
    return {"files": files, "bytes": total, "big": big}


def size_warning(info: dict) -> str:
    """Why a scan of these files may take a while, or '' when it looks quick."""
    reasons = []
    if info["files"] >= MANY_FILES:
        reasons.append(f"{info['files']:,} files")
    if info["bytes"] >= BIG_FOLDER:
        reasons.append(f"{gb(info['bytes'])} to read")
    if info["big"]:
        names = ", ".join(f"{n} ({gb(b)})" for n, b in info["big"][:3]) + (" …" if len(info["big"]) > 3 else "")
        reasons.append(f"{len(info['big'])} large file{'s' if len(info['big']) > 1 else ''}: {names}")
    return "; ".join(reasons)


def gb(n: int) -> str:
    return f"{n / 1024 ** 3:.1f} GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.0f} MB"


def pick(found: dict[str, list[Path]], suffixes: Iterable[str]) -> list[Path]:
    return sorted(p for s in suffixes for p in found.get(s.lower(), []))


class Steps:
    """Counts files read across the parts of a scan, for the progress bar, and mentions slow ones."""

    SLOW = 20  # seconds

    def __init__(self, report: Report = quiet, total: int = 0, verb: str = "Reading") -> None:
        self.report, self.total, self.verb, self.done = report, total, verb, 0
        self.slow: list[tuple[str, float]] = []  # (file, seconds) for every file over SLOW, for the scan log
        self.seconds = 0.0

    @contextmanager
    def __call__(self, what: str, rel: str):
        self.report(f"{self.verb} {what} {self.done + 1:,} of {self.total:,}: {rel}", self.done, self.total)
        started = time.monotonic()
        try:
            yield
        finally:
            self.done += 1
            took = time.monotonic() - started
            self.seconds += took
            if took >= self.SLOW:
                self.slow.append((rel, round(took)))
                self.report(f"  {rel} took {took:.0f}s", self.done, self.total)


def printer(every: float = 30.0, out: Callable[[str], None] | None = None) -> Report:
    """A report function for the terminal: notes about slow steps always, other updates every `every` seconds,
    so a long scan shows it's still going without filling the screen."""
    out = out or (lambda line: print(line, flush=True))
    last = [float("-inf")]

    def report(text: str, done: int | None = None, total: int | None = None) -> None:
        if text.startswith("  ") or time.monotonic() - last[0] >= every:
            last[0] = time.monotonic()
            out(text if text.startswith("  ") else f"  … {text}")
    return report


class Reads:
    """File reads done ahead, before a scan opens its database transaction.

    A scan writes everything in one transaction so a failed scan changes nothing, but while it is open no other
    window or scan can write to the catalogue. Reading the files (the slow part) first keeps that window short.
    `get` hands back a result read ahead, or reads the file now if it wasn't."""

    def __init__(self) -> None:
        self._done: dict[tuple[str, Path], tuple[bool, object]] = {}

    def load(self, fn: Callable, path: Path, *args) -> None:
        try:
            self._done[(fn.__name__, path)] = (True, fn(path, *args))
        except Exception as e:  # kept, and raised again where the scan would have hit it
            self._done[(fn.__name__, path)] = (False, e)

    def get(self, fn: Callable, path: Path, *args):
        hit = self._done.pop((fn.__name__, path), None)
        if hit is None:
            return fn(path, *args)
        ok, value = hit
        if not ok:
            raise value
        return value

