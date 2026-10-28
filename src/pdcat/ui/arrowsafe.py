"""Keep the app working when the pyarrow library can't load.

Streamlit sends tables, editable grids and charts to the browser through pyarrow. On some Windows PCs
pyarrow is installed but its DLLs won't load ("DLL load failed while importing lib"), usually because
pip and conda versions got mixed or the Microsoft Visual C++ runtime is old. Then every table crashes
its page. When that happens, these stand-ins take over: tables are drawn as plain HTML, a row of a
clickable table is picked from a drop-down instead, editable grids are shown read-only, and charts are
left out. The sidebar says what happened and how to repair it.

Set PDCAT_NO_ARROW=1 to try the stand-ins on a PC where pyarrow works.
"""
from __future__ import annotations

import functools
import os
from types import SimpleNamespace
from typing import Any

import pandas as pd
import streamlit as st
from streamlit.delta_generator import DeltaGenerator

MAX_ROWS = 500
FIX = ("To repair it: close the app, open Anaconda Prompt, run `conda activate pdcat`, then "
       "`pip uninstall -y pyarrow` and `conda remove -y --force pyarrow pyarrow-core`. Now "
       "`python -c \"import pyarrow; print(pyarrow.__path__)\"` should fail; if it prints a folder instead, delete "
       "that folder. Then `conda install -y -c conda-forge pyarrow` and check with "
       "`python -c \"import pyarrow; print(pyarrow.table({'a': [1]}))\"`. If a DLL error remains, install the "
       "latest Microsoft Visual C++ Redistributable (x64).")


@functools.lru_cache(maxsize=1)
def arrow_error() -> str | None:
    """Why pyarrow can't be used, or None when it loads fine."""
    if os.environ.get("PDCAT_NO_ARROW"):
        return "turned off with PDCAT_NO_ARROW"
    try:
        import pyarrow
        import pyarrow.lib  # noqa: F401
    except (ImportError, OSError, AttributeError) as e:
        return f"{type(e).__name__}: {e}"
    # A half-removed install leaves an empty pyarrow folder that still imports, but holds nothing.
    if getattr(pyarrow, "__file__", None) is None or not hasattr(pyarrow, "table"):
        where = ", ".join(str(p) for p in getattr(pyarrow, "__path__", []))
        return f"pyarrow is only partly installed (an empty or broken folder at {where or 'an unknown place'})"
    return None


def _is_arrow_failure(e: BaseException) -> bool:
    if isinstance(e, (ImportError, OSError)):
        return True
    return isinstance(e, AttributeError) and "pyarrow" in str(e)  # "module 'pyarrow' has no attribute 'table'"


def _frame(data: Any) -> pd.DataFrame:
    if data is None:
        return pd.DataFrame()
    if hasattr(data, "data") and hasattr(data, "to_html") and not isinstance(data, pd.DataFrame):
        data = data.data  # a pandas Styler
    if isinstance(data, pd.DataFrame):
        return data
    if isinstance(data, pd.Series):
        return data.to_frame()
    try:
        return pd.DataFrame(data)
    except (ValueError, TypeError):
        return pd.DataFrame({"value": [str(data)]})


def _html(dg: DeltaGenerator, df: pd.DataFrame, hide_index: bool | None) -> None:
    shown = df.head(MAX_ROWS)
    table = shown.to_html(index=not hide_index, escape=True, na_rep="", border=0)
    dg.markdown(f'<div style="max-height:420px;overflow:auto;font-size:0.85rem">{table}</div>', unsafe_allow_html=True)
    if len(df) > MAX_ROWS:
        dg.caption(f"Showing the first {MAX_ROWS} of {len(df):,} rows.")


def _label(df: pd.DataFrame, i: int) -> str:
    row = df.iloc[i]
    texts = [str(v) for v in row.values if isinstance(v, str) and v.strip()]
    return " · ".join(texts[:2])[:120] or f"row {i + 1}"


def _picked(key: str) -> None:
    v = st.session_state.get(key)
    st.session_state[f"{key}-picked"] = v
    st.session_state[key] = None


