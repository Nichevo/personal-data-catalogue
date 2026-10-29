"""Version 2.14: big folders sampled by date, scans that save as they go, recently scanned sources, and the
smaller fixes that came with them (file names with times, measures across reports, docs links)."""
import copy
import json
import os
import zipfile

import pandas as pd
import pytest
from test_office import TMSL, layout, write_pbix

from pdcat import series as S
from pdcat.scan_files import scan_folder, scanned_recently
from pdcat.store import Catalogue


def _csv(path, rows=3):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"a": list(range(rows)), "b": ["x"] * rows}).to_csv(path, index=False)


# ---- dates with a time after them -----------------------------------------------------------------

def test_a_time_after_the_date_is_part_of_the_date_placeholder():
    dp = S.parse("TOPIC_TOPIC_202304201901500.csv")
    assert (dp.year, dp.month, dp.day) == (2023, 4, 20) and dp.template == "TOPIC_TOPIC_{yyyy}{mm}{dd}{time}.csv"
    assert S.parse("sales_2023-04-20_190150.csv").template == "sales_{yyyy}{mm}{dd}{time}.csv"
    assert S.parse("sales_2023-04-20_2.csv").template == "sales_{yyyy}{mm}{dd}_2.csv"  # a short number isn't a time
    assert S.parse("20230420123.csv") is None  # 11 digits: neither a date nor a date with a time
    groups, alone = S.group([f"TOPIC_TOPIC_202304{d:02d}1901500.csv" for d in (18, 19, 20)]
                            + ["TOPIC_TOPIC_202304201905000.csv"])  # a second file the same day
    [g] = groups
    assert alone == [] and len(g.members) == 4 and S.describe(g)["frequency"] == "daily"


# ---- big folders are sampled by date --------------------------------------------------------------

SERIES = "files://drops/~series/topic_topic_{yyyy}{mm}{dd}{time}.csv"  # series keys are lower case


def _daily_folder(root, days=8):
    for d in range(1, days + 1):
        _csv(root / f"TOPIC_TOPIC_202304{d:02d}1901500.csv", rows=d)
    _csv(root / "stores.csv")
    _csv(root / "regions.csv")


def test_big_folders_read_only_the_newest_files_of_each_series(tmp_path):
    root = tmp_path / "drops"
    _daily_folder(root)
    cat = Catalogue(tmp_path / "c.db")
    cat.set_setting("sample_over_files", 5)  # pretend 5 files is a lot
    cat.set_setting("sample_newest", 2)
    heard = []
    counts = scan_folder(cat, "drops", root, report=lambda t, done=None, total=None: heard.append(t))
    assert counts["files"] == 10 and counts["series"] == 1
    assert any("sampled by date" in t for t in heard)
    assert any(t.startswith("Listing data files") and "not opened" in t for t in heard)
    s = cat.get_asset(SERIES)["attrs"]
    assert s["file_count"] == 8 and s["files_not_read"] == 6
    assert [f["rows"] for f in s["files"]] == [None] * 6 + [7, 8]  # the two newest were opened
    assert all(f.get("not_read") for f in s["files"][:6]) and not any(f.get("not_read") for f in s["files"][6:])
    assert cat.get_asset("files://drops/TOPIC_TOPIC_202304011901500.csv")["attrs"]["not_read"] is True
    assert "row_count" not in cat.get_asset("files://drops/TOPIC_TOPIC_202304011901500.csv")["attrs"]
    assert cat.get_asset("files://drops/stores.csv")["attrs"]["row_count"] == 3  # stand-alone files are read
    [entry] = cat.scan_log()
    assert entry["summary"]["sampled"] is True and entry["summary"]["listed"] == 6
    assert entry["summary"]["files_read"] == 4 and entry["summary"]["unchanged_or_listed"] == 6
    # "Read every file" on the folder: the next scan opens the listed files too.
    cat.set_setting("read_all_sources", ["drops"])
    scan_folder(cat, "drops", root)
    s = cat.get_asset(SERIES)["attrs"]
    assert "files_not_read" not in s and [f["rows"] for f in s["files"]] == list(range(1, 9))
    assert cat.scan_log()[0]["summary"].get("sampled") is None
    assert cat.scan_log()[0]["summary"]["files_read"] == 6  # only the files listed last time were opened


