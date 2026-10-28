"""The Streamlit app, one module per group of pages. `pdcat ui` starts it through app.py."""
from __future__ import annotations

from collections.abc import Callable


def pages_for_key(key: str) -> Callable | None:
    """Item pages for things that aren't scanned assets: packages, knowledge, questions, metrics…"""
    if key.startswith("pkg://"):
        from .code import package_page
        return package_page
    if key.startswith(("kb://", "q://", "metric://")):
        from .knowledge_pages import knowledge_item_page
        return knowledge_item_page
    if key.startswith("feature://"):
        from .ml_pages import feature_page
        return feature_page
    if key.startswith("key://"):
        from .keys_pages import key_page
        return key_page
    if key.startswith("env://"):
        from .sources_extra import env_page
        return env_page
    if key.startswith("group://"):
        from .groups_page import group_page
        return group_page
    if key.startswith("query://"):
        from .sql_pages import query_page
        return query_page
    if key.startswith("doc://"):
        from .documents_page import document_page
        return document_page
    if key.startswith("bp://"):
        from .bproject_pages import bproject_page
        return bproject_page
    return None
