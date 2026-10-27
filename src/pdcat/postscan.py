"""Work done after scans, from what is already in the catalogue (no queries to your servers):
relationship suggestions, company-wide key suggestions, dated file series, duplicates and the review list."""
from __future__ import annotations

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


def _extra_steps():
    steps = []
    try:
        from .keys import suggest_keys
        steps.append(("key suggestions", suggest_keys))
    except ImportError:
        pass
    try:
        from .mlfeatures import suggest_features
        steps.append(("feature suggestions", suggest_features))
    except ImportError:
        pass
    try:
        from .duplicates import find_duplicates
        steps.append(("duplicates", find_duplicates))
    except ImportError:
        pass
    return steps
