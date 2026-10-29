"""Text that may arrive as something else.

pbixray hands back pandas frames, and an empty cell in one is NaN (a float), not "". A measure with no
expression, a query with no body or a missing description then reaches code that expects a string and
fails with "'float' object has no attribute 'strip'". Everything that takes such a value goes through
`as_text` first; catalogues scanned before this fix can hold NaN in their stored attributes too.
"""
from __future__ import annotations

from typing import Any


def as_text(value: Any) -> str:
    """The value as a string: None, NaN and other non-strings become "" or their text."""
    if value is None:
        return ""
    if isinstance(value, float):
        return "" if value != value else str(value)
    if isinstance(value, str):
        return value
    try:
        import pandas as pd
        if pd.isna(value):  # pandas NA / NaT
            return ""
    except (TypeError, ValueError, ImportError):
        pass
    return str(value)
