"""Version 2.8.1: trees of folders named by date are only walked for the newest weeks; log folders are counted."""
from datetime import date, timedelta

import pandas as pd

from pdcat.datedfolders import DatedWalk, span
from pdcat.scan_files import scan_folder
from pdcat.scanprogress import find_files
from pdcat.store import Catalogue


def _csv(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"a": [1, 2]}).to_csv(path, index=False)


def _daily(root, start=date(2025, 11, 1), days=120):
    """root/2025/11 2025/20251101/archived/orders_20251101.csv …"""
    for i in range(days):
        d = start + timedelta(days=i)
        _csv(root / str(d.year) / f"{d.month:02d} {d.year}" / d.strftime("%Y%m%d") / "archived"
             / f"orders_{d:%Y%m%d}.csv")
    return start + timedelta(days=days - 1)


def test_folder_names_as_dates():
    assert span("2026") == (date(2026, 1, 1), date(2026, 12, 31))
    assert span("05 2026") == (date(2026, 5, 1), date(2026, 5, 31))
    assert span("2026-05") == (date(2026, 5, 1), date(2026, 5, 31))
    assert span("May 2026") == (date(2026, 5, 1), date(2026, 5, 31))
    assert span("20260514") == (date(2026, 5, 14), date(2026, 5, 14))
    assert span("05", span("2026")) == (date(2026, 5, 1), date(2026, 5, 31))
    assert span("14", span("2026-05")) == (date(2026, 5, 14), date(2026, 5, 14))
    assert span("Sales 2026 final") is None and span("05") is None


def test_only_the_newest_weeks_of_a_dated_tree_are_walked(tmp_path):
    newest = _daily(tmp_path / "Files")
    walk = DatedWalk(days=14)
    found, _ = find_files(tmp_path, (".csv",), dated=walk)
    days = sorted(p.parent.parent.name for p in found[".csv"])
    assert len(days) == 14 and days[-1] == f"{newest:%Y%m%d}"
    [tree] = walk.summaries()
    assert tree["folder"] == "Files" and tree["newest"] == newest.isoformat()
    assert tree["skipped_first"] == "2025-01-01" and tree["skipped"] >= 4  # the whole 2025 folder and older days
    everything, _ = find_files(tmp_path, (".csv",))
    assert len(everything[".csv"]) == 120


def test_year_month_day_number_folders(tmp_path):
    for d in range(1, 29):
        _csv(tmp_path / "2026" / "02" / f"{d:02d}" / "x.csv")
    for m in ("01", "03"):
        _csv(tmp_path / "2026" / m / "01" / "x.csv")
    _csv(tmp_path / "2025" / "12" / "31" / "x.csv")
    _csv(tmp_path / "notes" / "readme.csv")
    found, _ = find_files(tmp_path, (".csv",), dated=DatedWalk(days=7))
    rels = sorted(p.relative_to(tmp_path).as_posix() for p in found[".csv"])
    assert rels == ["2026/02/23/x.csv", "2026/02/24/x.csv", "2026/02/25/x.csv", "2026/02/26/x.csv",
                    "2026/02/27/x.csv", "2026/02/28/x.csv", "2026/03/01/x.csv", "notes/readme.csv"]


def test_scan_keeps_what_older_dated_folders_held_and_counts_log_folders(tmp_path):
    root = tmp_path / "weekly"
    _daily(root, days=40)
    for i in range(30):
        log = root / "logs" / f"run_{i}.csv"  # even data-like files in logs/ aren't read
        _csv(log)
    cat = Catalogue(tmp_path / "c.db")
    cat.set_setting("dated_folders_days", 0)  # first scan: everything
    cat.set_setting("skip_log_folders", False)
    scan_folder(cat, "weekly", root)
    old = "files://weekly/2025/11 2025/20251101/archived/orders_20251101.csv"
    assert cat.get_asset(old)["status"] == "active"
    cat.set_setting("dated_folders_days", 14)
    cat.set_setting("skip_log_folders", True)
    cat.set_setting("recent_scan_minutes", 0)
    heard = []
    scan_folder(cat, "weekly", root, report=lambda t, done=None, total=None: heard.append(t))
    assert cat.get_asset(old)["status"] == "active"  # not walked this time, but kept
    assert cat.get_asset("files://weekly/logs/run_0.csv")["status"] == "active"
    log = cat.get_asset("files://weekly/logs")
    assert log["attrs"]["log_folder"] and log["attrs"]["files"] == 30 and "not read" in log["description"]
    summary = cat.scan_log(1)[0]["summary"]
    assert summary["dated_trees"][0]["skipped"] > 0 and summary["log_folders"][0]["files"] == 30
    assert summary["files_found"] < 40
    assert any("named by date" in t for t in heard) and any("log folder" in t for t in heard)


def test_read_every_file_walks_everything(tmp_path):
    root = tmp_path / "w"
    _daily(root, days=40)
    (root / "logs").mkdir()
    _csv(root / "logs" / "a.csv")
    cat = Catalogue(tmp_path / "c.db")
    cat.set_setting("read_all_sources", ["w"])
    scan_folder(cat, "w", root)
    assert cat.scan_log(1)[0]["summary"]["files_found"] == 41


def test_month_folders_are_walked_in_full(tmp_path):
    for y in (2024, 2025, 2026):
        for m in range(1, 13):
            _csv(tmp_path / str(y) / f"{m:02d}" / "sales.csv")
    walk = DatedWalk(days=14)
    found, _ = find_files(tmp_path, (".csv",), dated=walk)
    assert len(found[".csv"]) == 36 and walk.summaries() == []
