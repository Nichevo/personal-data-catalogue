"""Version 2.8: folders full of history files (archived copies, dated subfolders, thousands of workbooks)."""
import os

import pandas as pd
from test_office import write_xlsx

from pdcat import series as S
from pdcat.folderroles import role_from_name, save
from pdcat.historyfolders import History
from pdcat.scan_files import scan_folder
from pdcat.store import Catalogue


def _weekly_tree(root, days=(2, 9, 14), months=(3, 4)):
    """Reports/2026/04 2026/20260414/archived/converted_REPORT20260414.xlsx and the like."""
    paths = []
    for mo in months:
        for d in days:
            day = f"2026{mo:02d}{d:02d}"
            p = root / "FreightFiles" / "2026" / f"{mo:02d} 2026" / day / "archived" / f"converted_REPORT{day}.xlsx"
            p.parent.mkdir(parents=True, exist_ok=True)
            write_xlsx(p)
            stamp = 1_700_000_000 + mo * 100_000 + d * 1000
            os.utime(p, (stamp, stamp))
            paths.append(p)
    return paths


def test_month_folders_with_a_space_and_dated_subfolders_make_one_series():
    a = S.parse("F/2026/05 2026/20260414/archived/converted_REPORT20260414.xlsx")
    b = S.parse("F/2026/04 2026/20260402/archived/converted_REPORT20260402.xlsx")
    assert a.template == b.template == "F/{yyyy}/{mm}{yyyy}/{yyyy}{mm}{dd}/archived/converted_REPORT{yyyy}{mm}{dd}.xlsx"
    assert a.date.isoformat() == "2026-04-14" and a.mismatch is None  # the deepest dated folder wins


def test_history_folder_names_and_roles(tmp_path):
    cat = Catalogue(tmp_path / "c.db")
    h = History(cat, tmp_path, "src")
    assert h.why("a/archived/x.xlsx") == "named archived/"
    assert h.why("a/History/x.csv") == "named History/"
    assert h.why("a/current/x.csv") is None
    assert role_from_name("previous") == "archive"
    save(cat, tmp_path, {"a/archived": "data"})  # you said it's data: read it in full
    save(cat, tmp_path, {"b": "archive"})
    h = History(cat, tmp_path, "src")
    assert h.why("a/archived/x.xlsx") is None
    assert h.why("b/x.csv") == "you gave it the role archive"
    cat.set_setting("read_all_sources", ["src"])
    assert History(cat, tmp_path, "src").why("b/x.csv") is None


def test_dated_workbooks_in_archived_folders_open_only_the_newest(tmp_path):
    root = tmp_path / "weekly"
    _weekly_tree(root)
    write_xlsx(root / "summary.xlsx")
    cat = Catalogue(tmp_path / "c.db")
    cat.set_setting("dated_folders_days", 0)  # walk every dated folder (2.8.1 would skip March)
    heard = []
    scan_folder(cat, "weekly", root, report=lambda t, done=None, total=None: heard.append(t))
    workbooks = [r[0] for r in cat.conn.execute("SELECT key FROM assets WHERE kind = 'workbook' AND status = 'active'")]
    assert sorted(workbooks) == ["files://weekly/FreightFiles/2026/04 2026/20260414/archived/converted_REPORT20260414.xlsx",
                                 "files://weekly/summary.xlsx"]
    [series] = [r for r in cat.conn.execute("SELECT key, attrs FROM assets WHERE kind = 'file_series'")]
    s = cat.get_asset(series["key"])["attrs"]
    assert s["file_count"] == 6 and s["files_not_read"] == 5 and s["office"] == "workbooks"
    assert s["opened"] == [workbooks[0]] and s["first"] == "2026-03-02" and s["last"] == "2026-04-14"
    assert cat.get_asset(workbooks[0])["attrs"]["in_series"] == series["key"]
    assert cat.columns(series["key"])  # the newest workbook's columns
    assert any("History folders" in t for t in heard)
    log = cat.scan_log(1)[0]["summary"]
    assert log["listed"] == 5 and log["history"][0]["files"] == 6 and log["history"][0]["folders"] == 6


def test_big_folders_sample_workbooks_by_date_too(tmp_path):
    root = tmp_path / "drops"
    for d in range(1, 9):
        p = root / "2026" / f"202603{d:02d}" / f"orders_202603{d:02d}.xlsx"  # no archive folder this time
        p.parent.mkdir(parents=True, exist_ok=True)
        write_xlsx(p)
    cat = Catalogue(tmp_path / "c.db")
    cat.set_setting("sample_over_files", 5)
    cat.set_setting("sample_newest", 2)
    scan_folder(cat, "drops", root)
    assert cat.conn.execute("SELECT COUNT(*) FROM assets WHERE kind = 'workbook'").fetchone()[0] == 2
    cat.set_setting("read_all_sources", ["drops"])
    scan_folder(cat, "drops", root)
    assert cat.conn.execute("SELECT COUNT(*) FROM assets WHERE kind = 'workbook' AND status = 'active'"
                            ).fetchone()[0] == 8


def test_workbooks_opened_per_scan_are_capped_and_the_next_scan_carries_on(tmp_path):
    root = tmp_path / "books"
    root.mkdir()
    for i in range(5):
        p = root / f"book {chr(65 + i)}.xlsx"
        write_xlsx(p)
        os.utime(p, (1_700_000_000 + i, 1_700_000_000 + i))
    cat = Catalogue(tmp_path / "c.db")
    cat.set_setting("max_office_opens", 2)
    heard = []
    scan_folder(cat, "books", root, report=lambda t, done=None, total=None: heard.append(t))
    names = {r[0] for r in cat.conn.execute("SELECT name FROM assets WHERE kind = 'workbook' AND status = 'active'")}
    assert names == {"book E.xlsx", "book D.xlsx"}  # newest first
    assert cat.scan_log(1)[0]["summary"]["deferred"] == 3 and any("left for the next scan" in t for t in heard)
    cat.set_setting("recent_scan_minutes", 0)
    scan_folder(cat, "books", root)
    scan_folder(cat, "books", root)
    names = {r[0] for r in cat.conn.execute("SELECT name FROM assets WHERE kind = 'workbook' AND status = 'active'")}
    assert len(names) == 5


def test_a_workbook_read_before_stays_when_it_moves_into_history_listing(tmp_path):
    root = tmp_path / "weekly"
    paths = _weekly_tree(root, days=(2,), months=(3,))
    cat = Catalogue(tmp_path / "c.db")
    scan_folder(cat, "weekly", root)  # one file: nothing to group, it is opened
    first = f"files://weekly/{paths[0].relative_to(root).as_posix()}"
    assert cat.get_asset(first)["status"] == "active"
    _weekly_tree(root, days=(2, 9), months=(3,))  # a newer one arrives: the older one is now only listed
    scan_folder(cat, "weekly", root)
    assert cat.get_asset(first)["status"] == "active"  # kept from the earlier scan, not marked missing


def test_stand_alone_data_in_history_is_still_read_in_a_normal_folder(tmp_path):
    root = tmp_path / "proj"
    (root / "old").mkdir(parents=True)
    pd.DataFrame({"a": [1, 2]}).to_csv(root / "old" / "stores.csv", index=False)
    cat = Catalogue(tmp_path / "c.db")
    scan_folder(cat, "proj", root)
    assert not cat.get_asset("files://proj/old/stores.csv")["attrs"].get("not_read")
