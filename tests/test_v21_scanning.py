"""Version 2.1 scanning: scripts that run scripts, output files followed further, unknown files."""
import textwrap
from pathlib import Path

from pdcat.config import PythonSource
from pdcat.scan_python import scan_project, scan_python_source
from pdcat.store import Catalogue


def refs(code: str) -> set[tuple[str, str]]:
    return {(rel, ref) for rel, ref, _ in scan_python_source(textwrap.dedent(code)).refs}


def test_path_passed_in_as_a_function_argument():
    got = refs('''
        def save(df, filepath):
            df.to_csv(filepath)

        def main(df):
            save(df, "outputs/forecast.csv")
    ''')
    assert ("writes", "file:outputs/forecast.csv") in got


def test_path_from_argparse_default_self_and_helper():
    got = refs('''
        import argparse
        p = argparse.ArgumentParser()
        p.add_argument("--out-file", default="results/out.parquet")
        args = p.parse_args()

        def where():
            return "results/helper.csv"

        class Job:
            def __init__(self):
                self.target = "results/self.csv"
            def run(self, df):
                df.to_csv(self.target)

        def go(df):
            df.to_parquet(args.out_file)
            df.to_csv(where())
    ''')
    assert {("writes", "file:results/out.parquet"), ("writes", "file:results/helper.csv"),
            ("writes", "file:results/self.csv")} <= got


def test_dictionary_paths_and_both_branches():
    got = refs('''
        PATHS = {"sales": "data/sales.csv", "out": "out/x.csv"}
        import pandas as pd
        df = pd.read_csv(PATHS["sales"])
        if df.empty:
            dest = "out/empty.csv"
        else:
            dest = "out/full.csv"
        df.to_csv(dest)
    ''')
    assert {("reads", "file:data/sales.csv"), ("writes", "file:out/empty.csv"),
            ("writes", "file:out/full.csv")} <= got


def test_more_writers():
    got = refs('''
        import numpy as np, shutil, polars as pl, pandas as pd
        from pathlib import Path
        from openpyxl import Workbook
        np.save("models/weights.npy", w)
        shutil.copy("data/in.csv", "archive/in.csv")
        pl.DataFrame().write_csv("out/pl.csv")
        Path("out/notes.txt").write_text("x")
        wb = Workbook()
        wb.save("out/book.xlsx")
        with pd.ExcelWriter("out/report.xlsx") as writer:
            df.to_excel(writer, sheet_name="a")
    ''')
    assert {("writes", "file:models/weights.npy"), ("reads", "file:data/in.csv"), ("writes", "file:archive/in.csv"),
            ("writes", "file:out/pl.csv"), ("writes", "file:out/notes.txt"), ("writes", "file:out/book.xlsx"),
            ("writes", "file:out/report.xlsx")} <= got
    assert not any("writer" in ref for _, ref in got)


def test_unknown_files_are_recorded_with_line_and_code():
    found = scan_python_source(textwrap.dedent('''
        import sys
        def save(df):
            df.to_csv(sys.argv[1])
    '''))
    unknown = found.attrs["unknown_files"]
    assert unknown[0]["line"] == 4 and "to_csv" in unknown[0]["code"] and unknown[0]["relation"] == "writes"
    assert unknown[0]["function"] == "save"


def test_scripts_that_run_scripts_and_constants_from_settings(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "jobs").mkdir(parents=True)
    (root / "settings.py").write_text('OUTPUT = "out/final.csv"\n')
    (root / "jobs" / "step2.py").write_text('from settings import OUTPUT\nimport pandas as pd\n'
                                            'pd.DataFrame().to_csv(OUTPUT)\n')
    (root / "jobs" / "step3.py").write_text('print(1)\n')
    (root / "run_all.py").write_text(textwrap.dedent('''
        import subprocess, runpy
        subprocess.run(["python", "jobs/step2.py"])
        subprocess.run("python -m jobs.step3", shell=True)
    '''))
    (root / "explore.ipynb").write_text('{"cells": [{"cell_type": "code", "source": ["%run jobs/step3.py"]}]}')
    cat = Catalogue(tmp_path / "c.db")
    scan_project(cat, PythonSource(name="proj", path=root))
    step2, step3 = "py://proj/jobs/step2.py", "py://proj/jobs/step3.py"
    from pdcat.codemap import called_by
    assert {r["src_key"] for r in called_by(cat, step2)} == {"py://proj/run_all.py"}
    assert {r["src_key"] for r in called_by(cat, step3)} == {"py://proj/run_all.py", "py://proj/explore.ipynb"}
    assert any(l["dst_ref"] == "file:out/final.csv" and "settings" in (l["how"] or "")
               for l in cat.links_from(step2))
    cat.close()


def test_file_in_another_folder_is_linked_and_flagged(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "load.py").write_text('import pandas as pd\npd.read_csv("inputs/sales.csv")\n')
    other = tmp_path / "shared"
    other.mkdir()
    (other / "sales.csv").write_text("a,b\n1,2\n")
    cat = Catalogue(tmp_path / "c.db")
    from pdcat.scan_files import scan_folder
    scan_folder(cat, "shared", other)
    scan_project(cat, PythonSource(name="proj", path=root))
    link = next(l for l in cat.links_from("py://proj/load.py") if l["dst_ref"] == "file:inputs/sales.csv")
    assert link["dst_key"] and "another folder" in link["match_note"]
    from pdcat.review import open_tasks, refresh_tasks
    refresh_tasks(cat)
    assert any(t["rule"] == "wrong_folder" for t in open_tasks(cat, limit=100))
    cat.close()


def test_answering_an_unknown_file_links_it(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "save.py").write_text('import sys\nimport pandas as pd\npd.DataFrame().to_csv(sys.argv[1])\n')
    (root / "result.csv").write_text("a\n1\n")
    cat = Catalogue(tmp_path / "c.db")
    scan_project(cat, PythonSource(name="proj", path=root))
    from pdcat.review import open_tasks, refresh_tasks
    from pdcat.unknownfiles import answer, unknown_files
    refresh_tasks(cat)
    assert any(t["rule"] == "unknown_file" for t in open_tasks(cat, limit=100))
    [u] = unknown_files(cat, "py://proj/save.py")
    target = next(r[0] for r in cat.conn.execute("SELECT key FROM assets WHERE kind = 'data_file'"))
    answer(cat, u, target)
    assert unknown_files(cat) == []
    assert any(l["dst_key"] == target and l["relation"] == "writes" for l in cat.links_from("py://proj/save.py"))
    scan_project(cat, PythonSource(name="proj", path=root))  # kept through a rescan
    assert any(l["dst_key"] == target for l in cat.links_from("py://proj/save.py"))
    cat.close()
