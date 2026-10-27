"""Drafted text for the "About this item" form, worked out from what the scan found.
No AI service is used: it's plain rules over names, keys, dates and links."""
from __future__ import annotations

import re
from typing import Any

from .store import Catalogue

ID_COL = re.compile(r"(^id$|_?id$|_?key$|_?code$|_?no$|number$)", re.I)
DATE_COL = re.compile(r"(date|day|week|month|period|_dt$|time|year)", re.I)


def _words(name: str) -> str:
    name = name.split("/")[-1].split(".")[-1] if "." in name else name.split("/")[-1]
    name = re.sub(r"\.(csv|parquet|txt|xlsx?)$", "", name, flags=re.I)
    name = re.sub(r"^(v|vw|tbl|t|usp|sp|fn|stg|dim|fact)_?(?=[A-Z_])", "", name)
    return re.sub(r"(?<=[a-z])(?=[A-Z])|_", " ", name).strip().lower()


def draft_info(cat: Catalogue, key: str) -> dict[str, Any]:
    a = cat.get_asset(key)
    if a is None:
        return {}
    out: dict[str, Any] = {}
    attrs = a["attrs"]
    cols = [c["name"] for c in cat.columns(key)]
    writers = [l["src_name"] or l["src_key"] for l in cat.links_to(key) if l["relation"] == "writes"]
    readers = [l["src_name"] or l["src_key"] for l in cat.links_to(key) if l["relation"] != "writes"]
    if a["kind"] in ("table", "view", "data_file", "file_series"):
        bits = []
        if a["description"]:
            bits.append(a["description"].strip().splitlines()[0])
        else:
            bits.append(f"{_words(a['name']).capitalize()} data.")
        if writers:
            bits.append(f"Created or loaded by {', '.join(dict.fromkeys(writers[:3]))}.")
        if readers:
            bits.append(f"Used by {len(set(readers))} item(s), e.g. {', '.join(list(dict.fromkeys(readers))[:3])}.")
        out["purpose"] = " ".join(bits)
        pk = attrs.get("primary_key") or []
        ids = pk or [c for c in cols if ID_COL.search(c)][:2]
        dates = [c for c in cols if DATE_COL.search(c) and c not in ids][:1]
        if ids or dates:
            out["grain"] = " and ".join([*ids, *dates])
        if attrs.get("frequency"):
            out["refresh"] = attrs["frequency"]
    elif a["kind"] in ("script", "notebook", "sql_script", "procedure"):
        reads = [l["dst_name"] or l["dst_ref"] for l in cat.links_from(key) if l["relation"] == "reads"]
        writes = [l["dst_name"] or l["dst_ref"] for l in cat.links_from(key) if l["relation"] == "writes"]
        bits = [a["description"].strip().splitlines()[0]] if a["description"] else []
        if reads:
            bits.append(f"Reads {', '.join(list(dict.fromkeys(reads))[:3])}.")
        if writes:
            bits.append(f"Writes {', '.join(list(dict.fromkeys(writes))[:3])}.")
        out["purpose"] = " ".join(bits)
    return {k: v for k, v in out.items() if v}
