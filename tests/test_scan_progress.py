"""Folder scans report how far they've got, skip tool folders, and don't get held up by one big file."""
import sys
import textwrap
import zipfile

import pandas as pd
from test_office import write_pbix, write_xlsx

from pdcat import scan_powerbi
from pdcat.folderroles import save
from pdcat.postscan import after_scan
from pdcat.scan_excel import SharedStrings
from pdcat.scan_files import scan_folder
from pdcat.scanprogress import find_files, has_files, printer
from pdcat.store import Catalogue


def _csv(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"a": [1, 2]}).to_csv(path, index=False)


def test_walk_skips_tool_environment_and_ignored_folders(tmp_path):
    for rel in ["keep/sales.csv", ".git/x.csv", "node_modules/pkg/y.csv", "envs/py311/conda-meta/z.csv",
                "envs/py311/Lib/site.csv", "venv2/pyvenv.cfg", "venv2/w.csv", "tmp/old.csv", "~$lock.csv"]:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        _csv(p) if p.suffix == ".csv" else p.write_text("")
    found, skipped = find_files(tmp_path, [".csv"], ignored={"tmp"})
    assert [p.relative_to(tmp_path).as_posix() for p in found[".csv"]] == ["keep/sales.csv"]
    assert skipped == 5  # .git, node_modules, the conda env, the venv, tmp
    assert has_files(tmp_path, [".csv"]) and not has_files(tmp_path / "envs", [".csv"])


def test_folder_scan_reports_each_file_and_honours_ignored_folders(tmp_path):
    folder = tmp_path / "data"
    _csv(folder / "sales.csv")
    _csv(folder / "scratch" / "junk.csv")
    write_xlsx(folder / "Sales.xlsm")
    write_pbix(folder / "Sales.pbix")
    cat = Catalogue(tmp_path / "c.db")
    save(cat, folder, {"scratch": "ignore"}, confirmed=True)
    heard = []
    counts = scan_folder(cat, "data", folder, report=lambda text, done=None, total=None: heard.append((text, done,
                                                                                                        total)))
    assert (counts["files"], counts["workbooks"], counts["reports"]) == (1, 1, 1)
    assert cat.get_asset("files://data/scratch/junk.csv") is None
    texts = [t for t, _, _ in heard]
    assert texts[0].startswith("Looking for files") and "Found 3 file(s)" in texts[1]
    assert any("Reading data file 1 of 3: sales.csv" in t for t in texts)
    assert any("Reading Power BI report 2 of 3: Sales.pbix" in t for t in texts)
    assert any("Reading Excel workbook 3 of 3: Sales.xlsm" in t for t in texts)
    assert heard[-1] == ("Saving and updating links and search …", 3, 3)


def test_after_scan_reports_every_step(tmp_path):
    cat = Catalogue(tmp_path / "c.db")
    heard = []
    out = after_scan(cat, report=lambda text, done=None, total=None: heard.append(text))
    assert heard[0].startswith("After the scan: relationship suggestions (1 of ")
    assert heard[-1].startswith("After the scan: review list")
    assert "relationships" in out and "review open" in out


def test_printer_shows_slow_notes_and_spaces_out_the_rest():
    lines = []
    report = printer(every=3600, out=lines.append)
    report("Reading data file 1 of 2: a.csv", 0, 2)
    report("Reading data file 2 of 2: b.csv", 1, 2)
    report("  b.csv took 40s")
    assert lines == ["  … Reading data file 1 of 2: a.csv", "  b.csv took 40s"]


def test_shared_strings_are_read_only_as_far_as_needed(tmp_path):
    m = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    p = tmp_path / "big.xlsx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("xl/sharedStrings.xml", f"<sst {m}>" + "".join(f"<si><t>s{i}</t></si>" for i in range(5000)) +
                   "</sst>")
    with zipfile.ZipFile(p) as z:
        strings = SharedStrings(z)
        assert strings.get(1) == "s1" and len(strings._items) == 2
        assert strings.get(4999) == "s4999" and strings.get(9999) == ""


def _fake_pbixray(tmp_path, monkeypatch, body):
    pkg = tmp_path / "fakes" / "pbixray"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(textwrap.dedent(body))
    monkeypatch.syspath_prepend(str(tmp_path / "fakes"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "fakes"))
    sys.modules.pop("pbixray", None)


def test_slow_pbixray_is_stopped_and_the_scan_carries_on(tmp_path, monkeypatch):
    _fake_pbixray(tmp_path, monkeypatch, """
        import time
        class PBIXRay:
            def __init__(self, path):
                time.sleep(30)
    """)
    p = tmp_path / "Sales.pbix"
    write_pbix(p)
    model, problem = scan_powerbi._model_in_own_process(p, timeout=2)
    assert model is None and "after 2 seconds" in problem


def test_pbixray_model_comes_back_from_its_own_process(tmp_path, monkeypatch):
    _fake_pbixray(tmp_path, monkeypatch, """
        import pandas as pd
        class PBIXRay:
            def __init__(self, path):
                self.schema = pd.DataFrame([{"TableName": "Sales", "ColumnName": "Units", "PandasDataType": "int64"}])
                self.dax_measures = pd.DataFrame([{"TableName": "Sales", "Name": "Total", "Expression": "SUM(x)"}])
                self.power_query = pd.DataFrame(columns=["TableName", "Expression"])
                self.relationships = pd.DataFrame(columns=["FromTableName", "FromColumnName", "ToTableName",
                                                           "ToColumnName"])
    """)
    p = tmp_path / "Sales.pbix"
    write_pbix(p)
    model, problem = scan_powerbi._model_in_own_process(p)
    assert problem is None
    assert model["tables"][0]["name"] == "Sales" and model["measures"][0]["name"] == "Total"
