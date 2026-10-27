"""Small range charts for a column: a 20-bar histogram for numbers, monthly counts for dates."""
from __future__ import annotations

import json

import altair as alt
import pandas as pd
import streamlit as st

from .common import loads

BAR = "#4a73c9"


def histogram_frame(hist: dict) -> pd.DataFrame:
    if hist.get("kind") == "date":
        return pd.DataFrame({"bucket": hist["labels"], "rows": hist["counts"], "order": range(len(hist["counts"]))})
    edges = hist["edges"]
    labels = [f"{_fmt(edges[i])} – {_fmt(edges[i + 1])}" for i in range(len(hist["counts"]))]
    return pd.DataFrame({"bucket": labels, "rows": hist["counts"], "order": range(len(labels))})


def _fmt(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if v.is_integer():
        return f"{int(v):,}"
    return f"{v:,.3g}" if abs(v) < 1000 else f"{v:,.0f}"


def range_chart(p, height: int = 110) -> None:
    hist = loads(p["histogram"] if not isinstance(p, dict) else p.get("histogram"), None)
    if isinstance(hist, str):
        hist = json.loads(hist)
    if not hist or not hist.get("counts"):
        return
    df = histogram_frame(hist)
    chart = alt.Chart(df).mark_bar(color=BAR).encode(
        x=alt.X("bucket:N", sort=alt.SortField("order"), axis=alt.Axis(labels=False, ticks=False, title=None)),
        y=alt.Y("rows:Q", axis=alt.Axis(labels=False, ticks=False, title=None, grid=False)),
        tooltip=[alt.Tooltip("bucket:N", title="range"), alt.Tooltip("rows:Q", format=",")],
    ).properties(height=height)
    st.altair_chart(chart, width="stretch")
    pct = loads(p["percentiles"] if not isinstance(p, dict) else p.get("percentiles"), None)
    if pct:
        st.caption(" · ".join(f"{k} {_fmt(v) if hist.get('kind') != 'date' else v}" for k, v in pct.items()))
