"""Value profiles: row counts, nulls, distinct counts, min/max, mean and the most common values.

Data files are profiled with pandas. SQL tables get exact counts and ranges from
one aggregate query over the whole table, plus most-common values from a
sample of rows, so even big tables cost one scan.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

DATA_SUFFIXES = (".csv", ".parquet")
DEFAULT_MAX_ROWS = 200_000
TOP_N = 5


def profile_dataframe(df: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for i, col in enumerate(df.columns, start=1):
        s = df[col]
        p: dict[str, Any] = {"name": str(col), "ordinal": i, "data_type": str(s.dtype),
                             "rows": int(len(s)), "nulls": int(s.isna().sum())}
        non_null = s.dropna()
        try:
            p["distinct"] = int(non_null.nunique())
        except TypeError:  # unhashable values such as lists in parquet
            p["distinct"] = None
        if len(non_null):
            if pd.api.types.is_bool_dtype(s):
                pass
            elif pd.api.types.is_numeric_dtype(s) or pd.api.types.is_datetime64_any_dtype(s):
                p["min"], p["max"] = non_null.min(), non_null.max()
                if pd.api.types.is_numeric_dtype(s):
                    p["mean"] = float(non_null.mean())
            else:
                as_text = non_null.astype(str)
                p["min"], p["max"] = as_text.min(), as_text.max()
            try:
                counts = non_null.astype(str).value_counts().head(TOP_N)
                p["top"] = [[v, int(c)] for v, c in counts.items()]
            except TypeError:
                p["top"] = []
        out.append(p)
    return out


def file_info(path: Path) -> dict[str, Any]:
    st = path.stat()
    return {
        "path": str(path),
        "size_bytes": st.st_size,
        "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds"),
    }


def read_data_file(path: Path, max_rows: int = DEFAULT_MAX_ROWS) -> tuple[pd.DataFrame, int | None, bool]:
    """Read up to max_rows of a CSV or Parquet file.

    Returns (frame, total_rows, exact). total_rows is exact for Parquet (from
    its metadata) and for CSVs that fit in max_rows; for bigger CSVs it is a
    line count, which can be off if values contain line breaks.
    """
    if path.suffix.lower() == ".parquet":
        import pyarrow.parquet as pq

        pf = pq.ParquetFile(path)
        total = pf.metadata.num_rows
        batches = []
        got = 0
        for batch in pf.iter_batches(batch_size=min(max_rows, 65_536)):
            batches.append(batch.to_pandas())
            got += batch.num_rows
            if got >= max_rows:
                break
        df = pd.concat(batches, ignore_index=True).head(max_rows) if batches else pf.schema_arrow.empty_table().to_pandas()
        return df, total, True

    df = _read_csv(path, max_rows + 1)
    if len(df) <= max_rows:
        return df, len(df), True
    return df.head(max_rows), _count_lines(path) - 1, False


def _read_csv(path: Path, nrows: int) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "cp1252"):  # Excel on Windows often saves cp1252
        try:
            with path.open(encoding=encoding) as f:
                head = f.read(64 * 1024)
            return pd.read_csv(path, nrows=nrows, sep=_sniff_delimiter(head), encoding=encoding, low_memory=False)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path, nrows=nrows, encoding="latin-1", low_memory=False)


def _sniff_delimiter(head: str) -> str:
    try:
        return csv.Sniffer().sniff(head, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _count_lines(path: Path) -> int:
    n = 0
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            n += chunk.count(b"\n")
    if os.path.getsize(path) and not _ends_with_newline(path):
        n += 1
    return n


def _ends_with_newline(path: Path) -> bool:
    with path.open("rb") as f:
        f.seek(-1, os.SEEK_END)
        return f.read(1) == b"\n"


def profile_file(path: Path, max_rows: int = DEFAULT_MAX_ROWS) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """Return (attrs, column profiles, method description) for a data file."""
    attrs = file_info(path)
    df, total, exact = read_data_file(path, max_rows)
    attrs["row_count"] = total
    attrs["row_count_exact"] = exact
    attrs["column_count"] = len(df.columns)
    profiles = profile_dataframe(df)
    if len(df) < (total or 0):
        method = f"first {len(df):,} of {total:,} rows"
    else:
        method = "whole file"
    return attrs, profiles, method


# ---- SQL Server ------------------------------------------------------------

_NO_RANGE_TYPES = {"text", "ntext", "image", "xml", "geography", "geometry", "hierarchyid", "sql_variant",
                   "varbinary", "binary", "timestamp", "rowversion", "bit", "uniqueidentifier"}


def _q(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def sql_profile_queries(schema: str, table: str, columns: list[dict[str, Any]], sample_rows: int) -> tuple[str, str]:
    """Build the aggregate query (whole table) and the sample query for one table."""
    full = f"{_q(schema)}.{_q(table)}"
    parts = ["COUNT_BIG(*) AS [__rows]"]
    for i, c in enumerate(columns):
        col = _q(c["name"])
        base = (c.get("data_type") or "").split("(")[0].lower()
        parts.append(f"COUNT_BIG({col}) AS [n{i}]" if base not in ("text", "ntext", "image", "xml") else f"NULL AS [n{i}]")
        is_max = "(max)" in (c.get("data_type") or "").lower()
        if base in _NO_RANGE_TYPES or is_max:
            parts += [f"NULL AS [d{i}]", f"NULL AS [lo{i}]", f"NULL AS [hi{i}]"]
        else:
            parts += [f"COUNT_BIG(DISTINCT {col}) AS [d{i}]",
                      f"CAST(MIN({col}) AS nvarchar(200)) AS [lo{i}]", f"CAST(MAX({col}) AS nvarchar(200)) AS [hi{i}]"]
    agg = f"SELECT {', '.join(parts)} FROM {full} WITH (NOLOCK)"
    sample = f"SELECT TOP ({int(sample_rows)}) * FROM {full} WITH (NOLOCK)"
    return agg, sample


def combine_sql_profile(columns: list[dict[str, Any]], agg: dict[str, Any], sample: pd.DataFrame) -> list[dict[str, Any]]:
    """Merge exact whole-table aggregates with the sample-based profile (used for mean and top values)."""
    sampled = {p["name"]: p for p in profile_dataframe(sample)} if len(sample.columns) else {}
    rows = int(agg["__rows"])
    out = []
    for i, c in enumerate(columns):
        s = sampled.get(c["name"], {})
        non_null = agg.get(f"n{i}")
        out.append({
            "name": c["name"], "ordinal": c.get("ordinal", i + 1), "data_type": c.get("data_type"),
            "rows": rows,
            "nulls": rows - int(non_null) if non_null is not None else s.get("nulls"),
            "distinct": agg.get(f"d{i}") if agg.get(f"d{i}") is not None else None,
            "min": agg.get(f"lo{i}"), "max": agg.get(f"hi{i}"),
            "mean": s.get("mean"), "top": s.get("top", []),
        })
    return out
