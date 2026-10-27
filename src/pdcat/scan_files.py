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
                max_rows: int = DEFAULT_MAX_ROWS) -> dict[str, int]:
    """Scan every CSV and Parquet file in a folder."""
    root = Path(path).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"Folder not found: {root}")
    folder_key = f"files://{name}"
    with cat.scan(name):
        cat.upsert_asset(folder_key, "folder", name, name, attrs={"path": str(root), "recursive": recursive})
        counts = ingest_files(cat, root, sorted(iter_data_files(root, recursive)), folder_key, name, max_rows)
    return {"files": counts["files"], "series": counts["series"], "errors": counts["errors"]}


def grouping_on(cat: Catalogue, source: str) -> bool:
    return bool(cat.setting("series_grouping", True)) and source not in cat.setting("series_off_sources", [])


def ingest_files(cat: Catalogue, root: Path, paths: list[Path], parent_key: str, source: str,
                 max_rows: int = DEFAULT_MAX_ROWS) -> dict[str, int]:
    """Catalogue data files under one folder or project, grouping dated files into series."""
    from . import series as S
    counts = {"files": 0, "series": 0, "errors": 0}
    by_rel = {p.relative_to(root).as_posix(): p for p in paths}
    groups, alone = (S.group(list(by_rel)) if grouping_on(cat, source) else ([], list(by_rel)))
    split = set(cat.setting("series_split", []))
    for g in list(groups):
        if S.series_key(parent_key, g.template) in split:
            groups.remove(g)
            alone += [m.rel for m in g.members]
    for rel in sorted(alone):
        error = ingest_data_file(cat, by_rel[rel], f"{parent_key}/{rel}", rel, source, parent_key, max_rows)
        counts["files"] += 1
        counts["errors"] += bool(error)
    for g in groups:
        counts["errors"] += ingest_series(cat, g, by_rel, parent_key, source, max_rows)
        counts["files"] += len(g.members)
        counts["series"] += 1
    return counts


def ingest_series(cat: Catalogue, g, by_rel: dict[str, Path], parent_key: str, source: str, max_rows: int) -> int:
    """One entry for the whole series. The newest file is profiled in full; the rest only have their
    rows and header read (and are skipped entirely when unchanged since the last scan)."""
    from . import series as S
    from .profiling import quick_info
    skey = S.series_key(parent_key, g.template)
    ordered = g.sorted()
    newest = ordered[-1]
    files, errors = [], 0
    for m in ordered:
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
