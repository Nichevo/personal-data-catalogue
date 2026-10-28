"""CSV and Parquet files as data sources.

Files are found inside Python projects (alongside the code that writes them)
and in any extra folders you point the catalogue at. Each file records its
size, modified date, row count, columns and a value profile.

Files that differ only by a date (sales_2026-01.csv, sales_2026-02.csv…)
become one dated series: see series.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from .profiling import DATA_SUFFIXES, DEFAULT_MAX_ROWS, profile_file
from .scanprogress import Report, Steps, find_files, pick, quiet
from .store import Catalogue


def ingest_data_file(cat: Catalogue, path: Path, key: str, name: str, source: str, parent: str,
                     max_rows: int = DEFAULT_MAX_ROWS, kind: str = "data_file") -> str | None:
    """Catalogue one data file. Unchanged files (same size and modified time) reuse their last profile.
    Returns an error message if the file could not be read."""
    st = path.stat()
    previous = cat.get_asset(key)
    if previous and previous["attrs"].get("size_bytes") == st.st_size \
            and previous["attrs"].get("mtime") == st.st_mtime and cat.profile(key):
        attrs = previous["attrs"]
        if "checksum" not in attrs:
            attrs["checksum"] = _fingerprint(path)
        cat.upsert_asset(key, kind, name, source, parent=parent, attrs=attrs)
        cat.set_columns(key, [{"name": p["column_name"], "ordinal": p["ordinal"], "data_type": p["data_type"]}
                              for p in cat.profile(key)])
        return previous["attrs"].get("read_error")

    try:
        attrs, profiles, method = profile_file(path, max_rows)
    except Exception as e:  # a malformed file must not stop the scan
        attrs = {"path": str(path), "size_bytes": st.st_size, "read_error": f"{type(e).__name__}: {e}"}
        profiles, method = [], ""
    attrs["mtime"] = st.st_mtime
    attrs["format"] = path.suffix.lower().lstrip(".")
    attrs["checksum"] = _fingerprint(path)
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
    `report(text, done, total)` hears how far it has got."""
    from .folderroles import ignored_dirs
    from .scan_access import SUFFIXES as ACCESS_SUFFIXES
    from .scan_excel import WORKBOOK_SUFFIXES
    from .scan_powerbi import REPORT_SUFFIXES
    root = Path(path).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"Folder not found: {root}")
    folder_key = f"files://{name}"
    office_suffixes = (*REPORT_SUFFIXES, ".pbip", *WORKBOOK_SUFFIXES, *ACCESS_SUFFIXES)
    report(f"Looking for files in {root} …")
    found, skipped = find_files(root, (*DATA_SUFFIXES, *office_suffixes), recursive, ignored_dirs(cat, root), report)
    from .features import on
    if not on(cat, "access"):
        found.update({s: [] for s in ACCESS_SUFFIXES})
    data = pick(found, DATA_SUFFIXES)
    total = len(data) + len(pick(found, office_suffixes))
    report(f"Found {total:,} file(s) to read" + (f" (skipped {skipped:,} tool, environment or ignored folders)"
                                                 if skipped else ""), 0, total)
    with cat.scan(name):
        cat.upsert_asset(folder_key, "folder", name, name, attrs={"path": str(root), "recursive": recursive})
        step = Steps(report, total)
        counts = ingest_files(cat, root, data, folder_key, name, max_rows, step)
        office = ingest_office(cat, root, folder_key, name, found, step)
        counts = {**office, **counts, "errors": counts["errors"] + office["errors"]}
        report("Saving and updating links and search …", total, total)
    return {k: counts[k] for k in ("files", "series", "reports", "workbooks", "access", "errors")}


def under(rel: str, folders: set[str]) -> bool:
    """Is this relative path inside one of these relative folders?"""
    low = rel.lower()
    return any(low == f or low.startswith(f + "/") for f in folders)


def ingest_office(cat: Catalogue, root: Path, parent_key: str, source: str, found: dict[str, list[Path]],
                  step: Steps | None = None) -> dict[str, int]:
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
            counts["errors"] += bool(ingest_report(cat, path, f"{parent_key}/{rel}", rel, source, parent_key))
        counts["reports"] += 1
    for path in pick(found, WORKBOOK_SUFFIXES):
        rel = path.relative_to(root).as_posix()
        with step("Excel workbook", rel):
            counts["errors"] += bool(ingest_workbook(cat, path, f"{parent_key}/{rel}", rel, source, parent_key))
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


