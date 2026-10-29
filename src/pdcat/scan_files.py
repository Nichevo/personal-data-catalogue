"""CSV and Parquet files as data sources.

Files are found inside Python projects (alongside the code that writes them)
and in any extra folders you point the catalogue at. Each file records its
size, modified date, row count, columns and a value profile.

Files that differ only by a date (sales_2026-01.csv, sales_2026-02.csv…)
become one dated series: see series.py.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .profiling import DATA_SUFFIXES, DEFAULT_MAX_ROWS, file_info, profile_file
from .scanprogress import (
    MANY_FILES,
    Reads,
    Report,
    Steps,
    find_files,
    pick,
    quiet,
    size_up,
    size_warning,
    stat_files,
)
from .store import Catalogue

SAMPLE_OVER = MANY_FILES  # a folder with this many data files is sampled by date (Settings: sample_over_files)
SAMPLE_NEWEST = 10        # ...reading only the newest files of each dated series (Settings: sample_newest)
DATA_CHUNK = 200          # stand-alone data files are saved every this many, so a stopped scan keeps them
OFFICE_CHUNK = 10         # ...and reports and workbooks every this many
RECENT_MINUTES = 60       # a source scanned less than this long ago isn't scanned again unless you insist


def ingest_data_file(cat: Catalogue, path: Path, key: str, name: str, source: str, parent: str,
                     max_rows: int = DEFAULT_MAX_ROWS, kind: str = "data_file", reads: Reads | None = None,
                     listed: bool = False, st=None) -> str | None:
    """Catalogue one data file. Unchanged files (same size and modified time) reuse their last profile.
    A `listed` file (in a folder sampled by date) is recorded from the file system without being opened,
    unless it was read in an earlier scan and hasn't changed since.
    Returns an error message if the file could not be read."""
    reads = reads or Reads()
    st = st or path.stat()  # the folder scan passes the stat it already has
    previous = cat.get_asset(key)
    if previous and previous["attrs"].get("size_bytes") == st.st_size \
            and previous["attrs"].get("mtime") == st.st_mtime and cat.profile(key):
        attrs = previous["attrs"]
        if "checksum" not in attrs and not listed:
            attrs["checksum"] = _fingerprint(path)
        cat.upsert_asset(key, kind, name, source, parent=parent, attrs=attrs)
        cat.set_columns(key, [{"name": p["column_name"], "ordinal": p["ordinal"], "data_type": p["data_type"]}
                              for p in cat.profile(key)])
        return previous["attrs"].get("read_error")
    if listed:
        attrs = {**file_info(path, st), "mtime": st.st_mtime, "format": path.suffix.lower().lstrip("."), "not_read": True}
        if kind == "series_file":
            attrs["in_series"] = parent
        cat.upsert_asset(key, kind, name, source, parent=parent, attrs=attrs)
        cat.set_columns(key, [])
        return None

    try:
        attrs, profiles, method = reads.get(profile_file, path, max_rows)
    except Exception as e:  # a malformed file must not stop the scan
        attrs = {"path": str(path), "size_bytes": st.st_size, "read_error": f"{type(e).__name__}: {e}"}
        profiles, method = [], ""
    attrs["mtime"] = st.st_mtime
    attrs["format"] = path.suffix.lower().lstrip(".")
    attrs["checksum"] = reads.get(_fingerprint, path)
    if kind == "series_file":
        attrs["in_series"] = parent
        attrs["header"] = [p["name"] for p in profiles]
    cat.upsert_asset(key, kind, name, source, parent=parent, attrs=attrs)
    cat.set_columns(key, [{"name": p["name"], "ordinal": p["ordinal"], "data_type": p["data_type"],
                           "nullable": int(p["nulls"] > 0)} for p in profiles])
    if profiles:
        cat.save_profile(key, profiles, method)
    return attrs.get("read_error")


def iter_data_files(root: Path, recursive: bool = True, skip: set[str] | None = None):
    pattern = root.rglob("*") if recursive else root.glob("*")
    for path in pattern:
        if path.suffix.lower() not in DATA_SUFFIXES or not path.is_file():
            continue
        if skip and any(part in skip for part in path.relative_to(root).parts[:-1]):
            continue
        yield path


def scan_folder(cat: Catalogue, name: str, path: str | Path, recursive: bool = True,
                max_rows: int = DEFAULT_MAX_ROWS, report: Report = quiet) -> dict[str, int]:
    """Scan every CSV and Parquet file, Power BI report, Excel workbook and Access database in a folder.
    `report(text, done, total)` hears how far it has got.

    The scan saves as it goes: reports, workbooks, each dated series and every couple of hundred stand-alone
    files are written as soon as they have been read, each in a short transaction. Stopping the scan (or a
    crash) loses only the part being read; the next scan skips what was saved and carries on. Files the
    folder no longer holds are marked missing at the end."""
    from .folderroles import ignored_dirs
    from .scan_access import SUFFIXES as ACCESS_SUFFIXES
    from .scan_excel import WORKBOOK_SUFFIXES
    from .scan_powerbi import REPORT_SUFFIXES
    root = Path(path).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"Folder not found: {root}")
    folder_key = f"files://{name}"
    office_suffixes = (*REPORT_SUFFIXES, ".pbip", *WORKBOOK_SUFFIXES, *ACCESS_SUFFIXES)
    started = time.monotonic()
    report(f"Looking for files in {root} …")
    found, skipped = find_files(root, (*DATA_SUFFIXES, *office_suffixes), recursive, ignored_dirs(cat, root), report)
    from .features import on
    if not on(cat, "access"):
        found.update({s: [] for s in ACCESS_SUFFIXES})
    data = pick(found, DATA_SUFFIXES)
    all_paths = data + pick(found, office_suffixes)
    total = len(all_paths)
    stats = stat_files(all_paths)
    size = size_up(all_paths, stats)
    report(f"Found {total:,} file(s) to read" + (f" (skipped {skipped:,} tool, environment or ignored folders)"
                                                 if skipped else ""), 0, total)
    warning = size_warning(size)
    if warning:
        report(f"  This may take a while: {warning}. Files are read one by one; unchanged files are skipped.")
    plan = plan_reads(cat, root, data, folder_key, name, stats)
    if plan.sampled:
        report(f"  {plan.note}")
    step = Steps(report, total)
    token = cat.begin_scan(name)
    with cat.conn:
        cat.upsert_asset(folder_key, "folder", name, name, attrs={"path": str(root), "recursive": recursive})
    counts = {"files": 0, "series": 0, "reports": 0, "workbooks": 0, "access": 0, "errors": 0}
    try:
        for part in scan_office(cat, root, folder_key, name, found, step):
            for k, v in part.items():
                counts[k] += v
        for part in scan_data(cat, root, folder_key, name, plan, max_rows, step):
            for k, v in part.items():
                counts[k] += v
    except BaseException as e:  # stopped from the app, Ctrl+C, or a failure: what was saved stays saved
        what = "stopped" if isinstance(e, BaseException) and not isinstance(e, Exception) else f"failed: {e}"
        cat.note_stopped_scan(token, {**counts, "seconds": round(time.monotonic() - started, 1),
                                      "files_found": total, "read_seconds": round(step.seconds, 1),
                                      "warning": f"Scan {what} after {step.done:,} of {total:,} files; "
                                                 f"{_saved(counts)} saved. The next scan carries on from there."})
        raise
    report("Updating links and search …", total, total)
    out = {k: counts[k] for k in ("files", "series", "reports", "workbooks", "access", "errors")}
    cat.finish_scan(token, {"seconds": round(time.monotonic() - started, 1), "read_seconds": round(step.seconds, 1),
                            "files_found": total, "bytes": size["bytes"], "skipped_folders": skipped,
                            "slow": step.slow[:20], "errors": out["errors"], "warning": warning,
                            "files_read": total - step.skipped, "unchanged_or_listed": step.skipped,
                            **({"sampled": True, "listed": len(plan.listed)} if plan.sampled else {})})
    return out


def _saved(counts: dict[str, int]) -> str:
    bits = [f"{counts[k]} {label}" for k, label in (("reports", "report(s)"), ("workbooks", "workbook(s)"),
                                                   ("files", "data file(s)")) if counts.get(k)]
    return ", ".join(bits) or "nothing"


@dataclass
class Plan:
    """Which data files a scan opens. In a folder sampled by date (more than `sample_over_files` data files),
    only the newest files of each dated series and the newest stand-alone files are read; the rest are listed
    from the file system (name, size, modified) without being opened."""
    groups: list = field(default_factory=list)   # dated series (series.Series)
    alone: list[str] = field(default_factory=list)  # relative paths of files on their own
    listed: set[Path] = field(default_factory=set)
    sampled: bool = False
    note: str = ""
    stats: dict = field(default_factory=dict)  # path -> os.stat_result, from one pass over the files

    def stat(self, path: Path):
        st = self.stats.get(path)
        if st is None:
            st = self.stats[path] = path.stat()
        return st


def plan_reads(cat: Catalogue, root: Path, data: list[Path], parent_key: str, source: str,
               stats: dict | None = None) -> Plan:
    groups, alone = plan_series(cat, root, data, parent_key, source)
    plan = Plan(groups, alone, stats=stats if stats is not None else {})
    over = int(cat.setting("sample_over_files", SAMPLE_OVER) or 0)
    newest = max(1, int(cat.setting("sample_newest", SAMPLE_NEWEST) or SAMPLE_NEWEST))
    if not (0 < over <= len(data)) or source in cat.setting("read_all_sources", []):
        return plan
    plan.sampled = True
    for g in groups:
        plan.listed |= {root / m.rel for m in g.sorted()[:-newest]}
    if len(alone) > over:
        by_age = sorted(alone, key=lambda rel: _mtime(plan, root / rel), reverse=True)
        plan.listed |= {root / rel for rel in by_age[over:]}
    read = len(data) - len(plan.listed)
    plan.note = (f"{len(data):,} data files, so this folder is sampled by date: the newest {newest} files of each "
                 f"dated series and the newest {over:,} stand-alone files are read ({read:,} in all); the other "
                 f"{len(plan.listed):,} are listed without being opened. Press \"Read every file\" on the folder "
                 "to change that.")
    return plan


def _mtime(plan: Plan, path: Path) -> float:
    try:
        return plan.stat(path).st_mtime
    except OSError:
        return 0.0


def _changed(cat: Catalogue, parent_key: str, root: Path, path: Path, reader: int | None = None,
             plan: Plan | None = None) -> bool:
    """Has this file changed since the catalogue last saw it (size, modified time, or the reader got better)?"""
    prev = cat.get_asset(f"{parent_key}/{path.relative_to(root).as_posix()}")
    try:
        st = plan.stat(path) if plan is not None else path.stat()
    except OSError:
        return False
    return not prev or prev["attrs"].get("not_read") is True or prev["attrs"].get("size_bytes") != st.st_size \
        or prev["attrs"].get("mtime") != st.st_mtime or (reader is not None and prev["attrs"].get("reader") != reader)


def _chunks(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def scan_office(cat: Catalogue, root: Path, parent_key: str, source: str, found: dict[str, list[Path]], step: Steps):
    """Read and save Power BI reports, then Excel workbooks, a few at a time, then Access databases. Yields the
    counts of each saved batch. Unchanged files are skipped without being opened."""
    from .duplicates import fingerprint
    from .scan_access import SUFFIXES as ACCESS_SUFFIXES
    from .scan_excel import READER_VERSION as WORKBOOK_READER
    from .scan_excel import WORKBOOK_SUFFIXES, ingest_workbook, read_workbook
    from .scan_powerbi import READER_VERSION as REPORT_READER
    from .scan_powerbi import REPORT_SUFFIXES, ingest_report, read_report
    sections = (("Power BI report", pick(found, (*REPORT_SUFFIXES, ".pbip")), REPORT_READER, read_report, ingest_report,
                 "reports"),
                ("Excel workbook", pick(found, WORKBOOK_SUFFIXES), WORKBOOK_READER, read_workbook, ingest_workbook,
                 "workbooks"))
    for what, paths, reader, read, ingest, counted in sections:
        for chunk in _chunks(paths, OFFICE_CHUNK):
            reads = Reads()
            for path in chunk:
                rel = path.relative_to(root).as_posix()
                if not _changed(cat, parent_key, root, path, reader):
                    step.skip(f"{what}s", "unchanged files are skipped")
                    continue
                with step(what, rel):
                    reads.load(read, path)
                    if path.is_file():
                        reads.load(fingerprint, path)
            counts = {counted: 0, "errors": 0}
            with cat.conn:
                for path in chunk:
                    rel = path.relative_to(root).as_posix()
                    key = f"{parent_key}/{rel}"
                    cat.conn.execute("DELETE FROM links WHERE source = ? AND src_key = ?", (source, key))
                    counts["errors"] += bool(ingest(cat, path, key, rel, source, parent_key, reads))
                    counts[counted] += 1
            yield counts
    for path in pick(found, ACCESS_SUFFIXES):
        from .scan_access import ingest_access
        rel = path.relative_to(root).as_posix()
        with step("Access database", rel), cat.conn:
            cat.conn.execute("DELETE FROM links WHERE source = ? AND src_key = ?", (source, f"{parent_key}/{rel}"))
            ingest_access(cat, path, f"{parent_key}/{rel}", rel, source, parent_key)
        yield {"access": 1}


def scan_data(cat: Catalogue, root: Path, parent_key: str, source: str, plan: Plan, max_rows: int, step: Steps):
    """Read and save data files: stand-alone files a couple of hundred at a time, then each dated series.
    Yields the counts of each saved batch."""
    from .profiling import quick_info
    by_rel = {root / rel: rel for rel in plan.alone}
    for chunk in _chunks(sorted(by_rel), DATA_CHUNK):
        reads = Reads()
        for path in chunk:
            if path in plan.listed:
                step.skip("data files", "not opened: listed from the file system")
            elif not _changed(cat, parent_key, root, path, plan=plan):
                step.skip("data files", "unchanged files are skipped")
            else:
                with step("data file", by_rel[path]):
                    reads.load(profile_file, path, max_rows)
                    reads.load(_fingerprint, path)
        counts = {"files": 0, "errors": 0}
        with cat.conn:
            for path in chunk:
                rel = by_rel[path]
                error = ingest_data_file(cat, path, f"{parent_key}/{rel}", rel, source, parent_key, max_rows,
                                         reads=reads, listed=path in plan.listed, st=plan.stats.get(path))
                counts["files"] += 1
                counts["errors"] += bool(error)
        yield counts
    for g in plan.groups:
        reads = Reads()
        ordered = g.sorted()
        members = {m.rel: root / m.rel for m in ordered}
        for m in ordered:
            path = members[m.rel]
            if path in plan.listed:
                step.skip("data files", "not opened: older files in dated series are listed from the file system")
            elif not _changed(cat, parent_key, root, path, plan=plan):
                step.skip("data files", "unchanged files are skipped")
            else:
                with step("data file in a dated series", m.rel):
                    if m is ordered[-1]:
                        reads.load(profile_file, path, max_rows)
                    else:
                        reads.load(quick_info, path)
                    reads.load(_fingerprint, path)
        with cat.conn:
            errors = ingest_series(cat, g, members, parent_key, source, max_rows, reads=reads, listed=plan.listed,
                                   stats=plan.stats)
        yield {"files": len(g.members), "series": 1, "errors": errors}


def scanned_recently(cat: Catalogue, source: str, minutes: int | None = None) -> str | None:
    """'12 minutes ago' when this source finished a scan less than `minutes` ago (Settings: recent_scan_minutes,
    0 = never skip), else None. Re-scanning a folder that was scanned a moment ago finds nothing new."""
    from datetime import datetime, timezone
    minutes = int(cat.setting("recent_scan_minutes", RECENT_MINUTES) if minutes is None else minutes)
    if minutes <= 0:
        return None
    last = cat.last_finished_scan(source)
    if not last:
        return None
    try:
        ago = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
    except ValueError:
        return None
    if ago < 0 or ago >= minutes * 60:
        return None
    return "just now" if ago < 90 else f"{int(ago // 60)} minutes ago"


def under(rel: str, folders: set[str]) -> bool:
    """Is this relative path inside one of these relative folders?"""
    low = rel.lower()
    return any(low == f or low.startswith(f + "/") for f in folders)


def ingest_office(cat: Catalogue, root: Path, parent_key: str, source: str, found: dict[str, list[Path]],
                  step: Steps | None = None, reads: Reads | None = None) -> dict[str, int]:
    """Power BI reports, Excel workbooks and Access databases among the files found under a folder
    (Access ones only when that feature is on: scan_folder leaves them out otherwise)."""
    from .scan_access import SUFFIXES as ACCESS_SUFFIXES
    from .scan_excel import WORKBOOK_SUFFIXES, ingest_workbook
    from .scan_powerbi import REPORT_SUFFIXES, ingest_report
    step = step or Steps()
    counts = {"reports": 0, "workbooks": 0, "access": 0, "errors": 0}
    for path in pick(found, (*REPORT_SUFFIXES, ".pbip")):
        rel = path.relative_to(root).as_posix()
        with step("Power BI report", rel):
            counts["errors"] += bool(ingest_report(cat, path, f"{parent_key}/{rel}", rel, source, parent_key, reads))
        counts["reports"] += 1
    for path in pick(found, WORKBOOK_SUFFIXES):
        rel = path.relative_to(root).as_posix()
        with step("Excel workbook", rel):
            counts["errors"] += bool(ingest_workbook(cat, path, f"{parent_key}/{rel}", rel, source, parent_key,
                                                         reads))
        counts["workbooks"] += 1
    for path in pick(found, ACCESS_SUFFIXES):
        from .scan_access import ingest_access
        rel = path.relative_to(root).as_posix()
        with step("Access database", rel):
            ingest_access(cat, path, f"{parent_key}/{rel}", rel, source, parent_key)
        counts["access"] += 1
    return counts


def grouping_on(cat: Catalogue, source: str) -> bool:
    return bool(cat.setting("series_grouping", True)) and source not in cat.setting("series_off_sources", [])


def plan_series(cat: Catalogue, root: Path, paths: list[Path], parent_key: str, source: str) -> tuple[list, list[str]]:
    """(dated series, relative paths of files on their own), leaving out series you split apart."""
    from . import series as S
    rels = [p.relative_to(root).as_posix() for p in paths]
    groups, alone = (S.group(rels) if grouping_on(cat, source) else ([], rels))
    split = set(cat.setting("series_split", []))
    for g in list(groups):
        if S.series_key(parent_key, g.template) in split:
            groups.remove(g)
            alone += [m.rel for m in g.members]
    return groups, alone


def ingest_files(cat: Catalogue, root: Path, paths: list[Path], parent_key: str, source: str,
                 max_rows: int = DEFAULT_MAX_ROWS, step: Steps | None = None, reads: Reads | None = None
                 ) -> dict[str, int]:
    """Catalogue data files under one folder or project, grouping dated files into series."""
    step = step or Steps()
    counts = {"files": 0, "series": 0, "errors": 0}
    by_rel = {p.relative_to(root).as_posix(): p for p in paths}
    plan = plan_reads(cat, root, paths, parent_key, source)
    for rel in sorted(plan.alone):
        with step("data file", rel):
            error = ingest_data_file(cat, by_rel[rel], f"{parent_key}/{rel}", rel, source, parent_key, max_rows,
                                     reads=reads, listed=by_rel[rel] in plan.listed)
        counts["files"] += 1
        counts["errors"] += bool(error)
    for g in plan.groups:
        counts["errors"] += ingest_series(cat, g, by_rel, parent_key, source, max_rows, step, reads, plan.listed)
        counts["files"] += len(g.members)
        counts["series"] += 1
    return counts


def ingest_series(cat: Catalogue, g, by_rel: dict[str, Path], parent_key: str, source: str, max_rows: int,
                  step: Steps | None = None, reads: Reads | None = None, listed: set[Path] | None = None,
                  stats: dict | None = None) -> int:
    """One entry for the whole series. The newest file is profiled in full; the rest only have their
    rows and header read (and are skipped entirely when unchanged since the last scan). Files in `listed`
    (a folder sampled by date) are recorded without being opened."""
    from . import series as S
    from .profiling import quick_info
    step = step or Steps()
    reads = reads or Reads()
    listed = listed or set()
    stats = stats if stats is not None else {}
    skey = S.series_key(parent_key, g.template)
    ordered = g.sorted()
    newest = ordered[-1]
    files, errors = [], 0
    for m in ordered:
        with step("data file in a dated series", m.rel):
            path = by_rel[m.rel]
            key = f"{parent_key}/{m.rel}"
            st = stats.get(path) or path.stat()
            prev = cat.get_asset(key)
            if m is newest:
                error = ingest_data_file(cat, path, key, m.rel, source, skey, max_rows, kind="series_file",
                                         reads=reads, st=st)
                info = cat.get_asset(key)["attrs"]
                cols = [c["name"] for c in cat.columns(key)]
                errors += bool(error)
            elif prev and prev["attrs"].get("size_bytes") == st.st_size and prev["attrs"].get("mtime") == st.st_mtime \
                    and ("header" in prev["attrs"] or path in listed):
                info, cols = prev["attrs"], prev["attrs"].get("header", [])
                cat.upsert_asset(key, "series_file", m.rel, source, parent=skey, attrs=info)
            elif path in listed:
                info = {**file_info(path, st), "mtime": st.st_mtime, "format": path.suffix.lower().lstrip("."),
                        "in_series": skey, "not_read": True}
                cols = []
                cat.upsert_asset(key, "series_file", m.rel, source, parent=skey, attrs=info)
            else:
                try:
                    info = reads.get(quick_info, path)
                except Exception as e:
                    info = {"path": str(path), "size_bytes": st.st_size, "read_error": f"{type(e).__name__}: {e}"}
                    errors += 1
                info.update({"mtime": st.st_mtime, "format": path.suffix.lower().lstrip("."), "in_series": skey,
                             "checksum": reads.get(_fingerprint, path)})
                cols = info.get("header", [])
                cat.upsert_asset(key, "series_file", m.rel, source, parent=skey, attrs=info)
            files.append({"rel": m.rel, "key": key, "period": S._period(m), "rows": info.get("row_count"),
                          "size": info.get("size_bytes"), "modified": info.get("modified"), "columns": cols,
                          **({"not_read": True} if info.get("not_read") else {})})
    changes = S.layout_changes(files)
    changed = {c["file"] for c in changes}
    for f in files:
        f["layout_differs"] = f["rel"] in changed
    newest_attrs = cat.get_asset(f"{parent_key}/{newest.rel}")["attrs"]
    unread = sum(1 for f in files if f.get("not_read"))
    attrs = {**S.describe(g), "files": files, "newest": f"{parent_key}/{newest.rel}",
             "row_count": newest_attrs.get("row_count"), "row_count_exact": newest_attrs.get("row_count_exact", True),
             "total_rows": sum(f["rows"] or 0 for f in files), "size_bytes": sum(f["size"] or 0 for f in files),
             **({"files_not_read": unread} if unread else {}),
             "modified": newest_attrs.get("modified"), "path": str(by_rel[newest.rel].parent),
             "layout_changes": changes, "format": by_rel[newest.rel].suffix.lower().lstrip(".")}
    name = g.template
    cat.upsert_asset(skey, "file_series", name, source, parent=parent_key,
                     description=f"{len(files)} {attrs['frequency']} files, {attrs['first']} to {attrs['last']}",
                     attrs=attrs)
    newest_key = f"{parent_key}/{newest.rel}"
    cat.set_columns(skey, [dict(c) for c in cat.columns(newest_key)])
    profile = cat.profile(newest_key)
    if profile:
        cat.save_profile(skey, [{"name": p["column_name"], "ordinal": p["ordinal"], "data_type": p["data_type"],
                                 "rows": p["row_count"], "nulls": p["null_count"], "distinct": p["distinct_count"],
                                 "min": p["min_value"], "max": p["max_value"], "mean": p["mean_value"],
                                 "top": json.loads(p["top_values"]), "histogram": _j(p["histogram"]),
                                 "percentiles": _j(p["percentiles"]), "hints": _j(p["hints"])} for p in profile],
                         f"newest file ({newest.rel}): " + profile[0]["method"])
    return errors


def _fingerprint(path: Path) -> str | None:
    from .duplicates import fingerprint
    try:
        return fingerprint(path)
    except OSError:
        return None


def _j(v):
    return json.loads(v) if v else None
