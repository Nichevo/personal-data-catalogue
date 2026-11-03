"""Version 2.8.3: fixes from the code review before 2.9, and the slow after-scan steps made quicker."""
import ast
import json
import subprocess
import textwrap
from types import SimpleNamespace

from pdcat import sqlchecks
from pdcat.codemap import Graph
from pdcat.commentdrafts import column_comments
from pdcat.datasources import MAX_ADDRESSES, merge, record_sources
from pdcat.metricfind import _select_items
from pdcat.scan_python import managed_imports
from pdcat.store import Catalogue
from pdcat.subway import helper_functions
from pdcat.triage import _JUNK_NAME

# ---- subway helpers ------------------------------------------------------------------------------

def _graph(edges, touching, unexpanded=()):
    g = Graph()
    names = {n for a, b in edges for n in (a, b)} | set(touching)
    for n in names:
        g.nodes[n] = {"label": n, "kind": "focus" if n == "root" else "function", "group": "f.py"}
        if n in unexpanded:
            g.nodes[n]["unexpanded"] = True
    for a, b in edges:
        g.edges[(a, b, "call")]
    for n in touching:
        g.nodes[f"t:{n}"] = {"label": "T", "kind": "table", "group": None}
        g.edges[(n, f"t:{n}", "reads")]
    return g


def test_functions_calling_each_other_still_lead_to_data_whatever_the_order():
    # root -> A, root -> B, A <-> B, A -> C, and C reads a table: neither A nor B is a "no data" helper.
    for order in range(30):
        edges = [("root", "A"), ("root", "B"), ("A", "B"), ("B", "A"), ("A", "C")]
        edges = edges[order % 5:] + edges[:order % 5]
        helpers = helper_functions(_graph(edges, ["C"]))
        assert "A" not in helpers and "B" not in helpers, order


def test_a_function_cut_off_by_the_depth_limit_is_not_called_a_no_data_helper():
    g = _graph([("root", "A"), ("A", "deep")], [], unexpanded={"deep"})
    assert "deep" not in helper_functions(g)
    assert "A" in helper_functions(g)   # fully followed and reaches nothing: still a helper


# ---- data source addresses -----------------------------------------------------------------------

def test_merged_addresses_stay_at_ten():
    first = {"type": "api", "name": "h", "relation": "reads", "addresses": [f"u{i}" for i in range(12)]}
    second = {"type": "api", "name": "h", "relation": "reads", "addresses": [f"v{i}" for i in range(5)]}
    out = merge([first, second])
    assert len(out[0]["addresses"]) == MAX_ADDRESSES


def test_a_rescan_drops_addresses_the_code_no_longer_uses(tmp_path):
    cat = Catalogue(tmp_path / "c.db")
    src = {"type": "api", "name": "host", "relation": "reads", "how": "x", "line": 1}

    def scan(addresses):
        token = cat.begin_scan("proj")
        cat.upsert_asset("py://proj/a.py", "script", "a.py", "proj")
        record_sources(cat, "py://proj/a.py", "proj", [{**src, "addresses": addresses}])
        cat.finish_scan(token)

    scan(["https://host/old"])
    scan(["https://host/new"])
    a = cat.get_asset("ext://proj/api/host")
    assert a["attrs"]["addresses"] == ["https://host/new"]


# ---- home-built module loaders -------------------------------------------------------------------

def test_a_loader_entry_with_a_file_path_is_one_module_and_its_folder_is_kept():
    out, _ = managed_imports(ast.parse(textwrap.dedent('''
        modules_to_load = {"Management": {"file": "C:/code/tools/mgmt_v2.py", "functions": ["f1"]}}
    ''')), with_instances=True)
    assert [d["module"] for d in out] == ["Management"]
    out, _ = managed_imports(ast.parse(textwrap.dedent('''
        modules_to_load = {"Management": {"module_path": "C:/code/tools/Management.py", "functions": ["f1"]}}
    ''')), with_instances=True)
    assert out[0]["folders"] == ["C:/code/tools"]


def test_a_label_is_not_taken_for_a_module_name():
    out, _ = managed_imports(ast.parse(textwrap.dedent('''
        x = {"name": "Management Tools", "functions": ["f1"]}
    ''')), with_instances=True)
    assert out == []


def test_two_same_named_copies_the_loader_cant_tell_apart_are_not_linked():
    from pdcat.codemap import managed_target
    files = {"py://a/Management.py": "C:/one/Management.py", "py://b/Management.py": "C:/two/Management.py"}
    modules = {"Management": "py://a/Management.py"}
    assert managed_target("Management", [], files, set(), modules) is None
    assert managed_target("Management", ["C:/two"], files, set(), modules) == "py://b/Management.py"


def test_functions_reading_web_addresses_dont_list_them_as_files(tmp_path):
    from pdcat.scan_python import scan_file
    p = tmp_path / "x.py"
    p.write_text("import pandas as pd\n\ndef load():\n    return pd.read_csv('https://host/x.csv')\n")
    found = scan_file(p)
    assert not any("https://" in ref for refs in (found.attrs.get("function_refs") or {}).values()
                   for _, ref in refs)


