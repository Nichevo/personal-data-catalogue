"""Build a made-up catalogue the size of a real one and time the scans and after-scan steps.

    python scripts/time_big_catalogue.py C:/temp/pdcat-big

Four databases of about 720 tables and views each (tests/bigdb.py), scanned three times with some row counts
changing, then 3,300 small CSV files and this repository's own Python code. Run it before a release, as well
as the tests: the tests use small, tidy catalogues and don't show what is slow on a big one. Nothing here
touches a real database.
"""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

import pandas as pd
from bigdb import big_database

from pdcat.config import PythonSource, SqlServerSource
from pdcat.postscan import after_scan
from pdcat.scan_files import scan_folder
from pdcat.scan_python import scan_project
from pdcat.scan_sqlserver import ingest
from pdcat.store import Catalogue


def timed(name: str, step):
    began = time.monotonic()
    out = step()
    print(f"{name}: {time.monotonic() - began:.1f}s", flush=True)
    return out


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    data = out / "data"
    if not data.exists():
        for s in range(30):
            d = data / f"area{s}"
            d.mkdir(parents=True)
            for day in range(100):
                pd.DataFrame({"PlantID": range(20), "Qty": [day] * 20}).to_csv(
                    d / f"Stock{s}_2026{day // 28 + 1:02d}{day % 28 + 1:02d}.csv", index=False)
        for i in range(300):
            pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}).to_csv(data / f"misc_{i}.csv", index=False)
    cat = Catalogue(out / "catalogue.db")
    dbs = {f"Db{i}": big_database(seed=i + 1) for i in range(4)}
    rnd = random.Random(1)
    for round_ in range(3):
        for name, d in dbs.items():
            if round_:
                for o in d["objects"]:
                    if o.get("row_count") and rnd.random() < 0.3:
                        o["row_count"] = int(o["row_count"] * 1.05) + 1
            src = SqlServerSource(name="wh", server="S", databases=[name])

            def scan(src=src, name=name, d=d):
                with cat.scan("wh", prefix=f"sql://wh/{name.lower()}"):
                    ingest(cat, src, name, d)
            timed(f"scan {name}, round {round_ + 1}", scan)
        timed(f"after-scan steps, round {round_ + 1}", lambda: after_scan(cat))
    timed("scan 3,300 CSV files", lambda: scan_folder(cat, "data", data))
    timed("scan this repository's Python", lambda: scan_project(cat, PythonSource(name="pdcat", path=ROOT / "src")))
    timed("after-scan steps, last", lambda: after_scan(cat))
    print("slowest after-scan steps:", json.dumps(cat.setting("after_scan_runs")[-1]["slowest"]))
    cat.close()


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "pdcat-big"))
