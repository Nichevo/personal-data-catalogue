"""Version 2.3: adding many folders at once, the scan scope, and likely values for metrics."""
from __future__ import annotations

import io
from pathlib import Path

from pdcat import bulkfolders as B
from pdcat import metricvalues as MV
from pdcat import scanscope
from pdcat.knowledge import get_metric, save_metric

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"


# ---- many folders ------------------------------------------------------------------------------

def test_parse_paths_and_list_files():
    assert B.parse_paths(' "C:\\data\\a"\n- C:\\data\\b\n\nC:\\data\\a\n# comment') == ["C:\\data\\a", "C:\\data\\b"]
    rows = B.parse_list_file(b"path,name,subfolders\nC:\\x,ex,no\nC:\\y,,yes\n")
    assert rows == [{"path": "C:\\x", "name": "ex", "recursive": False}, {"path": "C:\\y", "name": "", "recursive": True}]
    assert B.parse_list_file("C:\\p\nC:\\q\n") == [{"path": "C:\\p", "name": "", "recursive": True},
                                                    {"path": "C:\\q", "name": "", "recursive": True}]


def test_preview_counts_files_and_spots_overlaps(catalogue, tmp_path: Path):
    parent = tmp_path / "projects"
    for sub, n in (("pricing", 3), ("stock", 1)):
        (parent / sub).mkdir(parents=True)
        for i in range(n):
            (parent / sub / f"f{i}.csv").write_text("a,b\n1,2\n")
    (parent / ".hidden").mkdir()
    subs = B.subfolders(str(parent))
    assert [Path(s).name for s in subs] == ["pricing", "stock"]
    catalogue.add_folder("stock", str(parent / "stock"))
    rows = B.preview(catalogue, [{"path": s} for s in subs] + [{"path": str(tmp_path / "missing")}])
    by = {r["path"]: r for r in rows}
    assert by[subs[0]]["files"] == 3 and by[subs[0]]["add"] and by[subs[0]]["treated as"] == "single project"
    assert by[subs[1]]["overlaps"] == "stock" and by[subs[1]]["name"] == "stock-2"   # the name is taken
    assert not by[str(tmp_path / "missing")]["exists"] and not by[str(tmp_path / "missing")]["add"]
    assert "stock" in B.export_list(catalogue) and B.export_list(catalogue).startswith("path,name,subfolders")


# ---- scan scope ------------------------------------------------------------------------------

def test_scope_applies_to_plain_runs_only(catalogue):
    names = ["warehouse", "extracts", "forecasting"]
    assert scanscope.apply(catalogue, names, set()) == set(names)
    scanscope.save(catalogue, ["extracts"])
    assert scanscope.apply(catalogue, names, set()) == {"extracts"}
    assert scanscope.apply(catalogue, names, {"warehouse"}) == {"warehouse"}      # --source wins
    assert scanscope.apply(catalogue, names, set(), everything=True) == set(names)  # --all wins
    scanscope.save(catalogue, ["gone"])
    assert scanscope.apply(catalogue, names, set()) == set(names)  # a scope naming nothing known scans everything


def test_ask_reads_numbers_or_falls_back_after_the_timeout(catalogue):
    names = ["warehouse", "extracts", "forecasting"]
    out = io.StringIO()
    assert scanscope.ask(catalogue, names, timeout=1, out=out, stream=io.StringIO("1 3\n")) == {"warehouse", "forecasting"}
    assert scanscope.ask(catalogue, names, timeout=1, out=out, stream=io.StringIO("all\n")) == set(names)
    scanscope.save(catalogue, ["extracts"])
    assert scanscope.ask(catalogue, names, timeout=1, out=out, stream=io.StringIO("\n")) == {"extracts"}

    class Never:
        def readline(self):
            import time
            time.sleep(3)
            return "1\n"
    assert scanscope.ask(catalogue, names, timeout=0.2, out=out, stream=Never()) == {"extracts"}
    assert "No answer" in out.getvalue()


# ---- likely values for metrics --------------------------------------------------------------