# ---- metric suggestions --------------------------------------------------------------------------

def test_business_names_are_not_working_columns():
    for name in ("newcustomers", "temperature", "sales2024", "revenuefy24", "testedunits"):
        assert not _JUNK_NAME.match(name), name
    for name in ("col1", "x", "tmp", "temp3", "new1", "calc2", "value4", "v01", "df12"):
        assert _JUNK_NAME.match(name), name


# ---- pbixray failures ----------------------------------------------------------------------------

def test_pbixray_failing_on_a_file_says_so_instead_of_install_pbixray(monkeypatch, tmp_path):
    import importlib.util

    from pdcat import scan_powerbi
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout=b"", stderr=b"Traceback...\nMemoryError"))
    model, problem = scan_powerbi._model_in_own_process(tmp_path / "r.pbix")
    assert model is None and "couldn't read" in problem and "MemoryError" in problem
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=b"pdcat-model:null\n", stderr=b""))
    model, problem = scan_powerbi._model_in_own_process(tmp_path / "r.pbix")
    assert model is None and "no model" in problem


# ---- speed: the same answers, quicker ------------------------------------------------------------

def _old_select_items(sql):
    """The 2.8.2 version, a regex per character, to check the quicker one gives the same answers."""
    import re
    out, low = [], sql.lower()
    for m in re.finditer(r"\bselect\b", low):
        depth, i, start, items = 0, m.end(), m.end(), []
        while i < len(sql):
            ch = sql[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and ch == ",":
                items.append(sql[start:i])
                start = i + 1
            elif depth == 0 and re.match(r"\bfrom\b|\binto\b", low[i:i + 5]) and (i == 0 or not low[i - 1].isalnum()):
                break
            i += 1
        items.append(sql[start:i])
        out += [x.strip() for x in items if x.strip()]
    return out


def test_select_items_reads_the_same_as_before():
    for sql in ("SELECT a, fromage, intox, SUM(b) AS total, (SELECT 1 FROM y) z FROM t; select q into #x from s",
                "select x_from, y FROM t", "SELECT from_date,into_x FROM\nt", "SELECT a FROM", "select a frOm b",
                "SELECT [From], b from t", "select a,b into"):
        assert _select_items(sql) == _old_select_items(sql), sql


def test_column_comments_still_found():
    sql = "SELECT\n  PlantID, -- the plant\n  Qty AS OnHand -- stock on hand\n  , Other\nFROM t"
    assert column_comments(sql) == {"PlantID": "the plant", "OnHand": "stock on hand"}


def test_sql_checks_are_kept_and_reused(catalogue):
    from pdcat.sqllibrary import build_library
    catalogue.save_snippet("Sales", "SELECT * FROM dbo.SalesDaily WITH (NOLOCK)")
    build_library(catalogue)
    first = sqlchecks.check_all(catalogue)
    saved = catalogue.conn.execute("SELECT COUNT(*) FROM sql_check_cache").fetchone()[0]
    assert saved >= 1
    sqlchecks.check_sql.cache_clear()
    calls = []
    real = sqlchecks.check_sql
    sqlchecks.check_sql = lambda s: calls.append(s) or real(s)
    try:
        assert sqlchecks.check_all(catalogue) == first
    finally:
        sqlchecks.check_sql = real
    assert calls == []   # nothing checked again: every piece of SQL came from the saved results
    rows = [json.loads(r[0]) for r in catalogue.conn.execute("SELECT found FROM sql_check_cache")]
    assert any(["nolock", ""] in found for found in rows)


# ---- second pass: 2.5.4 to 2.8.2 -----------------------------------------------------------------

def test_a_top_level_only_scan_doesnt_count_log_folders(tmp_path):
    from pdcat.scanprogress import find_files
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "a.log").write_text("x")
    (tmp_path / "a.csv").write_text("a\n1\n")
    logs: list = []
    find_files(tmp_path, [".csv"], recursive=False, logs=logs)
    assert logs == []
    find_files(tmp_path, [".csv"], recursive=True, logs=logs)
    assert [x["folder"] for x in logs] == ["logs"]


def test_a_day_folder_made_ahead_of_time_doesnt_hide_the_real_recent_ones(tmp_path):
    from datetime import date, timedelta

    from pdcat.datedfolders import newest_date, span
    today = date.today()
    real = [today - timedelta(days=i) for i in range(3)]
    ahead = today + timedelta(days=60)
    for d in [*real, ahead]:
        (tmp_path / d.strftime("%Y%m%d")).mkdir()
    spans = {d.strftime("%Y%m%d"): span(d.strftime("%Y%m%d")) for d in [*real, ahead]}
    newest, days = newest_date(str(tmp_path), spans)
    assert newest == today and days


def test_quarter_folders_are_dated():
    from datetime import date

    from pdcat.datedfolders import span
    assert span("2026Q2") == (date(2026, 4, 1), date(2026, 6, 30))
    assert span("Q2 2026") == (date(2026, 4, 1), date(2026, 6, 30))
    assert span("Questions") is None


