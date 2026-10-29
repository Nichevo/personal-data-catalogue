"""Version 2.17: versions of a report in one folder are grouped automatically, and only the current one shows."""
import os

from test_office import write_xlsx
from test_report_versions import write_pbit

from pdcat import features, groups, searchview
from pdcat.measures import across_reports
from pdcat.pbiobjects import all_objects
from pdcat.postscan import after_scan
from pdcat.powerbimap import reports
from pdcat.reportgroups import auto_groups, name_match, name_words, sync
from pdcat.scan_files import scan_folder
from pdcat.store import Catalogue

R = "files://reports/"


def test_names_match_on_a_meaningful_start():
    w = {n: name_words(n) for n in ("Sales report v2", "Sales report 2023-04 FINAL", "Sales report - copy",
                                    "SalesReport_v3", "Sales report March", "Sales report North",
                                    "Sales report South", "Sales budget", "Weekly KPI pack", "Weekly KPI pack Bob",
                                    "Inventory", "Inventory by plant", "Report", "Dashboard")}
    assert w["Sales report 2023-04 FINAL"] == w["SalesReport_v3"] == w["Sales report March"] == ["sales", "report"]
    assert name_match(w["Sales report v2"], w["Sales report - copy"]) == "same"
    assert name_match(w["Weekly KPI pack"], w["Weekly KPI pack Bob"]) == "prefix"
    # "Sales report" is only one real word: a longer name starting with it needs the content to agree.
    assert name_match(w["Sales report v2"], w["Sales report North"]) == "partial"
    assert name_match(w["Sales report North"], w["Sales report South"]) == "partial"
    assert name_match(w["Inventory"], w["Inventory by plant"]) == "partial"
    assert name_match(w["Sales report v2"], w["Sales budget"]) == ""
    assert name_match(w["Report"], w["Dashboard"]) == ""


def _folder(tmp_path):
    folder = tmp_path / "reports"
    write_pbit(folder / "Sales report.pbit", when=1_700_000_000)
    write_pbit(folder / "Sales report v2.pbit", when=1_700_100_000)
    write_pbit(folder / "Sales report 2023-04 FINAL.pbit", when=1_700_200_000,
               extra_measures=[("Avg", "AVERAGE(Sales[Units])")])
    write_pbit(folder / "Sales report - copy.pbit", when=1_699_000_000)
    # Starts the same but shows different pages: a different report, not a version.
    write_pbit(folder / "Sales report North.pbit", when=1_700_000_000, rename_page="North map")
    write_pbit(folder / "Stock.pbit", when=1_700_000_000, rename_page="Stock")
    write_pbit(folder / "archive" / "Sales report v1.pbit", when=1_690_000_000)  # another folder: only suggested
    cat = Catalogue(tmp_path / "c.db")
    scan_folder(cat, "reports", folder)
    return cat


def test_versions_in_one_folder_are_grouped_and_only_the_newest_shows(tmp_path):
    cat = _folder(tmp_path)
    assert sync(cat) == 1
    [g] = auto_groups(cat).values()
    assert {m["asset_key"] for m in g["members"]} == {R + "Sales report.pbit", R + "Sales report v2.pbit",
                                                      R + "Sales report 2023-04 FINAL.pbit",
                                                      R + "Sales report - copy.pbit"}
    assert g["main_key"] == R + "Sales report 2023-04 FINAL.pbit" and "newest" in g["attrs"]["why"]

    hits = [r["key"] for r in cat.search("sales", kinds=("report",))]
    assert R + "Sales report 2023-04 FINAL.pbit" in hits and R + "Sales report v2.pbit" not in hits
    assert R + "Sales report North.pbit" in hits
    rows = {r["key"]: r for r in searchview.rows_for(cat, [dict(r) for r in cat.search("sales", kinds=("report",))])}
    assert rows[R + "Sales report 2023-04 FINAL.pbit"]["name"].endswith("(+3 older)")
    # Show older versions brings them back, marked.
    every = [dict(r) for r in cat.search("sales", kinds=("report",), old_versions=True)]
    assert R + "Sales report v2.pbit" in {r["key"] for r in every}
    rows = {r["key"]: r for r in searchview.rows_for(cat, every)}
    assert rows[R + "Sales report v2.pbit"]["status"] == "older version"
    # The measures inside older versions are left out of search too.
    measures = {r["key"] for r in cat.search("", kinds=("measure",), limit=500)}
    assert not any(k.startswith((R + "Sales report v2.pbit").lower()) for k in measures)
    assert any(k.startswith((R + "Sales report 2023-04 FINAL.pbit").lower()) for k in measures)

    names = {r["name"]: r for r in reports(cat)}
    assert "Sales report v2.pbit" not in names and names["Sales report 2023-04 FINAL.pbit"]["older versions"] == 3
    assert "Sales report v2.pbit" in {r["name"] for r in reports(cat, old_versions=True)}
    assert all(t["in"] != "Sales report v2.pbit" for t in all_objects(cat, "tables"))
    assert any(t["in"] == "Sales report v2.pbit" for t in all_objects(cat, "tables", old_versions=True))
    in_reports = {m["report"] for e in across_reports(cat)["same_dax"] for m in e["measures"]}
    assert "Sales report 2023-04 FINAL.pbit" in in_reports and "Sales report v2.pbit" not in in_reports
    in_reports = {m["report"] for e in across_reports(cat, old_versions=True)["same_dax"] for m in e["measures"]}
    assert "Sales report v2.pbit" in in_reports