def ingest_files(cat: Catalogue, root: Path, paths: list[Path], parent_key: str, source: str,
                 max_rows: int = DEFAULT_MAX_ROWS, step: Steps | None = None) -> dict[str, int]:
    """Catalogue data files under one folder or project, grouping dated files into series."""
    from . import series as S
    step = step or Steps()
    counts = {"files": 0, "series": 0, "errors": 0}
    by_rel = {p.relative_to(root).as_posix(): p for p in paths}
    groups, alone = (S.group(list(by_rel)) if grouping_on(cat, source) else ([], list(by_rel)))
    split = set(cat.setting("series_split", []))
    for g in list(groups):
        if S.series_key(parent_key, g.template) in split:
            groups.remove(g)
            alone += [m.rel for m in g.members]
    for rel in sorted(alone):
        with step("data file", rel):
            error = ingest_data_file(cat, by_rel[rel], f"{parent_key}/{rel}", rel, source, parent_key, max_rows)
        counts["files"] += 1
        counts["errors"] += bool(error)
    for g in groups:
        counts["errors"] += ingest_series(cat, g, by_rel, parent_key, source, max_rows, step)
        counts["files"] += len(g.members)
        counts["series"] += 1
    return counts


def ingest_series(cat: Catalogue, g, by_rel: dict[str, Path], parent_key: str, source: str, max_rows: int,
                  step: Steps | None = None) -> int:
    """One entry for the whole series. The newest file is profiled in full; the rest only have their
    rows and header read (and are skipped entirely when unchanged since the last scan)."""
    from . import series as S
    from .profiling import quick_info
    step = step or Steps()
    skey = S.series_key(parent_key, g.template)
    ordered = g.sorted()
    newest = ordered[-1]
    files, errors = [], 0
    for m in ordered:
        with step("data file in a dated series", m.rel):
            path = by_rel[m.rel]
            key = f"{parent_key}/{m.rel}"
            st = path.stat()
            prev = cat.get_asset(key)
            if m is newest:
                error = ingest_data_file(cat, path, key, m.rel, source, skey, max_rows, kind="series_file")
                info = cat.get_asset(key)["attrs"]
                cols = [c["name"] for c in cat.columns(key)]
                errors += bool(error)
            elif prev and prev["attrs"].get("size_bytes") == st.st_size and prev["attrs"].get("mtime") == st.st_mtime \
                    and "header" in prev["attrs"]:
                info, cols = prev["attrs"], prev["attrs"]["header"]
                cat.upsert_asset(key, "series_file", m.rel, source, parent=skey, attrs=info)
            else:
                try:
                    info = quick_info(path)
                except Exception as e:
                    info = {"path": str(path), "size_bytes": st.st_size, "read_error": f"{type(e).__name__}: {e}"}
                    errors += 1
                info.update({"mtime": st.st_mtime, "format": path.suffix.lower().lstrip("."), "in_series": skey,
                             "checksum": _fingerprint(path)})
                cols = info.get("header", [])
                cat.upsert_asset(key, "series_file", m.rel, source, parent=skey, attrs=info)
            files.append({"rel": m.rel, "key": key, "period": S._period(m), "rows": info.get("row_count"),
                          "size": info.get("size_bytes"), "modified": info.get("modified"), "columns": cols})
    changes = S.layout_changes(files)
    changed = {c["file"] for c in changes}
    for f in files:
        f["layout_differs"] = f["rel"] in changed
    newest_attrs = cat.get_asset(f"{parent_key}/{newest.rel}")["attrs"]
    attrs = {**S.describe(g), "files": files, "newest": f"{parent_key}/{newest.rel}",
             "row_count": newest_attrs.get("row_count"), "row_count_exact": newest_attrs.get("row_count_exact", True),
             "total_rows": sum(f["rows"] or 0 for f in files), "size_bytes": sum(f["size"] or 0 for f in files),
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