def test_undoing_a_removed_gold_mark_brings_back_its_ok_to_differ_answers(tmp_path):
    from test_v214_scans import _pbit

    from pdcat import edits, gold
    from pdcat.scan_files import scan_folder
    folder = tmp_path / "reports"
    _pbit(folder / "Sales.pbit", [("Total Units", "SUM(Sales[Units])")])
    _pbit(folder / "Stock.pbit", [("Total Units", "SUMX(Sales, Sales[Units])")])
    cat = Catalogue(tmp_path / "c.db")
    scan_folder(cat, "reports", folder)
    keys = {r["parent"].rsplit("/", 1)[-1]: r["key"] for r in cat.conn.execute(
        "SELECT key, parent FROM assets WHERE kind = 'measure'")}
    std, other = keys["Sales.pbit"], keys["Stock.pbit"]
    gold.mark(cat, std)
    gold.ok_to_differ(cat, std, other, "stock counts differently")
    gold.unmark(cat, std)
    assert cat.conn.execute("SELECT COUNT(*) FROM gold_ok").fetchone()[0] == 0
    last = cat.conn.execute("SELECT id FROM edits ORDER BY id DESC LIMIT 1").fetchone()[0]
    edits.undo(cat, last)
    assert gold.get(cat, std)
    assert cat.conn.execute("SELECT note FROM gold_ok WHERE gold_key = ? AND item_key = ?",
                            (std, other)).fetchone()[0] == "stock counts differently"
    cat.close()


def test_review_text_keeps_names_as_written():
    from pdcat.review import _first_upper
    assert _first_upper("named 'Total Sales' instead of 'Sales'") == "Named 'Total Sales' instead of 'Sales'"
    assert _first_upper("") == ""


def test_metric_kinds_match_whole_words():
    from pdcat.organise import metric_kind
    assert metric_kind("Sales percentage") == "Rates, ratios and shares"
    assert metric_kind("Average order value") == "Money and margin"
    assert metric_kind("Latest forecast") == "Other"
    assert metric_kind("Returns") == "Other"
    assert metric_kind("Stock turns") == "Stock and coverage"
    assert metric_kind("Late orders") == "Service and delivery"
    assert metric_kind("Stock age") == "Stock and coverage"
    assert metric_kind("Order age") == "Time"


def test_power_bi_table_names_with_a_slash_are_not_paths():
    from pdcat.pathbits import split_name
    assert split_name("Sales / Units", "pbi_table") == ("Sales / Units", "")
    assert split_name("Sales / Units", "pq_query") == ("Sales / Units", "")
    assert split_name("Finance/Sales.pbix", "report") == ("Sales.pbix", "Finance")


def test_keys_with_underscores_or_percent_only_match_themselves(tmp_path):
    cat = Catalogue(tmp_path / "c.db")
    with cat.scan("wh", prefix="sql://wh/sales_dw"):
        cat.upsert_asset("sql://wh/sales_dw", "database", "Sales_DW", "wh")
        cat.upsert_asset("sql://wh/sales_dw/dbo.t", "table", "dbo.t", "wh")
    with cat.scan("wh", prefix="sql://wh/salesxdw"):
        cat.upsert_asset("sql://wh/salesxdw", "database", "SalesXDW", "wh")
        cat.upsert_asset("sql://wh/salesxdw/dbo.t", "table", "dbo.t", "wh")
    # rescanning sales_dw must not reach into salesxdw ("_" would match any one character)
    with cat.scan("wh", prefix="sql://wh/sales_dw"):
        cat.upsert_asset("sql://wh/sales_dw", "database", "Sales_DW", "wh")
    assert cat.get_asset("sql://wh/salesxdw/dbo.t")["status"] == "active"
    assert cat.get_asset("sql://wh/sales_dw/dbo.t")["status"] == "missing"
    # a report with % in its name keeps what's inside it when it isn't opened this time
    token = cat.begin_scan("files")
    cat.upsert_asset("files://x/Margin 100%.pbix", "report", "Margin 100%.pbix", "files")
    cat.upsert_asset("files://x/Margin 100%.pbix/m/a", "measure", "a", "files", parent="files://x/Margin 100%.pbix/m")
    cat.finish_scan(token)
    token = cat.begin_scan("files")
    cat.keep_seen("files://x/Margin 100%.pbix")
    cat.finish_scan(token)
    assert cat.get_asset("files://x/Margin 100%.pbix/m/a")["status"] == "active"
    cat.close()


def test_history_and_archive_folder_names_are_one_list():
    from pdcat.folderroles import role_from_name
    from pdcat.historyfolders import HISTORY_NAME
    for name in ("prev", "prior", "past", "older", "archive", "old"):
        assert HISTORY_NAME.match(name) and role_from_name(name) == "archive", name


def test_a_scan_counts_as_a_change_so_cached_pages_refresh(tmp_path):
    cat = Catalogue(tmp_path / "c.db")
    before = cat.revision()
    with cat.scan("wh"):
        cat.upsert_asset("sql://wh/db", "database", "db", "wh")
    assert cat.revision() > before
    cat.close()
