"""2.4.1: script versions grouped automatically, and older versions kept off the data flow maps unless asked for."""
from pathlib import Path

import pytest

from pdcat import groups, suggestions
from pdcat.config import PythonSource
from pdcat.scan_python import scan_project
from pdcat.store import Catalogue

WRITER = 'import pandas as pd\npd.DataFrame().to_csv("out/forecast.csv")\n'


@pytest.fixture
def cat(tmp_path: Path):
    c = Catalogue(tmp_path / "c.db")
    root = tmp_path / "proj"
    root.mkdir()
    for name in ("forecast_v2.py", "forecast_v3.py", "forecast_old.py"):
        (root / name).write_text(WRITER)
    (root / "other.py").write_text("x = 1\n")
    (root / "main.py").write_text("import forecast_v3\n")
    scan_project(c, PythonSource(name="proj", path=root))
    yield c
    c.close()


def test_script_versions_are_grouped_without_waiting_for_a_yes(cat):
    from pdcat.reportgroups import sync
    from pdcat.versions import find_versions
    assert sync(cat) == 1
    assert groups.older_versions(cat) == {"py://proj/forecast_v2.py", "py://proj/forecast_old.py"}
    find_versions(cat)
    assert suggestions.open_suggestions(cat, "versions") == []          # nothing left to suggest
    cat.set_setting("features_off", ["script_versions"])
    sync(cat)                                                             # switched off: the groups go
    assert groups.older_versions(cat) == set()


def test_two_live_versions_stay_a_suggestion(cat, tmp_path):
    from pdcat.reportgroups import sync
    (tmp_path / "proj" / "main.py").write_text("import forecast_v3\nimport forecast_v2\n")
    scan_project(cat, PythonSource(name="proj", path=tmp_path / "proj"))
    sync(cat)
    assert groups.older_versions(cat) == set()


def test_data_flow_hides_older_versions_unless_asked_and_labels_them(cat):
    from pdcat.dataflow import flow_graph
    from pdcat.reportgroups import sync
    from pdcat.subway import subway_svg
    sync(cat)
    current, older = "py://proj/forecast_v3.py", "py://proj/forecast_v2.py"
    g = flow_graph(cat)
    assert current in g.nodes and older not in g.nodes
    every = flow_graph(cat, old_versions=True)
    assert every.nodes[older]["label"].endswith("(older version)") and every.nodes[older]["older"]
    assert "older version" not in every.nodes[current]["label"]
    assert "forecast_v2.py (older version)" in subway_svg(every)
    # An older version's own page still shows its flow, labelled.
    mine = flow_graph(cat, focus=older)
    assert older in mine.nodes and mine.nodes[older]["older"]
