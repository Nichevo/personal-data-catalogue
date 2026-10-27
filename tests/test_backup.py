import json

import pytest

from pdcat.backup import backup_now, due, list_backups, maybe_backup, restore
from pdcat.store import Catalogue

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"


def test_backups_are_off_until_a_folder_is_chosen(catalogue):
    assert not due(catalogue)
    assert maybe_backup(catalogue) is None
    with pytest.raises(ValueError):
        backup_now(catalogue)


def test_backup_now_copies_catalogue_and_notes(catalogue, tmp_path):
    catalogue.add_note(SALES, "Loaded nightly.")
    folder = tmp_path / "backups"
    catalogue.set_setting("backup_folder", str(folder))
    path = backup_now(catalogue)
    assert path.exists()
    notes = json.loads(path.with_suffix(".notes.json").read_text(encoding="utf-8"))
    assert [n["body"] for n in notes["notes"]] == ["Loaded nightly."]
    listed = list_backups(folder)
    assert listed[0]["notes"] == 1 and listed[0]["items"] > 0
    assert not due(catalogue)  # daily by default, and one was just made


def test_schedule_and_keep(catalogue, tmp_path):
    folder = tmp_path / "b"
    catalogue.set_setting("backup_folder", str(folder))
    catalogue.set_setting("backup_every", "app open")
    catalogue.set_setting("backup_keep", 2)
    import time
    for _ in range(3):
        assert maybe_backup(catalogue) is not None
        time.sleep(1.05)  # file names are per second
    assert len(list_backups(folder)) == 2


def test_restore_brings_back_old_notes_and_saves_current(catalogue, tmp_path):
    catalogue.add_note(SALES, "First.")
    path = backup_now(catalogue, tmp_path / "b")
    catalogue.add_note(SALES, "Second.")
    safety = restore(catalogue, path)
    assert [n["body"] for n in catalogue.notes(SALES)] == ["First."]
    old = Catalogue(safety)
    assert {n["body"] for n in old.notes(SALES)} == {"First.", "Second."}
    old.close()
