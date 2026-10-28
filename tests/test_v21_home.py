"""Version 2.1: home page, five quick questions, sidebar search and search results as a table."""
from pdcat import groups, quickq, searchview, suggestions

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"
STORE = "sql://warehouse/forecastdb/dbo.store"


def _files(catalogue):
    keys = []
    for grain in ("D", "W", "M"):
        k = f"files://shared/soh/SOH_{grain}.csv"
        catalogue.upsert_asset(k, "data_file", f"soh/SOH_{grain}.csv", "shared",
                               attrs={"path": f"soh/SOH_{grain}.csv", "row_count": 10})
        keys.append(k)
    catalogue.conn.commit()
    catalogue.rebuild_search()
    uid = groups.create(catalogue, "family", "SOH", {keys[0]: "daily", keys[1]: "weekly", keys[2]: "monthly"})
    return keys, uid


def test_quick_questions_pick_and_drop_off(catalogue):
    sid = suggestions.add(catalogue, "description", STORE, "dbo.Store: “the store list”",
                          payload={"key": STORE, "text": "the store list"})
    qs = quickq.todays(catalogue, today="2026-01-01")
    assert qs and qs[0]["type"] == "suggestion" and len(qs) <= quickq.COUNT
    assert len({q["key"] for q in qs}) == len(qs)  # one question per item
    suggestions.accept(catalogue, sid)
    assert all(q.get("suggestion") != sid for q in quickq.todays(catalogue, today="2026-01-01"))
    for q in quickq.todays(catalogue, today="2026-01-01"):
        if q["type"] == "describe":
            quickq.answer_describe(catalogue, q["key"], "One line about it")
            assert all(x["key"] != q["key"] for x in quickq.todays(catalogue, today="2026-01-01"))
            break


def test_search_rows_families_as_one_and_grouping(catalogue):
    keys, uid = _files(catalogue)
    results = [dict(r) for r in catalogue.search("SOH")]
    assert {r["key"] for r in results} >= set(keys)
    rows = searchview.rows_for(catalogue, results)
    fam = [r for r in rows if r["key"] == f"group://{uid}"]
    assert len(fam) == 1 and fam[0]["name"].startswith("SOH: ") and "weekly" in fam[0]["name"]
    assert not any(r["key"] in keys for r in rows)
    assert len(searchview.rows_for(catalogue, results, families_as_one=False)) >= 3
    sales = searchview.rows_for(catalogue, [dict(r) for r in catalogue.search("sales")])
    for by in searchview.GROUP_BY:
        out = searchview.grouped(sales, by)
        assert sum(len(g) for _, g in out) == len(sales)
    by_db = dict(searchview.grouped(sales, "Folder or database"))
    assert any("forecastdb" in h.lower() for h in by_db)


def test_metric_and_measure_link(catalogue):
    from pdcat.knowledge import save_metric
    m = save_metric(catalogue, "Fill Rate", definition="Shipped over ordered")
    catalogue.upsert_asset("files://r/Sales.pbit#measure/Fill Rate", "measure", "Fill Rate", "r", attrs={})
    catalogue.conn.commit()
    links = searchview.metric_links(catalogue, [{"key": f"metric://{m}", "kind": "metric", "name": "Fill Rate"},
                                                {"key": "files://r/Sales.pbit#measure/Fill Rate", "kind": "measure",
                                                 "name": "Fill Rate"}])
    assert links[0]["to"][0][0] == "files://r/Sales.pbit#measure/Fill Rate"
    assert links[1]["to"][0][0] == f"metric://{m}"


def test_contains_rows_collapse_series_and_script_outputs(catalogue, tmp_path):
    from pdcat import families
    from pdcat.contents import contents_rows
    from pdcat.scan_files import scan_folder
    root = tmp_path / "share"
    (root / "monthly").mkdir(parents=True)
    (root / "out").mkdir()
    for m in ("01", "02", "03"):
        (root / "monthly" / f"sales_2024{m}.csv").write_text("a,b\n1,2\n")
    for run in ("r1", "r2", "r3"):
        (root / "out" / f"fcst_{run}.csv").write_text("a\n1\n")
    (root / "notes.txt").write_text("hello")
    scan_folder(catalogue, "share", root)
    script = "py://proj/make.py"
    catalogue.upsert_asset(script, "script", "make.py", "proj", attrs={"write_patterns": ["out/fcst_{}.csv"]})
    catalogue.conn.commit()
    assert families.group_script_outputs(catalogue) == 1
    folder = "files://share"
    children = [dict(c) for c in catalogue.children(folder) if c["kind"] != "series_file"]
    rows = contents_rows(catalogue, folder, children)
    series = [r for r in rows if r["type"] == "dated series"]
    outputs = [r for r in rows if r["type"] == "script output"]
    assert len(series) == 1 and "3 files" in series[0]["detail"]
    assert len(outputs) == 1 and outputs[0]["files"] == 3 and outputs[0]["made by"] == "make.py"
    assert not any(r["name"].startswith("fcst_") for r in rows)
