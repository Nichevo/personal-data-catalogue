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

import numpy as np
import pandas as pd

DATA_SUFFIXES = (".csv", ".parquet")
DEFAULT_MAX_ROWS = 200_000
TOP_N = 5
BUCKETS = 20
PERCENTILES = (1, 25, 50, 75, 99)


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
            p.update(ranges(s))
            hints = type_hints(s)
            if hints:
                p["hints"] = hints
        out.append(p)
    return out


# ---- range charts ------------------------------------------------------------

def ranges(s: pd.Series) -> dict[str, Any]:
    """A 20-bucket histogram and percentiles for numbers, monthly (or yearly) counts for dates.
    Worked out from the rows already read, so it costs no extra queries."""
    non_null = s.dropna()
    if not len(non_null) or pd.api.types.is_bool_dtype(s):
        return {}
    if pd.api.types.is_datetime64_any_dtype(s):
        return _date_ranges(non_null)
    if pd.api.types.is_numeric_dtype(s):
        values = non_null.astype(float).to_numpy()
    else:
        return {}
    values = values[np.isfinite(values)]
    if not len(values):
        return {}
    lo, hi = float(values.min()), float(values.max())
    if lo == hi:
        counts, edges = np.array([len(values)]), np.array([lo, hi])
    else:
        counts, edges = np.histogram(values, bins=BUCKETS, range=(lo, hi))
    pct = np.percentile(values, PERCENTILES)
    return {"histogram": {"kind": "number", "edges": [float(e) for e in edges], "counts": [int(c) for c in counts]},
            "percentiles": {f"p{k}": _round(v) for k, v in zip(PERCENTILES, pct)}}


def _date_ranges(non_null: pd.Series) -> dict[str, Any]:
    d = pd.to_datetime(non_null, errors="coerce").dropna()
    if not len(d):
        return {}
    if getattr(d.dt, "tz", None) is not None:
        d = d.dt.tz_localize(None)
    span_months = (d.max().year - d.min().year) * 12 + d.max().month - d.min().month
    freq = "Y" if span_months > 120 else "M"
    periods = d.dt.to_period(freq)
    counts = periods.value_counts().sort_index()
    full = pd.period_range(counts.index.min(), counts.index.max(), freq=freq)
    counts = counts.reindex(full, fill_value=0)
    q = d.quantile([k / 100 for k in PERCENTILES])
    return {"histogram": {"kind": "date", "labels": [str(p) for p in counts.index], "counts": [int(c) for c in counts]},
            "percentiles": {f"p{k}": str(v)[:10] for k, v in zip(PERCENTILES, q)}}


def _round(v: float) -> float | int:
    v = float(v)
    return int(v) if v.is_integer() else float(f"{v:.6g}")


# ---- data types that don't fit the data ------------------------------------------

YES_NO = {"y", "n", "yes", "no", "true", "false", "t", "f", "0", "1"}
HINT_TEXT = {
    "text_dates": "text that holds dates",
    "text_numbers": "text that holds numbers",
    "int_yyyymmdd": "whole numbers that look like dates (YYYYMMDD)",
    "float_whole": "decimal type holding only whole numbers",
    "yes_no": "yes/no values stored as {type}",
    "wide_text": "declared much wider than its values",
    "midnight": "date and time, but the time is always midnight",
}


def type_hints(s: pd.Series, declared: str | None = None) -> list[dict[str, str]]:
    """Ways the data type doesn't fit the values, e.g. a text column that is really dates.
    Each hint is {"code", "text"}; the review list turns them into "Data type" tasks."""
    non_null = s.dropna()
    if len(non_null) < 5:
        return []
    out = []
    declared_l = (declared or str(s.dtype)).lower()
    is_text = s.dtype == object or pd.api.types.is_string_dtype(s)
    if is_text and not any(t in declared_l for t in ("date", "time", "int", "decimal", "numeric", "float", "money")):
        text = non_null.astype(str).str.strip()
        text = text[text != ""]
        if len(text) >= 5:
            low = set(text.str.lower().unique())
            if len(low) <= 2 and low <= YES_NO and low - {"0", "1"}:
                out.append(_hint("yes_no", type="text"))
            else:
                as_num = pd.to_numeric(text.str.replace(",", "", regex=False), errors="coerce")
                if as_num.notna().mean() >= 0.95 and not text.str.match(r"^0\d").any():
                    out.append(_hint("text_numbers"))
                elif text.str.contains(r"\d", regex=True).mean() >= 0.95 and \
                        _date_share(text) >= 0.95:
                    out.append(_hint("text_dates"))
        if declared:
            width = _declared_width(declared)
            longest = int(text.str.len().max()) if len(text) else 0
            if width and width >= 50 and longest and width >= 4 * longest:
                out.append({"code": "wide_text", "text": f"declared {declared} but the longest value is {longest} "
                                                          "characters"})
    elif pd.api.types.is_integer_dtype(s) or (pd.api.types.is_float_dtype(s) and _whole(non_null)):
        values = non_null.astype("int64", errors="ignore") if pd.api.types.is_float_dtype(s) else non_null
        if ((values >= 19000101) & (values <= 21001231)).mean() >= 0.95 and \
                pd.to_datetime(values.astype(str), format="%Y%m%d", errors="coerce").notna().mean() >= 0.95:
            out.append(_hint("int_yyyymmdd"))
        elif set(non_null.unique()) <= {0, 1} and non_null.nunique() == 2 and "bit" not in declared_l \
                and "bool" not in declared_l:
            out.append(_hint("yes_no", type="a number"))
        elif pd.api.types.is_float_dtype(s) and not any(t in declared_l for t in ("int",)):
            out.append(_hint("float_whole"))
    elif pd.api.types.is_datetime64_any_dtype(s):
        d = pd.to_datetime(non_null, errors="coerce").dropna()
        if len(d) >= 5 and (d == d.dt.normalize()).all() and "datetime" in (declared or "datetime"):
            out.append(_hint("midnight"))
    return out


