"""Dated file series, following paths through code, hand-made links, and duplicates."""
import textwrap

import pandas as pd
import pytest

from pdcat.config import PythonSource
from pdcat.scan_files import scan_folder
from pdcat.scan_python import scan_project, scan_python_source
from pdcat.store import Catalogue

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"


def _csv(path, **cols):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(cols).to_csv(path, index=False)


# ---- series ------------------------------------------------------------------

def test_dated_files_become_one_series(tmp_path):
    root = tmp_path / "extracts"
    for m, rows in (("01", 3), ("02", 4), ("04", 5)):
        _csv(root / f"sales_2026-{m}.csv", store=list(range(rows)), units=[1.5] * rows)
    _csv(root / "sales_2026-05.csv", store=[1], units=[2.0], region=["N"])  # a new column
    _csv(root / "stores.csv", store=[1, 2])
    cat = Catalogue(tmp_path / "c.db")
    counts = scan_folder(cat, "extracts", root)
    assert counts == {"files": 5, "series": 1, "reports": 0, "workbooks": 0, "access": 0, "errors": 0, "documents": 0}
    key = "files://extracts/~series/sales_{yyyy}{mm}.csv"
    s = cat.get_asset(key)
    a = s["attrs"]
    assert s["kind"] == "file_series" and a["frequency"] == "monthly"
    assert (a["first"], a["last"], a["missing"]) == ("2026-01", "2026-05", ["2026-03"])
    assert [f["rows"] for f in a["files"]] == [3, 4, 5, 1]
    assert a["layout_changes"][0]["file"] == "sales_2026-05.csv" and "region" in a["layout_changes"][0]["detail"]
    assert [c["name"] for c in cat.columns(key)] == ["store", "units", "region"]  # newest file's layout
    assert cat.profile(key)[0]["row_count"] == 1
    # Only the series and the stand-alone file show up in search and counts.
    assert {r["key"] for r in cat.search("sales")} == {key}
    assert cat.stats()["kinds"] == {"folder": 1, "file_series": 1, "data_file": 1}
    # Split the series: the files come back one by one.
    cat.set_setting("series_split", [key])
    scan_folder(cat, "extracts", root)
    assert cat.get_asset("files://extracts/sales_2026-01.csv")["kind"] == "data_file"
    assert cat.get_asset(key)["status"] == "missing"


def test_dates_in_folders(tmp_path):
    root = tmp_path / "drops"
    _csv(root / "2026" / "01" / "orders.csv", id=[1])
    _csv(root / "2026" / "02" / "orders.csv", id=[1, 2])
    _csv(root / "2026" / "03" / "orders_2026-04-02.csv", id=[1])  # disagrees: the file name wins
    _csv(root / "2026" / "03" / "orders_2026-04-03.csv", id=[1])
    cat = Catalogue(tmp_path / "c.db")
    scan_folder(cat, "drops", root)
    s = cat.get_asset("files://drops/~series/{yyyy}/{mm}/orders.csv")
    assert s["attrs"]["file_count"] == 2
    other = cat.get_asset("files://drops/~series/{yyyy}/{mm}/orders_{yyyy}{mm}{dd}.csv")
    assert other["attrs"]["mismatches"] and "going by the file name" in other["attrs"]["mismatches"][0]["detail"]


def test_grouping_can_be_switched_off(tmp_path):
    root = tmp_path / "x"
    _csv(root / "a_2026-01-01.csv", v=[1])
    _csv(root / "a_2026-01-02.csv", v=[1])
    cat = Catalogue(tmp_path / "c.db")
    cat.set_setting("series_grouping", False)
    assert scan_folder(cat, "x", root)["series"] == 0


# ---- following paths -------------------------------------------------------------

def _refs(code: str) -> dict:
    found = scan_python_source(textwrap.dedent(code))
    return {(rel, ref): found.how.get((rel, ref)) for rel, ref, _ in found.refs}


def test_paths_through_variables_join_and_pathlib():
    refs = _refs('''
        import os
        from pathlib import Path
        import pandas as pd
        DATA = "data"
        BASE = Path("C:/work") / "out"
        def run(df, month):
            src = os.path.join(DATA, "raw", "sales.csv")
            df = pd.read_csv(src)
            df.to_parquet(BASE / "clean.parquet")
            df.to_csv(f"{DATA}/monthly_{month}.csv")
            out = "results/{}.csv".format(month)
            df.to_csv(out)
        ''')
    assert refs[("reads", "file:data/raw/sales.csv")] == "os.path.join"
    assert refs[("writes", "file:C:/work/out/clean.parquet")] == "pathlib path"
    assert refs[("writes", "file:data/monthly_{}.csv")] == "f-string pattern"
    assert refs[("writes", "file:results/{}.csv")] == "string formatting"