def test_small_folders_and_a_zero_threshold_read_everything(tmp_path):
    root = tmp_path / "drops"
    _daily_folder(root, days=3)
    cat = Catalogue(tmp_path / "c.db")
    scan_folder(cat, "drops", root)
    s = cat.get_asset(SERIES)["attrs"]
    assert [f["rows"] for f in s["files"]] == [1, 2, 3]
    cat.set_setting("sample_over_files", 0)
    cat.set_setting("sample_newest", 1)
    scan_folder(cat, "drops", root)
    assert "files_not_read" not in cat.get_asset(SERIES)["attrs"]


def test_too_many_stand_alone_files_are_sampled_by_modified_date(tmp_path):
    root = tmp_path / "many"
    for i in range(6):
        _csv(root / f"file{i}.csv")
        os.utime(root / f"file{i}.csv", (1_700_000_000 + i, 1_700_000_000 + i))  # file5 is the newest
    cat = Catalogue(tmp_path / "c.db")
    cat.set_setting("sample_over_files", 4)
    scan_folder(cat, "many", root)
    read = sorted(r[0] for r in cat.conn.execute("SELECT key FROM assets WHERE kind = 'data_file' AND attrs NOT LIKE "
                                                 "'%not_read%'"))
    assert read == [f"files://many/file{i}.csv" for i in (2, 3, 4, 5)]
    assert cat.get_asset("files://many/file0.csv")["attrs"]["not_read"] is True


# ---- a scan saves as it goes ---------------------------------------------------------------------

class Stopped(BaseException):
    """What Streamlit raises inside the script when Stop is pressed: not an Exception."""


