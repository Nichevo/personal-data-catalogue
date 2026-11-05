"""Version 2.32: change trackers that copy whole rows no longer stop every tracker from being linked."""
from pathlib import Path

from conftest import SRC
from test_v29_change_tracking import HEADER, PLANT, ROWS, _with_plant

from pdcat import trackers
from pdcat.scan_files import scan_folder
from pdcat.scan_sqlserver import ingest
from pdcat.store import Catalogue


def _scan(tmp_path: Path, catalogue: Catalogue) -> Path:
    with catalogue.scan(SRC.name):
        ingest(catalogue, SRC, "ForecastDB", _with_plant())
    folder = tmp_path / "changes"
    folder.mkdir()
    (folder / "ChangesX_TABLEPlant.csv").write_text(HEADER + ROWS)
    # a copy of whole rows plus change columns (the "wide" layout): it used to crash the link step
    (folder / "TABLEPlant_history.csv").write_text(
        "PlantID,WERKS_NAME,STATUS,Operation,ChangeDate\nP1,Alpha,A,U,2026-10-01\n")
    scan_folder(catalogue, "changes", folder)
    return folder


def _links(cat: Catalogue, key: str) -> set[str]:
    return {r[0] for r in cat.conn.execute("SELECT table_key FROM trackers WHERE tracker_key = ?", (key,))}


def test_wide_tracker_does_not_stop_linking(tmp_path, catalogue):
    _scan(tmp_path, catalogue)
    n = trackers.detect(catalogue)
    assert n >= 2
    assert PLANT in _links(catalogue, "files://changes/ChangesX_TABLEPlant.csv")
    assert PLANT in _links(catalogue, "files://changes/TABLEPlant_history.csv")


def test_save_and_look_again_twice(tmp_path, catalogue):
    _scan(tmp_path, catalogue)
    trackers.detect(catalogue)
    catalogue.set_setting("tracker_prefixes", ["ChangesX_"])
    assert trackers.detect(catalogue) >= 2          # again, with the links already there
    assert PLANT in _links(catalogue, "files://changes/ChangesX_TABLEPlant.csv")


def test_one_failing_tracker_does_not_stop_the_others(tmp_path, catalogue, monkeypatch):
    _scan(tmp_path, catalogue)
    real = trackers._suggest_links

    def flaky(cat, key, *a):
        if "history" in key:
            raise ValueError("odd file")
        return real(cat, key, *a)
    monkeypatch.setattr(trackers, "_suggest_links", flaky)
    trackers.detect(catalogue)
    assert PLANT in _links(catalogue, "files://changes/ChangesX_TABLEPlant.csv")
    bad = next(t for t in trackers.list_trackers(catalogue) if "history" in t["key"])
    assert "odd file" in bad["shape"]["link_error"]