def test_dynamic_to_sql_and_bcp():
    refs = _refs('''
        import subprocess
        TABLE = "SalesStage"
        def load(df, engine, path):
            df.to_sql(TABLE, engine, schema="stg")
            subprocess.run(["bcp", "dbo.SalesDaily", "in", "exports/sales.csv", "-T"])
            subprocess.run("bcp dbo.Store out stores.csv -c")
        ''')
    assert ("writes", "table:stg.salesstage") in refs
    assert refs[("writes", "table:dbo.salesdaily")] == "bcp command"
    assert ("reads", "file:exports/sales.csv") in refs
    assert ("reads", "table:dbo.store") in refs and ("writes", "file:stores.csv") in refs


def test_paths_from_a_config_file(catalogue, tmp_path):
    proj = tmp_path / "cfgproj"
    (proj / "data").mkdir(parents=True)
    (proj / "config.yaml").write_text("paths:\n  sales_file: data/sales_extract.csv\n")
    _csv(proj / "data" / "sales_extract.csv", x=[1])
    (proj / "run.py").write_text("import pandas as pd, yaml\ncfg = yaml.safe_load(open('config.yaml'))\n"
                                 "df = pd.read_csv(cfg['paths']['sales_file'])\n"
                                 "df.to_csv('../cfgproj/./data/out.csv')\n")
    scan_project(catalogue, PythonSource(name="cfgproj", path=proj))
    links = {(l["relation"], l["dst_ref"]): l for l in catalogue.links_from("py://cfgproj/run.py")}
    hit = links[("reads", "file:data/sales_extract.csv")]
    assert hit["dst_key"] == "py://cfgproj/data/sales_extract.csv" and hit["how"] == "config file"
    assert links[("writes", "file:cfgproj/data/out.csv")]["how"] == "pandas to_csv, relative path"


# ---- links by hand -----------------------------------------------------------------

def test_hand_made_links_survive_rescans(catalogue, project):
    from pdcat.links import add_manual_link, hide_link, links_for, unlinked_data
    script = "py://forecasting/src/features.py"
    store = "sql://warehouse/forecastdb/dbo.store"
    assert store in {r["key"] for r in unlinked_data(catalogue)}
    add_manual_link(catalogue, script, "writes", store)
    hide_link(catalogue, script, "reads", SALES)
    scan_project(catalogue, PythonSource(name="forecasting", path=project, default_database="warehouse/ForecastDB"))
    _, made_by, _ = links_for(catalogue, store, catalogue.get_asset(store))
    assert [l["src_key"] for l in made_by] == [script] and made_by[0]["how"] == "added by hand"
    assert store not in {r["key"] for r in unlinked_data(catalogue)}
    assert SALES not in {l["dst_key"] for l in catalogue.links_from(script) if l["relation"] == "reads"}


# ---- duplicates ---------------------------------------------------------------------

def test_duplicate_files_and_copied_folders(tmp_path):
    from pdcat.duplicates import report
    a, b = tmp_path / "share" / "sales", tmp_path / "backup" / "sales"
    for d in (a, b):
        _csv(d / "jan.csv", v=[1, 2, 3])
        _csv(d / "feb.csv", v=[4, 5, 6])
    _csv(a / "Copy of stores.csv", s=[9])
    _csv(a / "stores.csv", s=[8])
    cat = Catalogue(tmp_path / "c.db")
    cat.set_setting("series_grouping", False)
    scan_folder(cat, "share", tmp_path / "share")
    scan_folder(cat, "backup", tmp_path / "backup")
    r = report(cat)
    assert len(r["same_file"]) == 2
    assert r["copied_folders"][0]["how"] == "partial copy" and r["copied_folders"][0]["shared_files"] == 2
    assert r["named_copies"][0]["original"] == "sales/stores.csv"


def test_mapped_drives_and_overlapping_folders(tmp_path):
    from pdcat.duplicates import add_drive_alias, canonical, drive_aliases
    cat = Catalogue(tmp_path / "c.db")
    add_drive_alias(cat, "s:", r"\\fileserver\shared")
    aliases = drive_aliases(cat)
    assert canonical(r"S:\Sales\Extracts", aliases) == canonical(r"\\FileServer\Shared\sales\extracts\\", aliases)
    cat.add_folder("sales", r"S:\Sales")
    assert cat.folder_overlaps(r"\\fileserver\shared\Sales\2026") == ["sales"]
    assert cat.folder_overlaps(r"S:\Finance") == []
    with pytest.raises(ValueError):
        add_drive_alias(cat, "nope", "x")
