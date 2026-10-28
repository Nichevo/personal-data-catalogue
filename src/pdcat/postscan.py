"""Work done after scans, from what is already in the catalogue (no queries to your servers):
relationship suggestions, company-wide key suggestions, dated file series, duplicates and the review list."""
from __future__ import annotations

from collections.abc import Callable

from . import (
    bprojects,
    colrenames,
    commentdrafts,
    condaenvs,
    families,
    folderroles,
    freshness,
    gitinfo,
    labels,
    learned,
    metricfind,
    naming,
    rulechecks,
    sapterms,
    schedules,
    sqlchecks,
    sqllibrary,
    tablepairs,
    versions,
)
from .duplicates import find_duplicates
from .keys import suggest_keys
from .mlfeatures import suggest_features
from .store import Catalogue


def after_scan(cat: Catalogue, progress=lambda *_: None) -> dict[str, int]:
    from .relationships import find_suggestions
    from .review import refresh_tasks
    out = {"relationships": find_suggestions(cat)}
    for name, step in _extra_steps():
        try:
            out[name] = step(cat)
        except Exception as e:  # one failing step must not hide the scan's results
            progress(f"  {name} failed: {type(e).__name__}: {e}")
    out.update({f"review {k}": v for k, v in refresh_tasks(cat).items()})
    cat.set_setting("tasks_revision", cat.revision(), quiet=True)
    return out


def _extra_steps() -> list[tuple[str, Callable[[Catalogue], int]]]:
    """(label, step) pairs, in the order they run. Each finder in SWITCHABLE_STEPS can be switched off in Settings."""
    from .features import on
    steps: list[tuple[str, Callable[[Catalogue], int]]] = [
        ("key suggestions", suggest_keys), ("feature suggestions", suggest_features), ("duplicates", find_duplicates)]
    for feature, step, label in SWITCHABLE_STEPS:
        steps.append((label, lambda cat, step=step, feature=feature: step(cat) if on(cat, feature) else 0))
    return steps


# (feature switch, function(cat) -> count, label); each one stores suggestions or groups for the review pages
SWITCHABLE_STEPS: list[tuple[str, Callable[[Catalogue], int], str]] = [
    ("folder_roles", folderroles.suggest_all, "folder role suggestions"),
    ("schedules", schedules.match_all, "schedule matches"),
    ("git_history", gitinfo.scan_git, "git history"),
    ("conda_envs", condaenvs.scan_envs, "conda environments"),
    ("script_versions", versions.find_versions, "script versions"),
    ("families", families.find_families, "file families"),
    ("families", families.group_script_outputs, "files a script writes"),
    ("sql_library", sqllibrary.build_library, "SQL query library"),
    ("sql_checks", sqlchecks.check_all, "SQL checks"),
    ("naming_rules", naming.derive_all, "naming habits"),
    ("sap_links", sapterms.suggest_sap, "SAP meanings"),
    ("metrics_found", metricfind.find_metrics, "metrics found in code"),
    ("backups", tablepairs.find_backups, "main and backup tables"),
    ("renames", colrenames.find_renames, "column renames"),
    ("comments", commentdrafts.find_descriptions, "descriptions from comments"),
    ("rule_checks", rulechecks.find_rules, "rules in notes"),
    ("report_labels", labels.find_labels, "report labels"),
    ("learned_joins", learned.learn_joins, "usual joins"),
    ("freshness", freshness.track, "freshness"),
    ("bproject_folders", bprojects.sync_folders, "new files in business project folders"),
]