def _profile(cat):
    cat.save_profile(SALES, [
        {"name": "StoreId", "data_type": "int", "rows": 1000, "nulls": 0, "distinct": 40, "min": 1, "max": 40},
        {"name": "Units", "data_type": "decimal", "rows": 1000, "nulls": 0, "distinct": 500, "min": 0, "max": 900,
         "mean": 100, "percentiles": {"p1": 2, "p25": 50, "p50": 90, "p75": 150, "p99": 800}},
        {"name": "Forecast", "data_type": "decimal", "rows": 1000, "nulls": 0, "distinct": 500, "min": 0, "max": 950,
         "mean": 100, "percentiles": {"p1": 3, "p25": 60, "p50": 100, "p75": 160, "p99": 850}},
    ], "test")


def test_one_column_metric_takes_the_columns_range(catalogue):
    _profile(catalogue)
    uid = save_metric(catalogue, "Average units", formula="AVG(SalesDaily.Units)", sources=[SALES])
    m = get_metric(catalogue, uid)
    ins = MV.inputs(catalogue, m)
    assert [(i["column"]) for i in ins] == ["Units"]
    est = MV.estimate(catalogue, m)
    assert est["kind"] == "measured" and est["low"] == 2 and est["high"] == 800 and est["middle"] == 90
    assert "typically 2 to 800" in est["text"]


def test_ratio_metric_is_estimated_from_quartiles(catalogue):
    _profile(catalogue)
    uid = save_metric(catalogue, "Forecast ratio", formula="SUM(SalesDaily.Forecast) / SUM(SalesDaily[Units])",
                      sources=[SALES])
    m = get_metric(catalogue, uid)
    est = MV.estimate(catalogue, m)
    assert est["kind"] == "estimated"
    assert abs(est["low"] - 60 / 150) < 1e-9 and abs(est["high"] - 160 / 50) < 1e-9 and abs(est["middle"] - 100 / 90) < 1e-9
    assert "not measured" in est["text"]
    uv = MV.usual_values(catalogue, m)
    assert uv["source"] == "estimated"


def test_bare_column_names_match_the_source_tables_and_your_range_wins(catalogue):
    _profile(catalogue)
    uid = save_metric(catalogue, "Accuracy", formula="1 - ABS(Forecast - Units) / Units", sources=[SALES])
    m = get_metric(catalogue, uid)
    assert {i["column"] for i in MV.inputs(catalogue, m)} == {"Forecast", "Units"}
    assert MV.estimate(catalogue, m)["kind"] in ("estimated", "inputs")
    MV.set_usual_range(catalogue, uid, "0.6 to 0.95", "below 0.7 usually means a load failed")
    m = get_metric(catalogue, uid)
    uv = MV.usual_values(catalogue, m)
    assert uv["source"] == "yours" and uv["text"] == "0.6 to 0.95" and "load failed" in uv["note"]
    assert any(r["key"] == f"metric://{uid}" for r in catalogue.search("load failed"))


def test_no_inputs_means_no_estimate(catalogue):
    uid = save_metric(catalogue, "Mystery", formula="something / else")
    assert MV.estimate(catalogue, get_metric(catalogue, uid)) is None
    assert MV.usual_values(catalogue, get_metric(catalogue, uid)) is None


# ---- summaries ---------------------------------------------------------------------------------

def test_summary_rows_and_totals(catalogue):
    from pdcat import summaries as S
    tables = S.table_rows(catalogue)
    sales = next(r for r in tables if r["key"] == SALES)
    assert sales["columns"] == 2 and sales["rows"] == 1200 and sales["Schema"] == "dbo" and sales["used by scripts"] >= 1
    grouped = S.with_totals(tables, "Schema", S.COUNT_KEYS_TABLE, "table")
    totals = [r for r in grouped if str(r["table"]).startswith("Total")]
    assert len(totals) == 3  # dbo, ml, and the whole table
    assert totals[-1]["columns"] == sum(r["columns"] for r in tables)
    py = S.python_rows(catalogue)
    assert py[0]["project"] == "forecasting" and py[0]["scripts"] == 1 and py[0]["functions"] == 2
    assert py[0]["tables read"] >= 1 and py[0]["third-party imports"] >= 1
    assert S.powerbi_rows(catalogue) == []
    assert S.with_totals([], "(none)", S.COUNT_KEYS_PBI, "report") == []
