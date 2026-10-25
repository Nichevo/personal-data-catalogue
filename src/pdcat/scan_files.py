"""CSV and Parquet files as data sources.

Files are found inside Python projects (alongside the code that writes them)
and in any extra folders you point the catalogue at. Each file records its
size, modified date, row count, columns and a value profile.
"""
from __future__ import annotations

from pathlib import Path

from .profiling import DATA_SUFFIXES, DEFAULT_MAX_ROWS, profile_file
from .store import Catalogue


def ingest_data_file(cat: Catalogue, path: Path, key: str, name: str, source: str, parent: str,
                     max_rows: int = DEFAULT_MAX_ROWS) -> str | None:
    """Catalogue one data file. Unchanged files (same size and modified time) reuse their last profile.
    Returns an error message if the file could not be read."""
    st = path.stat()
    previous = cat.get_asset(key)
    if previous and previous["attrs"].get("size_bytes") == st.st_size \
            and previous["attrs"].get("mtime") == st.st_mtime and cat.profile(key):
        cat.upsert_asset(key, "data_file", name, source, parent=parent, attrs=previous["attrs"])
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
    cat.upsert_asset(key, "data_file", name, source, parent=parent, attrs=attrs)
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
    counts = {"files": 0, "errors": 0}
    with cat.scan(name):
        cat.upsert_asset(folder_key, "folder", name, name, attrs={"path": str(root), "recursive": recursive})
        for f in sorted(iter_data_files(root, recursive)):
            rel = f.relative_to(root).as_posix()
            error = ingest_data_file(cat, f, f"{folder_key}/{rel}", rel, name, folder_key, max_rows)
            counts["files"] += 1
            counts["errors"] += bool(error)
    return counts
