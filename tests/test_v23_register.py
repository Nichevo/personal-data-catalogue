"""Version 2.3: the Power BI report register, brought in as a data file in a scanned folder."""
from __future__ import annotations

from pathlib import Path

from pdcat import register as R
from pdcat.curation import item_info, set_item_info
from pdcat.scan_files import scan_folder

CSV = ("uniqueID,name,description,owner,audience,tags,statusName,powerBIAppID,webUrl,dataset,datasetId,"
       "publishedDateTime,modifiedDateTime\n"
       "r1,Stock on hand,Daily stock by plant,Planning team,Planners,stock;soh,Live,Supply app,"
       "https://example.test/r1,Supply model,ds-1,2026-01-05T10:00:00,2026-03-01T09:30:00\n"
       "r2,Supply model,The shared semantic model,Data team,Everyone,,Live,,,Supply model,ds-1,"
       "2025-12-01T10:00:00,2026-02-01T09:30:00\n"
       "r3,Forecast accuracy v2,,Forecast team,Managers,forecast,Draft,,,Forecast model,ds-2,,\n"
       "r4,Nothing scanned,,Someone,,,Retired,,,,,,\n")


def _reports(cat):
    cat.upsert_asset("files://reports/Stock on hand.pbix", "report", "Stock on hand.pbix", "reports",
                     attrs={"pages": [], "tables": [], "measures": [], "live": {"dataset": "Supply model"}})
    cat.upsert_asset("files://reports/Supply model.pbix", "report", "Supply model.pbix", "reports",
                     attrs={"pages": [], "tables": [{"name": "Stock", "columns": []}], "measures": []})
    cat.upsert_asset("files://reports/Forecast accuracy.pbix", "report", "Forecast accuracy.pbix", "reports",
                     attrs={"pages": [], "tables": [], "measures": []})
    cat.conn.commit()


def test_mapping_is_guessed_from_the_column_names():
    m = R.guess_mapping(["uniqueID", "name", "Owner", "statusName", "powerBIAppID", "webUrl", "datasetId", "extra"])
    assert m["uid"] == "uniqueID" and m["owner"] == "Owner" and m["status"] == "statusName"
    assert m["app"] == "powerBIAppID" and m["url"] == "webUrl" and m["dataset_id"] == "datasetId"
    assert "extra" not in m.values()


def test_register_rows_become_items_matched_to_reports(catalogue, tmp_path: Path):
    _reports(catalogue)
    root = tmp_path / "registry"
    root.mkdir()
    (root / "reports.csv").write_text(CSV)
    catalogue.add_folder("registry", str(root))
    scan_folder(catalogue, "registry", root)
    key = "files://registry/reports.csv"
    assert catalogue.get_asset(key)
    assert "owner" in R.guess_mapping([c["name"] for c in catalogue.columns(key)])
    R.set_register(catalogue, key)
    assert R.refresh(catalogue) == 4
    by = {e["uid"]: e for e in R.entries(catalogue)}
    assert by["r1"]["report"] == "files://reports/Stock on hand.pbix" and by["r1"]["how"] == "name"
    assert by["r3"]["report"] == "files://reports/Forecast accuracy.pbix" and by["r3"]["how"] == "words"
    assert by["r4"]["report"] is None and [e["uid"] for e in R.unmatched(catalogue)] == ["r4"]
    # the report got the owner and description where it had none
    info = item_info(catalogue, "files://reports/Stock on hand.pbix")
    assert info["owner"] == "Planning team" and info["purpose"] == "Daily stock by plant"
    # the context strip, what summaries read, and the dataset → model map for thin reports
    assert ("Owner", "Planning team") in R.strip_line(R.for_report(catalogue, "files://reports/Stock on hand.pbix"))
    assert R.rows_by_report(catalogue)["files://reports/Supply model.pbix"]["owner"] == "Data team"
    assert R.dataset_models(catalogue) == {"supply model": "files://reports/Supply model.pbix",
                                           "ds-1": "files://reports/Supply model.pbix"}
    # searchable by owner, audience and status
    assert any(r["key"] == "reg://r3" for r in catalogue.search("Managers"))
    assert any(r["key"] == "reg://r1" for r in catalogue.search("Planning team"))
    # a folder rescan leaves the rows alone (they belong to the register, not the folder)
    scan_folder(catalogue, "registry", root)
    assert catalogue.get_asset("reg://r1")["status"] == "active"


def test_your_own_owner_is_kept_and_matches_can_be_changed_by_hand(catalogue, tmp_path: Path):
    _reports(catalogue)
    (tmp_path / "reports.csv").write_text(CSV)
    catalogue.upsert_asset("files://x/reports.csv", "data_file", "reports.csv", "x", attrs={"path": str(tmp_path / "reports.csv")})
    catalogue.set_columns("files://x/reports.csv", [{"name": n, "ordinal": i, "data_type": "text"}
                                                    for i, n in enumerate(CSV.splitlines()[0].split(","))])
    catalogue.conn.commit()
    set_item_info(catalogue, "files://reports/Stock on hand.pbix", owner="Me")
    R.set_register(catalogue, "files://x/reports.csv")
    R.refresh(catalogue)
    assert item_info(catalogue, "files://reports/Stock on hand.pbix")["owner"] == "Me"
    # the register's own value is replaced when the register changes it, but yours never is
    (tmp_path / "reports.csv").write_text(CSV.replace("Data team", "New data team"))
    R.refresh(catalogue)
    assert item_info(catalogue, "files://reports/Supply model.pbix")["owner"] == "New data team"
    R.set_match(catalogue, "reg://r4", "files://reports/Forecast accuracy.pbix")
    e = R.entry(catalogue, "reg://r4")
    assert e["report"] == "files://reports/Forecast accuracy.pbix" and e["how"] == "manual"
    R.set_match(catalogue, "reg://r1", None)
    assert R.entry(catalogue, "reg://r1")["report"] is None
    R.forget_match(catalogue, "reg://r1")
    assert R.entry(catalogue, "reg://r1")["report"] == "files://reports/Stock on hand.pbix"
    # a row that leaves the register is marked missing
    (tmp_path / "reports.csv").write_text("\n".join(CSV.splitlines()[:3]) + "\n")
    R.refresh(catalogue)
    assert catalogue.get_asset("reg://r4")["status"] == "missing"
    # stopping clears everything on the next refresh
    R.set_register(catalogue, None)
    assert R.refresh(catalogue) == 0 and R.entries(catalogue) == []
    facets = R.facets(catalogue)
    assert facets["owner"] == []