def dataframe(self: DeltaGenerator, data: Any = None, *args: Any, hide_index: bool | None = None,
              key: str | None = None, on_select: Any = "ignore", **kwargs: Any) -> Any:
    df = _frame(data)
    _html(self, df, hide_index)
    if on_select == "ignore" or not len(df):
        return self if on_select == "ignore" else SimpleNamespace(selection=SimpleNamespace(rows=[], columns=[]))
    k = f"arrowsafe-{key or id(data)}"
    n = min(len(df), MAX_ROWS)
    self.selectbox("Open a row", [None, *range(n)], key=k, format_func=lambda i: "Open a row…" if i is None else
                   _label(df, i), label_visibility="collapsed", on_change=_picked, args=(k,))
    picked = st.session_state.pop(f"{k}-picked", None)
    rows = [picked] if picked is not None else []
    if callable(on_select) and rows:
        on_select()
    return SimpleNamespace(selection=SimpleNamespace(rows=rows, columns=[]))


def data_editor(self: DeltaGenerator, data: Any = None, *args: Any, hide_index: bool | None = None,
                **kwargs: Any) -> Any:
    _html(self, _frame(data), hide_index)
    self.caption("Editing in a grid needs the pyarrow library, which can't load on this PC (see the note in the "
                 "sidebar), so this is read-only for now.")
    return data.copy() if hasattr(data, "copy") else data


def table(self: DeltaGenerator, data: Any = None, *args: Any, **kwargs: Any) -> DeltaGenerator:
    _html(self, _frame(data), False)
    return self


def chart(self: DeltaGenerator, *args: Any, **kwargs: Any) -> DeltaGenerator:
    self.caption("📉 Chart left out: charts need the pyarrow library, which can't load on this PC.")
    return self


STAND_INS = {"dataframe": dataframe, "data_editor": data_editor, "table": table,
             **{name: chart for name in ("bar_chart", "line_chart", "area_chart", "scatter_chart", "altair_chart",
                                         "vega_lite_chart")}}


_ORIGINALS: dict[str, Any] = {}


_RUNTIME_ERROR: list[str] = []


def _guard(name: str, original: Any, stand_in: Any) -> Any:
    """Streamlit's own function, switching to the stand-in for good if pyarrow fails while drawing."""
    @functools.wraps(original)
    def run(self: DeltaGenerator, *args: Any, **kwargs: Any) -> Any:
        if _RUNTIME_ERROR:
            return stand_in(self, *args, **kwargs)
        try:
            return original(self, *args, **kwargs)
        except (ImportError, OSError, AttributeError) as e:  # "DLL load failed while importing lib", and the like
            if not _is_arrow_failure(e):
                raise
            _RUNTIME_ERROR.append(f"{type(e).__name__}: {e}")
            return stand_in(self, *args, **kwargs)
    return run


def install() -> bool:
    """Use the stand-ins when pyarrow can't load; otherwise guard Streamlit's functions so a pyarrow failure
    while drawing falls back too. Returns True when the stand-ins are in use from the start."""
    if getattr(DeltaGenerator, "_pdcat_arrowsafe", False):
        return arrow_error() is not None
    broken = arrow_error() is not None
    for name, fn in STAND_INS.items():
        _ORIGINALS[name] = getattr(DeltaGenerator, name)
        setattr(DeltaGenerator, name, fn if broken else _guard(name, _ORIGINALS[name], fn))
        # st.dataframe and friends are bound to the main area when Streamlit is imported
        main = getattr(st, "_main", None)
        if main is not None:
            setattr(st, name, getattr(main, name))
    DeltaGenerator._pdcat_arrowsafe = True
    return broken


def uninstall() -> None:
    """Put Streamlit's own functions back (for tests)."""
    for name, fn in _ORIGINALS.items():
        setattr(DeltaGenerator, name, fn)
        main = getattr(st, "_main", None)
        if main is not None:
            setattr(st, name, getattr(main, name))
    _ORIGINALS.clear()
    _RUNTIME_ERROR.clear()
    DeltaGenerator._pdcat_arrowsafe = False
    arrow_error.cache_clear()


def sidebar_note() -> None:
    err = arrow_error() or (_RUNTIME_ERROR[0] if _RUNTIME_ERROR else None)
    if err:
        with st.sidebar.expander("⚠️ Tables shown in a simple form", expanded=False):
            st.caption("The pyarrow library can't load on this PC, so tables are plain, rows are picked from a "
                       "drop-down, grids are read-only and charts are left out. " + FIX)
            st.caption(f"Details: {err[:300]}")
