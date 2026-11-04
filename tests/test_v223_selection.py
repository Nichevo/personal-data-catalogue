"""Table selections that outlive the rows they pointed at (the Profiling page's list index error)."""
from types import SimpleNamespace

from pdcat.ui.common import selected_rows, selection_key


def _event(rows):
    return SimpleNamespace(selection=SimpleNamespace(rows=rows))


def test_stale_rows_are_dropped():
    assert selected_rows(_event([0, 2, 5, 9]), 3) == [0, 2]
    assert selected_rows(_event([4]), 0) == []


def test_missing_event_or_selection():
    assert selected_rows(None, 5) == []
    assert selected_rows(SimpleNamespace(), 5) == []
    assert selected_rows({"selection": {"rows": [1]}}, 5) == []  # not a widget result: nothing picked


def test_key_changes_with_the_rows_shown():
    a = selection_key("prof-table", ["t1", "t2"])
    assert a == selection_key("prof-table", ["t1", "t2"])
    assert a != selection_key("prof-table", ["t1"])
    assert a.startswith("prof-table-")