def _hint(code: str, **fmt: str) -> dict[str, str]:
    return {"code": code, "text": HINT_TEXT[code].format(**fmt)}


def _whole(values: pd.Series) -> bool:
    try:
        v = values.astype(float)
        return bool(len(v)) and bool(((v % 1) == 0).all()) and bool((v.abs() < 1e15).all())
    except (TypeError, ValueError):
        return False


def _date_share(text: pd.Series) -> float:
    sample = text.head(2000)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y%m%d", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"):
        share = pd.to_datetime(sample, format=fmt, errors="coerce").notna().mean()
        if share >= 0.95:
            return float(share)
    try:
        return float(pd.to_datetime(sample, errors="coerce", format="mixed", dayfirst=True).notna().mean())
    except (TypeError, ValueError):
        return 0.0


def _declared_width(declared: str) -> int | None:
    import re
    m = re.match(r"^n?(var)?char\((\d+)\)$", declared.lower().replace(" ", ""))
    return int(m.group(2)) if m else None


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


def sql_profile_queries(schema: str, table: str, columns: list[dict[str, Any]], sample_rows: int,
                        sample_for_aggregates: int | None = None) -> tuple[str, str]:
    """Build the aggregate query and the sample query for one table.

    The aggregates run over the whole table, or, for big tables, over the first
    `sample_for_aggregates` rows only, so a huge table never costs a full scan.
    """
    full = f"{_q(schema)}.{_q(table)}"
    source = f"{full} WITH (NOLOCK)"
    if sample_for_aggregates:
        source = f"(SELECT TOP ({int(sample_for_aggregates)}) * FROM {full} WITH (NOLOCK)) AS s"
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
    agg = f"SELECT {', '.join(parts)} FROM {source}"
    sample = f"SELECT TOP ({int(sample_rows)}) * FROM {full} WITH (NOLOCK)"
    return agg, sample


def typed_sample(columns: list[dict[str, Any]], sample: pd.DataFrame) -> pd.DataFrame:
    """Give the rows read from SQL Server proper types (decimals arrive as Python objects)."""
    sample = sample.copy()
    for c in columns:
        name, base = c["name"], (c.get("data_type") or "").split("(")[0].lower()
        if name not in sample.columns:
            continue
        if base in ("decimal", "numeric", "money", "smallmoney", "float", "real", "int", "bigint", "smallint",
                    "tinyint"):
            sample[name] = pd.to_numeric(sample[name], errors="coerce")
        elif base in ("date", "datetime", "datetime2", "smalldatetime", "datetimeoffset"):
            sample[name] = pd.to_datetime(sample[name], errors="coerce", utc=base == "datetimeoffset")
    return sample


def sample_profile(columns: list[dict[str, Any]], sample: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if not len(sample.columns):
        return {}
    sample = typed_sample(columns, sample)
    declared = {c["name"]: c.get("data_type") for c in columns}
    out = {}
    for p in profile_dataframe(sample):
        p["hints"] = type_hints(sample[p["name"]], declared.get(p["name"]))
        out[p["name"]] = p
    return out


def combine_sql_profile(columns: list[dict[str, Any]], agg: dict[str, Any], sample: pd.DataFrame) -> list[dict[str, Any]]:
    """Merge the aggregates with the sample-based profile (used for mean, top values and range charts)."""
    sampled = sample_profile(columns, sample)
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
            "histogram": s.get("histogram"), "percentiles": s.get("percentiles"), "hints": s.get("hints"),
        })
    return out
