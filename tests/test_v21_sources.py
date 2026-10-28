"""Version 2.1: folder roles, script versions, file families, schedules, conda environments, git, Access."""
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from pdcat import folderroles, groups, suggestions
from pdcat.config import PythonSource
from pdcat.scan_files import scan_folder
from pdcat.scan_python import scan_project
from pdcat.store import Catalogue


@pytest.fixture
def cat(tmp_path: Path):
    c = Catalogue(tmp_path / "c.db")
    yield c
    c.close()


def test_master_folder_and_single_project_roles(tmp_path: Path):
    master = tmp_path / "work"
    for p in ("forecast", "pricing"):
        (master / p / "src").mkdir(parents=True)
        (master / p / "README.md").write_text("x")
        (master / p / "environment.yml").write_text("name: x")
    found = folderroles.analyse(master)
    assert found["mode"] == "master" and set(found["projects"]) == {"forecast", "pricing"}

    single = tmp_path / "forecast"
    for d in ("code", "data/raw_data", "archive", "outputs", "tmp"):
        (single / d).mkdir(parents=True)
    found = folderroles.analyse(single)
    assert found["mode"] == "single"
    assert found["roles"] == {"code": "code", "data": "data", "data/raw_data": "raw data", "archive": "archive",
                              "outputs": "outputs", "tmp": "ignore"}


def test_confirmed_roles_ignore_and_archive(cat, tmp_path: Path):
    root = tmp_path / "proj"
    for d in ("code", "archive", "tmp"):
        (root / d).mkdir(parents=True)
    (root / "code" / "run.py").write_text("x = 1\n")
    (root / "archive" / "old.py").write_text("x = 1\n")
    (root / "tmp" / "scratch.py").write_text("x = 1\n")
    folderroles.save(cat, root, {"archive": "archive", "tmp": "ignore"})
    scan_project(cat, PythonSource(name="proj", path=root))
    names = {r[0] for r in cat.conn.execute("SELECT name FROM assets WHERE kind = 'script'")}
    assert names == {"code/run.py", "archive/old.py"}
    from pdcat.codemap import hidden_from_map
    assert "py://proj/archive/old.py" in hidden_from_map(cat)
    assert "py://proj/archive/old.py" not in hidden_from_map(cat, show_old=True)


