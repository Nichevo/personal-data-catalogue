"""Opening a v1 catalogue with v2 upgrades it in place, after keeping an untouched copy."""
import sqlite3
from pathlib import Path

import pytest

from pdcat.store import SCHEMA_VERSION, Catalogue, UpgradeError

FIXTURE = Path(__file__).parent / "fixtures" / "v1_catalogue.sql"
SALES = "sql://warehouse/salesdw/dbo.sales"


@pytest.fixture
def v1_db(tmp_path: Path) -> Path:
    path = tmp_path / "catalogue.db"
    conn = sqlite3.connect(path)
    conn.executescript(FIXTURE.read_text())
    conn.close()
    return path


def _rows(path: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_upgrade_keeps_every_note_and_makes_a_backup(v1_db):
    before = _rows(v1_db, "SELECT asset_key, body, tags, note_type, status, created_at FROM notes ORDER BY id")
    cat = Catalogue(v1_db)
    try:
        assert cat.upgrade_backup is not None and cat.upgrade_backup.exists()
        assert _rows(cat.upgrade_backup, "SELECT asset_key, body, tags, note_type, status, created_at "
                                         "FROM notes ORDER BY id") == before
        after = [tuple(r) for r in cat.conn.execute(
            "SELECT asset_key, body, tags, note_type, status, created_at FROM notes ORDER BY id")]
        assert after == before
        uids = [r[0] for r in cat.conn.execute("SELECT uid FROM notes")]
        assert len(set(uids)) == 3 and all(len(u) == 32 for u in uids)
        assert all(r[0] for r in cat.conn.execute("SELECT author FROM notes"))
        assert cat.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        # Scanned data, profiles and search still work.
        assert [c["name"] for c in cat.columns(SALES)] == ["PlantID", "Units"]
        assert cat.profile(SALES)[0]["max_value"] == "99"
        cat.rebuild_search()
        assert cat.search("nightly")[0]["key"] == SALES
    finally:
        cat.close()


def test_second_open_does_not_back_up_again(v1_db):
    Catalogue(v1_db).close()
    cat = Catalogue(v1_db)
    assert cat.upgrade_backup is None
    cat.close()
    assert len(list(v1_db.parent.glob(f"*.before-v{SCHEMA_VERSION}-*"))) == 1


def test_new_catalogue_needs_no_backup(tmp_path):
    cat = Catalogue(tmp_path / "new.db")
    assert cat.upgrade_backup is None
    cat.close()


def test_failed_upgrade_points_to_the_backup(v1_db, monkeypatch):
    def broken(self):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(Catalogue, "_migrate", broken)
    with pytest.raises(UpgradeError) as err:
        Catalogue(v1_db)
    assert f"before-v{SCHEMA_VERSION}" in str(err.value)
    # The original notes are untouched.
    assert len(_rows(v1_db, "SELECT * FROM notes")) == 3


def test_first_run_config(tmp_path):
    from pdcat import firstrun
    from pdcat.config import load_config
    path = firstrun.ensure_config(tmp_path / "config.toml")
    assert load_config(path).sqlserver == []
    firstrun.add_sqlserver(path, "warehouse", r"DEMO-SQL01\SALES", ["ForecastDB", " Other "], "ODBC Driver 18 for SQL Server")
    proj = tmp_path / "proj"
    proj.mkdir()
    firstrun.add_python(path, "forecasting", str(proj), "warehouse/ForecastDB")
    cfg = load_config(path)
    assert cfg.sqlserver[0].server == r"DEMO-SQL01\SALES" and cfg.sqlserver[0].databases == ["ForecastDB", "Other"]
    assert cfg.python[0].default_database == "warehouse/ForecastDB"
    import pytest
    with pytest.raises(ValueError):  # duplicate names are refused and the file is left as it was
        firstrun.add_python(path, "warehouse", str(proj))
    assert len(load_config(path).python) == 1
    assert "drivers" in firstrun.odbc_drivers()
