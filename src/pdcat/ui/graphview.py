"""The interactive graph viewer (Cytoscape with ELK layout) as a Streamlit component.

The viewer is plain HTML and JavaScript in ui/graph, with its libraries copied
into ui/graph/vendor, so nothing is fetched from the internet. Clicking a box
opens that item, double-clicking a script opens its functions, and the groups
you open are remembered per view.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from pdcat.store import Catalogue

from .common import go

_viewer = components.declare_component("pdcat_graph", path=str(Path(__file__).with_name("graph")))


def graph_view(cat: Catalogue, elements: list[dict[str, Any]], *, key: str, height: int = 620,
               direction: str = "RIGHT", name: str = "graph", packed: bool = False) -> None:
    """Draw the graph. Opens what was clicked, and remembers which groups are open.
    packed: lay each group out on its own and pack the groups together, for groups that don't flow into
    each other (database subject areas)."""
    remembered = cat.setting(f"graph_open:{key}", None)
    leaves = sum(1 for e in elements if "source" not in e["data"] and not e["data"]["id"].startswith("grp:"))
    if remembered is None:
        # Small graphs start fully open; bigger ones start with their groups closed.
        remembered = [e["data"]["id"] for e in elements if e["data"]["id"].startswith("grp:")
                      and not e["data"].get("closed")] if leaves <= 40 else []
    event = _viewer(elements=elements, open=remembered, height=height, direction=direction, name=name, key=key,
                    packed=packed,
                    default=None)
    stamp = (event or {}).get("at")
    if not event or stamp == st.session_state.get(f"{key}-last-event"):
        return
    st.session_state[f"{key}-last-event"] = stamp
    if event.get("action") == "groups":
        cat.set_setting(f"graph_open:{key}", event.get("open", []), quiet=True)
    elif event.get("action") == "open" and event.get("key"):
        go(event["key"])
        st.rerun()
    elif event.get("action") == "functions" and event.get("key"):
        go(event["key"])
        st.session_state[f"show-functions-{event['key']}"] = True
        st.rerun()