def test_script_versions_are_suggested_with_the_current_one(cat, tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    for name in ("forecast_v2.py", "forecast_v3.py", "forecast_old.py", "other.py"):
        (root / name).write_text("x = 1\n")
    (root / "main.py").write_text("import forecast_v3\n")
    scan_project(cat, PythonSource(name="proj", path=root))
    from pdcat.versions import base_name, find_versions
    assert base_name("forecast_v2_old") == ("forecast", "v2 old")
    assert base_name("forecast - Copy") == ("forecast", "Copy")
    assert base_name("sales_20261001")[0] == "sales"
    find_versions(cat)
    [s] = suggestions.open_suggestions(cat, "versions")
    assert s["payload"]["main_key"] == "py://proj/forecast_v3.py" and len(s["payload"]["members"]) == 3
    suggestions.accept(cat, s["id"])
    assert groups.older_versions(cat) == {"py://proj/forecast_v2.py", "py://proj/forecast_old.py"}
    find_versions(cat)
    assert suggestions.open_suggestions(cat, "versions") == []


def test_file_families_by_grain(cat, tmp_path: Path):
    folder = tmp_path / "stock"
    folder.mkdir()
    for name in ("SOH_D.csv", "SOH_W.csv", "SOH_M.csv", "Sales_D.csv"):
        (folder / name).write_text("date,qty\n2026-01-01,1\n2026-01-02,2\n")
    scan_folder(cat, "stock", folder)
    from pdcat.families import find_families, split
    assert split("SOH_D", {"d": "daily"}) == ("SOH", "daily")
    find_families(cat)
    [s] = suggestions.open_suggestions(cat, "family")
    assert s["payload"]["name"] == "SOH" and sorted(s["payload"]["members"].values()) == ["daily", "monthly", "weekly"]


def test_files_a_script_writes_from_a_pattern_are_grouped(cat, tmp_path: Path):
    root = tmp_path / "proj"
    (root / "out").mkdir(parents=True)
    (root / "job.py").write_text('import pandas as pd\nrun = 7\npd.DataFrame().to_csv(f"out/result_{run}.csv")\n')
    for i in (1, 2, 3):
        (root / "out" / f"result_{i}.csv").write_text("a\n1\n")
    scan_project(cat, PythonSource(name="proj", path=root))
    from pdcat.families import group_script_outputs
    assert group_script_outputs(cat) == 1
    [g] = groups.all_groups(cat, "outputs")
    assert g["members"] == 3 and groups.get(cat, g["uid"])["attrs"]["script"] == "py://proj/job.py"


def test_schedule_csv_is_matched_to_scripts(cat, tmp_path: Path):
    root = tmp_path / "proj"
    (root / "jobs").mkdir(parents=True)
    (root / "jobs" / "refresh.py").write_text("x = 1\n")
    (root / "load_sales.py").write_text("x = 1\n")
    scan_project(cat, PythonSource(name="proj", path=root))
    csv_text = textwrap.dedent(f'''\
        "HostName","TaskName","Next Run Time","Task To Run","Schedule Type"
        "PC1","\\Refresh","01/11/2026 06:00","python {root / 'jobs' / 'refresh.py'}","Weekly"
        "PC1","\\Sales","N/A","C:\\\\run.bat load_sales","Daily"
        "PC1","\\Backup","N/A","robocopy a b","Daily"
        "HostName","TaskName","Next Run Time","Task To Run","Schedule Type"
    ''').encode("cp1252")
    from pdcat import schedules
    cols, rows = schedules.read_csv(csv_text)
    assert len(rows) == 3
    guess = schedules.guess_columns(cols)
    assert guess["name"] == "TaskName" and guess["command"] == "Task To Run"
    schedules.import_rows(cat, rows, guess, "tasks.csv")
    jobs = {j["name"]: j for j in schedules.jobs(cat)}
    assert [l["state"] for l in jobs["Refresh"]["links"]] == ["confirmed"]
    assert [l["state"] for l in jobs["Sales"]["links"]] == ["suggested"]
    assert jobs["Backup"]["links"] == []
    sales = jobs["Sales"]["links"][0]["asset_key"]
    schedules.decide(cat, jobs["Sales"]["uid"], sales, "confirmed")
    schedules.import_rows(cat, rows, guess, "tasks.csv")  # your decision survives a re-import
    assert schedules.schedules_for(cat, sales)[0]["name"] == "Sales"


def test_conda_environments_are_read_from_conda_meta(cat, tmp_path: Path, monkeypatch):
    base = tmp_path / "miniconda3"
    env = base / "envs" / "forecast"
    (env / "conda-meta").mkdir(parents=True)
    (base / "conda-meta").mkdir(parents=True)
    for name, ver in (("pandas", "2.2.1"), ("python", "3.11.9"), ("numpy", "1.26.4")):
        (env / "conda-meta" / f"{name}-{ver}-0.json").write_text(json.dumps(
            {"name": name, "version": ver, "build": "0", "channel": "https://conda.anaconda.org/conda-forge/win-64"}))
    dist = env / "Lib" / "site-packages" / "lightgbm-4.3.0.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text("Name: lightgbm\nVersion: 4.3.0\n")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    from pdcat import condaenvs
    assert condaenvs.scan_envs(cat) >= 2
    e = condaenvs.get_env(cat, "forecast")
    assert e["python"] == "3.11.9"
    by = {p["name"]: p for p in e["packages"]}
    assert by["pandas"]["channel"] == "conda-forge" and by["lightgbm"]["channel"] == "pypi"

    root = tmp_path / "proj"
    root.mkdir()
    (root / "environment.yml").write_text("name: forecast\ndependencies:\n  - pandas>=2.3\n  - scikit-learn\n")
    (root / "m.py").write_text("import pandas, sklearn, numpy\n")
    scan_project(cat, PythonSource(name="proj", path=root))
    condaenvs.guess_project_envs(cat)
    assert condaenvs.project_env(cat, "py://proj")["env_name"] == "forecast"
    rows = {r["package"]: r["status"] for r in condaenvs.compare_installed(cat, "py://proj", "forecast")}
    assert rows["pandas"].startswith("different version") and rows["scikit-learn"].startswith("missing")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_history_and_owner_suggestions(cat, tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n")

    def git(*args):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                       env={"GIT_AUTHOR_NAME": "Sam", "GIT_AUTHOR_EMAIL": "s@example.com", "GIT_COMMITTER_NAME": "Sam",
                            "GIT_COMMITTER_EMAIL": "s@example.com", "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    git("init", "-q")
    git("add", ".")
    git("commit", "-qm", "first")
    (root / "a.py").write_text("x = 2\n")
    git("commit", "-qam", "second")
    scan_project(cat, PythonSource(name="proj", path=root))
    from pdcat import gitinfo
    assert gitinfo.scan_git(cat) == 1
    assert [c["message"] for c in gitinfo.history(cat, "py://proj/a.py")] == ["second", "first"]
    [s] = suggestions.open_suggestions(cat, "owner")
    suggestions.accept(cat, s["id"])
    from pdcat.curation import item_info
    assert item_info(cat, "py://proj/a.py")["owner"] == "Sam"


def test_access_files_are_listed_even_when_they_cannot_be_read(cat, tmp_path: Path):
    folder = tmp_path / "db"
    folder.mkdir()
    (folder / "orders.accdb").write_bytes(b"\x00" * 64)
    counts = scan_folder(cat, "db", folder)
    assert counts["access"] == 1
    a = cat.get_asset("files://db/orders.accdb")
    assert a["kind"] == "access_db"
    from pdcat.scan_access import engine_status
    ok, why = engine_status()
    if not ok:
        assert a["attrs"]["read_error"] == why


def test_access_linked_tables_point_at_their_source():
    from pdcat.scan_access import link_target
    odbc = {"name": "dbo_Sales", "source_table": "dbo.Sales", "odbc": True,
            "connect": "ODBC;DRIVER=SQL Server;SERVER=x;DATABASE=SalesDW;Trusted_Connection=Yes"}
    assert link_target(odbc)[0] == "table:salesdw.dbo.sales"
    other = {"name": "Prices", "source_table": "Prices", "odbc": False, "connect": ";DATABASE=C:\\data\\prices.accdb"}
    assert link_target(other)[0] == "file:C:\\data\\prices.accdb"


def test_outputs_that_stop_refreshing_go_to_review(cat):
    from pdcat import freshness
    day = 86400
    log = {"files://x/out.csv": [1000 * day, 1007 * day, 1014 * day]}
    assert freshness.status(cat, "files://x/out.csv", log, now=1016 * day)["state"] == "refreshing"
    stopped = freshness.status(cat, "files://x/out.csv", log, now=1060 * day)
    assert stopped["state"] == "stopped" and stopped["every_days"] == 7
