"""Old versions of Power BI reports: grouped by name or by content, with what changed between them."""
import copy
import json
import os
import zipfile

from test_office import TMSL, layout

from pdcat import groups, suggestions
from pdcat.reportversions import diff, find_report_versions, similarity, summary, timeline
from pdcat.scan_files import scan_folder
from pdcat.store import Catalogue
from pdcat.versions import find_versions


def write_pbit(path, *, extra_measures=(), drop_page=False, rename_page=None, when=None):
    """A .pbit like test_office's, with a few differences and a chosen modified time."""
    model = copy.deepcopy(TMSL)
    for name, expr in extra_measures:
        model["model"]["tables"][0]["measures"].append({"name": name, "expression": expr})
    lay = layout()
    if drop_page:
        lay["sections"] = []
    if rename_page:
        for sec in lay["sections"]:
            sec["displayName"] = rename_page
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Report/Layout", json.dumps(lay).encode("utf-16-le"))
        z.writestr("DataModelSchema", json.dumps(model).encode("utf-16-le"))
    if when:
        os.utime(path, (when, when))


def _reports(cat):
    return {r[0]: json.loads(r[1]) for r in cat.conn.execute("SELECT key, attrs FROM assets WHERE kind = 'report'")}


def test_copies_named_like_versions_are_grouped_with_the_newest_as_current(tmp_path):
    folder = tmp_path / "reports"
    write_pbit(folder / "Sales.pbit", when=1_700_000_000)
    write_pbit(folder / "Sales_v2.pbit", when=1_700_100_000, extra_measures=[("Avg", "AVERAGE(Sales[Units])")])
    write_pbit(folder / "Sales - Copy.pbit", when=1_699_000_000)
    write_pbit(folder / "Stock.pbit", when=1_700_000_000, rename_page="Stock", drop_page=False)
    cat = Catalogue(tmp_path / "c.db")
    scan_folder(cat, "reports", folder)
    find_versions(cat)
    [s] = suggestions.open_suggestions(cat, "versions")
    assert s["payload"]["main_key"] == "files://reports/Sales_v2.pbit"
    assert set(s["payload"]["members"]) == {"files://reports/Sales.pbit", "files://reports/Sales_v2.pbit",
                                            "files://reports/Sales - Copy.pbit"}
    assert "newest" in s["title"]
    suggestions.accept(cat, s["id"])
    assert groups.older_versions(cat) == {"files://reports/Sales.pbit", "files://reports/Sales - Copy.pbit"}


def test_same_report_under_another_name_is_matched_by_content(tmp_path):
    folder = tmp_path / "reports"
    write_pbit(folder / "Weekly sales.pbit", when=1_700_100_000)
    write_pbit(folder / "archive" / "sales report FINAL.pbit", when=1_700_200_000)  # newer, but archived
    cat = Catalogue(tmp_path / "c.db")
    scan_folder(cat, "reports", folder)
    reports = _reports(cat)
    a, b = reports["files://reports/Weekly sales.pbit"], reports["files://reports/archive/sales report FINAL.pbit"]
    assert similarity(a, b, "report") == 1.0
    found = find_report_versions(cat, set(), set())
    assert len(found) == 1 and "same pages, measures and sources" in found[0]["title"]
    assert found[0]["payload"]["main_key"] == "files://reports/Weekly sales.pbit"
    assert "archived" in found[0]["title"]


def test_thin_or_different_reports_are_not_matched(tmp_path):
    folder = tmp_path / "reports"
    write_pbit(folder / "Sales.pbit")
    write_pbit(folder / "Other.pbit", drop_page=True, extra_measures=[("A", "1"), ("B", "2"), ("C", "3"), ("D", "4")])
    cat = Catalogue(tmp_path / "c.db")
    scan_folder(cat, "reports", folder)
    assert find_report_versions(cat, set(), set()) == []


def test_diff_and_summary_between_two_versions(tmp_path):
    folder = tmp_path / "reports"
    write_pbit(folder / "a.pbit")
    write_pbit(folder / "b.pbit", extra_measures=[("Avg", "AVERAGE(Sales[Units])")], rename_page="Summary")
    cat = Catalogue(tmp_path / "c.db")
    scan_folder(cat, "reports", folder)
    r = _reports(cat)
    d = diff(r["files://reports/a.pbit"], r["files://reports/b.pbit"], "report")
    assert d["measures"]["added"] == ["Sales[Avg]"]
    assert d["pages"] == {"added": ["Summary"], "removed": ["Overview"], "changed": []}
    assert summary(d) == "+1 page, −1 page, +1 visual, −1 visual, +1 measure"


def test_rescans_keep_revisions_and_history(tmp_path):
    folder = tmp_path / "reports"
    write_pbit(folder / "Sales.pbit", when=1_700_000_000)
    cat = Catalogue(tmp_path / "c.db")
    scan_folder(cat, "reports", folder)
    scan_folder(cat, "reports", folder)  # unchanged: no new revision
    write_pbit(folder / "Sales.pbit", when=1_700_100_000, extra_measures=[("Avg", "AVERAGE(Sales[Units])")])
    scan_folder(cat, "reports", folder)
    revisions = _reports(cat)["files://reports/Sales.pbit"]["revisions"]
    assert len(revisions) == 2 and "summary" not in revisions[0]
    assert revisions[1]["summary"] == "+1 measure" and revisions[1]["counts"]["measures"] == 2
    from pdcat.history import history
    assert [(h["change"], h["detail"]) for h in history(cat, "files://reports/Sales.pbit")] == \
        [("measure added", "Sales[Avg]")]
    assert timeline(cat, "files://reports/Sales.pbit") == []  # alone: the revisions table covers it


def test_timeline_walks_the_versions_oldest_first(tmp_path):
    folder = tmp_path / "reports"
    write_pbit(folder / "Sales.pbit", when=1_700_000_000)
    write_pbit(folder / "Sales_v2.pbit", when=1_700_100_000, extra_measures=[("Avg", "AVERAGE(Sales[Units])")])
    cat = Catalogue(tmp_path / "c.db")
    scan_folder(cat, "reports", folder)
    find_versions(cat)
    [s] = suggestions.open_suggestions(cat, "versions")
    suggestions.accept(cat, s["id"])
    steps = timeline(cat, "files://reports/Sales_v2.pbit")
    assert [t["name"] for t in steps] == ["Sales.pbit", "Sales_v2.pbit"]
    assert steps[0]["summary"] == "first version" and steps[1]["summary"] == "+1 measure" and steps[1]["current"]