def test_your_choices_stick(tmp_path):
    cat = _folder(tmp_path)
    sync(cat)
    [g] = auto_groups(cat).values()
    groups.set_main(cat, g["uid"], R + "Sales report v2.pbit")
    sync(cat)
    [g] = auto_groups(cat).values()
    assert g["main_key"] == R + "Sales report v2.pbit" and g["attrs"]["main_by"] == "you"

    groups.remove_member(cat, g["uid"], R + "Sales report - copy.pbit")
    sync(cat)
    [g] = auto_groups(cat).values()
    assert len(g["members"]) == 3

    groups.delete(cat, g["uid"])
    sync(cat)
    assert auto_groups(cat) == {}
    assert R + "Sales report v2.pbit" in {r["key"] for r in cat.search("sales", kinds=("report",))}
    groups.allow_again(cat, "versions", R + "Sales report v2.pbit")
    groups.allow_again(cat, "versions", R + "Sales report.pbit")
    sync(cat)
    [g] = auto_groups(cat).values()
    assert {m["asset_key"] for m in g["members"]} == {R + "Sales report v2.pbit", R + "Sales report.pbit"}


def test_switching_it_off_unfolds_everything(tmp_path):
    cat = _folder(tmp_path)
    after_scan(cat)
    assert len(auto_groups(cat)) == 1
    features.set_on(cat, "report_versions", False)
    after_scan(cat)
    assert auto_groups(cat) == {}
    assert R + "Sales report v2.pbit" in {r["key"] for r in cat.search("sales", kinds=("report",))}


def test_a_new_version_joins_its_group_on_the_next_scan(tmp_path):
    cat = _folder(tmp_path)
    sync(cat)
    write_pbit(tmp_path / "reports" / "Sales report v5.pbit", when=1_800_000_000)
    scan_folder(cat, "reports", tmp_path / "reports")
    sync(cat)
    [g] = auto_groups(cat).values()
    assert len(g["members"]) == 5 and g["main_key"] == R + "Sales report v5.pbit"


def test_workbooks_are_grouped_too(tmp_path):
    folder = tmp_path / "books"
    folder.mkdir()
    for name, when in (("Forecast pack.xlsm", 1_700_000_000), ("Forecast pack v2.xlsm", 1_700_100_000),
                       ("Stock levels.xlsm", 1_700_000_000)):
        write_xlsx(folder / name)
        os.utime(folder / name, (when, when))
    cat = Catalogue(tmp_path / "c.db")
    scan_folder(cat, "books", folder)
    sync(cat)
    [g] = auto_groups(cat).values()
    assert g["main_key"] == "files://books/Forecast pack v2.xlsm" and len(g["members"]) == 2


def test_pages_fold_older_versions_and_can_show_them(catalogue, tmp_path, monkeypatch):
    import pytest
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from test_app import APP, goto

    folder = tmp_path / "reports"
    write_pbit(folder / "Sales report.pbit", when=1_700_000_000)
    write_pbit(folder / "Sales report v2.pbit", when=1_700_100_000)
    scan_folder(catalogue, "reports", folder)
    monkeypatch.setenv("PDCAT_DB", str(catalogue.path))
    app = AppTest.from_file(APP, default_timeout=10)
    app.run()  # the first visit groups versions already scanned
    assert not app.exception
    [g] = auto_groups(catalogue).values()
    for key in [f"group://{g['uid']}", R + "Sales report.pbit", R + "Sales report v2.pbit"]:
        app.query_params["asset"] = key
        app.run()
        assert not app.exception, (key, app.exception)
    app.query_params.clear()
    goto(app, "Power BI")
    names = " ".join(str(v) for d in app.dataframe for v in d.value["name"])
    assert "(+1 older)" in names and "Sales report.pbit " not in names + " "
    app.checkbox(key="browse-old-pbi").check().run()
    assert not app.exception
    names = [str(v) for d in app.dataframe for v in d.value["name"]]
    assert "Sales report.pbit" in names
    for page, box in (("Search", "search-old"), ("Power BI map", "pbimap-versions"),
                      ("Measures across reports", "mx-old")):
        goto(app, page)
        app.checkbox(key=box).check().run()
        assert not app.exception, page
