"""Switches for the version 2.1 extras that run during or after a scan, on the Settings page.
Each is on by default; turning one off skips that step (nothing already found is deleted).
Steps that need an optional package (sqlglot, pywin32) say so and fall back or skip quietly."""
from __future__ import annotations

from .store import Catalogue

FEATURES: dict[str, str] = {
    "called_by": "Find scripts that run other scripts (subprocess, python -m, %run)",
    "more_outputs": "Follow file paths through function arguments, settings modules and config dictionaries",
    "script_versions": "Group versions of a script (forecast_v2.py, forecast_v3.py)",
    "families": "Group files that are the same data at different grains (SOH_D, SOH_W, SOH_M)",
    "folder_roles": "Suggest folder roles (code, raw data, data, outputs, archive)",
    "git_history": "Read git history for scripts (who changed them last, and when)",
    "conda_envs": "Read installed conda environments",
    "access": "Read Access databases (.accdb, .mdb) on Windows",
    "sql_library": "Collect every SQL query into the SQL query library",
    "sql_checks": "Check SQL for likely mistakes, slow patterns and risks",
    "naming_rules": "Audit naming rules and work out the habits names follow",
    "sap_links": "Suggest SAP meanings for tables and columns",
    "metrics_found": "Suggest metrics found in code and reports",
    "backups": "Suggest main and backup table pairs",
    "renames": "Follow column renames in code and SQL",
    "comments": "Draft descriptions from comments and docstrings",
    "learned_joins": "Learn usual joins from your queries",
    "freshness": "Track whether output files keep refreshing",
}


def on(cat: Catalogue, name: str) -> bool:
    return name not in set(cat.setting("features_off", []))


def set_on(cat: Catalogue, name: str, value: bool) -> None:
    off = set(cat.setting("features_off", []))
    off.discard(name) if value else off.add(name)
    cat.set_setting("features_off", sorted(off))


def optional_packages() -> dict[str, bool]:
    """Optional packages and whether they're installed."""
    out = {}
    for name, mod in (("sqlglot", "sqlglot"), ("pywin32", "win32com.client"), ("pbixray", "pbixray"),
                      ("olefile", "olefile"), ("python-docx", "docx"), ("pypdf", "pypdf")):
        try:
            __import__(mod)
            out[name] = True
        except Exception:
            out[name] = False
    return out