def test_a_stopped_scan_keeps_what_it_had_read_and_the_next_scan_carries_on(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    write_pbix(root / "Sales.pbix")
    _csv(root / "sales.csv")
    _csv(root / "stores.csv")
    cat = Catalogue(tmp_path / "c.db")

    def stop_at_data_files(text, done=None, total=None):
        if text.startswith("Reading data file"):
            raise Stopped()
    with pytest.raises(Stopped):
        scan_folder(cat, "data", root, report=stop_at_data_files)
    assert cat.get_asset("files://data/Sales.pbix")["status"] == "active"  # read before the stop: saved
    assert cat.get_asset("files://data/sales.csv") is None
    [entry] = cat.scan_log()
    assert entry["summary"]["stopped"] is True and "1 report(s) saved" in entry["summary"]["warning"]
    assert scanned_recently(cat, "data") is None  # a stopped scan doesn't count as a recent one
    heard = []
    counts = scan_folder(cat, "data", root, report=lambda t, done=None, total=None: heard.append(t))
    assert counts == {"files": 2, "series": 0, "reports": 1, "workbooks": 0, "access": 0, "errors": 0}
    assert not any("Reading Power BI" in t for t in heard)  # unchanged, so not opened again
    assert cat.get_asset("files://data/sales.csv")["attrs"]["row_count"] == 3
    assert cat.scan_log()[0]["summary"]["stopped"] is None if "stopped" in cat.scan_log()[0]["summary"] else True


def test_files_that_disappear_are_marked_missing_and_links_dropped(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    write_pbix(root / "Sales.pbix")
    _csv(root / "old.csv")
    cat = Catalogue(tmp_path / "c.db")
    scan_folder(cat, "data", root)
    assert cat.conn.execute("SELECT COUNT(*) FROM links WHERE src_key = 'files://data/Sales.pbix'").fetchone()[0] > 0
    (root / "old.csv").unlink()
    (root / "Sales.pbix").unlink()
    scan_folder(cat, "data", root)
    assert cat.get_asset("files://data/old.csv")["status"] == "missing"
    assert cat.get_asset("files://data/Sales.pbix")["status"] == "missing"
    assert cat.conn.execute("SELECT COUNT(*) FROM links WHERE src_key = 'files://data/Sales.pbix'").fetchone()[0] == 0
    assert cat.get_asset("files://data")["status"] == "active"


def test_other_windows_can_write_while_files_are_read(tmp_path):
    import sqlite3
    root = tmp_path / "data"
    for i in range(3):
        _csv(root / f"f{i}.csv")
    cat = Catalogue(tmp_path / "c.db")
    writes = []

    def report(text, done=None, total=None):
        if text.startswith("Reading"):
            other = sqlite3.connect(tmp_path / "c.db", timeout=0.1)
            try:
                with other:
                    other.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('probe', '1')")
                writes.append(text)
            finally:
                other.close()
    scan_folder(cat, "data", root, report=report)
    assert len(writes) == 3


# ---- recently scanned ----------------------------------------------------------------------------

def test_recently_scanned_sources_are_known(tmp_path):
    root = tmp_path / "data"
    _csv(root / "a.csv")
    cat = Catalogue(tmp_path / "c.db")
    assert scanned_recently(cat, "data") is None
    scan_folder(cat, "data", root)
    assert scanned_recently(cat, "data") == "just now"
    assert scanned_recently(cat, "data", minutes=0) is None
    cat.set_setting("recent_scan_minutes", 0)
    assert scanned_recently(cat, "data") is None


def test_cli_skips_a_source_scanned_a_moment_ago_unless_forced(tmp_path, capsys):
    from pdcat.cli import main
    root = tmp_path / "data"
    _csv(root / "a.csv")
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'db_path = "{(tmp_path / "c.db").as_posix()}"\n\n[[folder]]\nname = "data"\n'
                   f'path = "{root.as_posix()}"\n')
    assert main(["--config", str(cfg), "scan"]) == 0
    assert main(["--config", str(cfg), "scan"]) == 0
    out = capsys.readouterr().out
    assert "Skipping data: scanned just now" in out
    assert main(["--config", str(cfg), "scan", "--force"]) == 0
    assert "Skipping" not in capsys.readouterr().out


# ---- faster profiling ---------------------------------------------------------------------------

def test_delimiters_are_found_without_the_sniffer_and_profiles_are_unchanged(tmp_path):
    from pdcat.profiling import _sniff_delimiter, profile_dataframe, profile_file
    assert _sniff_delimiter("a;b;c\n1;2;3\n") == ";"
    assert _sniff_delimiter("a\tb\n1\t2\n") == "\t"
    assert _sniff_delimiter("a|b\n1|2\n3|4\n") == "|"
    assert _sniff_delimiter('name,city\n"x, y",z\n1,2\n') == ","  # quoted commas: left to csv.Sniffer
    assert _sniff_delimiter("single\n1\n2\n") == ","
    df = pd.DataFrame({"n": [1, 1, 2, None], "t": ["b", "a", "a", None], "m": [1, "x", 2.5, None]})
    [n, t, m] = profile_dataframe(df)
    assert n["top"] == [["1.0", 2], ["2.0", 1]] and (n["min"], n["max"]) == (1.0, 2.0)
    assert t["top"] == [["a", 2], ["b", 1]] and (t["min"], t["max"]) == ("a", "b")
    assert m["top"][0][1] == 1 and m["min"] == "1" and m["max"] == "x"  # mixed values compared as text
    p = tmp_path / "x.csv"
    pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}).to_csv(p, index=False, sep=";")
    attrs, profiles, _ = profile_file(p)
    assert attrs["row_count"] == 2 and [c["name"] for c in profiles] == ["a", "b"]


# ---- measures across reports ---------------------------------------------------------------------

def _pbit(path, measures):
    model = copy.deepcopy(TMSL)
    model["model"]["tables"][0]["measures"] = [{"name": n, "expression": e} for n, e in measures]
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Report/Layout", json.dumps(layout()).encode("utf-16-le"))
        z.writestr("DataModelSchema", json.dumps(model).encode("utf-16-le"))


def test_measures_that_recur_across_reports_are_compared(tmp_path):
    from pdcat.measures import across_reports
    folder = tmp_path / "reports"
    _pbit(folder / "Sales.pbit", [("Total Units", "SUM(Sales[Units])"), ("Avg Units", "AVERAGE(Sales[Units])")])
    _pbit(folder / "Stock.pbit", [("Total Units", "SUMX(Sales, Sales[Units])"),
                                  ("Average Units", "average( Sales[Units] ) // per row")])
    _pbit(folder / "Other.pbit", [("Count", "COUNTROWS(Sales)")])
    cat = Catalogue(tmp_path / "c.db")
    scan_folder(cat, "reports", folder)
    found = across_reports(cat)
    [shared] = found["same_dax"]
    assert shared["names"] == ["Average Units", "Avg Units"] and shared["different_names"] and shared["reports"] == 2
    [clash] = found["same_name"]
    assert clash["name"] == "Total Units" and clash["versions"] == 2
    assert {v["expression"] for v in clash["variants"]} == {"SUM(Sales[Units])", "SUMX(Sales, Sales[Units])"}


# ---- odds and ends -------------------------------------------------------------------------------

def test_package_documentation_links():
    from pdcat.libclass import docs_url
    assert docs_url("sklearn") == docs_url("scikit-learn") == "https://scikit-learn.org/stable/user_guide.html"
    assert docs_url("Pandas").startswith("https://pandas.pydata.org/")
    assert docs_url("some-house-package") == "https://pypi.org/project/some-house-package/"


def test_diagram_fields_can_be_shown_for_chosen_tables(catalogue):
    from pdcat import erd
    db = next(d["key"] for d in erd.databases(catalogue) if d["tables"])
    sales = "sql://warehouse/forecastdb/dbo.salesdaily"
    column = catalogue.columns(sales)[0]["name"]
    none = {e["data"]["id"]: e["data"]["label"] for e in erd.erd_elements(catalogue, db) if "label" in e["data"]}
    some = {e["data"]["id"]: e["data"]["label"] for e in erd.erd_elements(catalogue, db, fields={sales})
            if "label" in e["data"]}
    every = {e["data"]["id"]: e["data"]["label"] for e in erd.erd_elements(catalogue, db, fields=True)
             if "label" in e["data"]}
    assert f"• {column}" in some[sales] or f"🔑 {column}" in some[sales]
    assert len(some[sales].splitlines()) >= len(none[sales].splitlines())
    others = [k for k in some if k != sales and not k.startswith("grp:")]
    assert all(some[k] == none[k] for k in others)
    assert all(len(every[k].splitlines()) >= len(none[k].splitlines()) for k in every)


def test_copilot_test_questions_include_a_sql_script(catalogue):
    from pdcat.aipack import test_questions
    key = "py://forecasting/sql/load.sql"
    catalogue.upsert_asset(key, "sql_script", "load.sql", "forecasting", attrs={})
    catalogue.add_link(key, "reads", "sql://warehouse/forecastdb/dbo.salesdaily", "forecasting",
                       dst_key="sql://warehouse/forecastdb/dbo.salesdaily")
    catalogue.add_link(key, "writes", "table:ml.forecastoutput", "forecasting")
    qs = test_questions(catalogue)
    [q] = [q for q in qs if q["about"] == "sqlscript"]
    assert q["question"] == "What does the SQL script load.sql read and write?"
    assert "reads forecastdb.dbo.SalesDaily" in q["answer"] and "writes ml.forecastoutput" in q["answer"]


def test_f_strings_follow_attributes_and_calls_to_a_file_pattern():
    from pdcat.scan_python import scan_python_source
    found = scan_python_source(
        "import pandas as pd\nfrom datetime import datetime\n"
        "class Job:\n    def __init__(self):\n        self.topic = 'TOPIC_TOPIC'\n        self.out = 'C:/data/out'\n"
        "    def run(self, df):\n        stamp = datetime.now().strftime('%Y%m%d%H%M%S0')\n"
        "        df.to_csv(f'{self.out}/{self.topic}_{stamp}.csv', index=False)\n")
    assert ("writes", "file:C:/data/out/TOPIC_TOPIC_{}.csv") in {(r[0], r[1]) for r in found.refs}
    assert found.attrs["write_patterns"] == ["C:/data/out/TOPIC_TOPIC_{}.csv"]
    assert "unknown_files" not in found.attrs
