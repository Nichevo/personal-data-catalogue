"""Sharing and merging, the Copilot pack, and the OpenMetadata export."""
import io
import json
import zipfile

from pdcat import aipack, curation, openmeta
from pdcat import sharing as S
from pdcat.knowledge import save_knowledge, save_metric, save_question
from pdcat.store import Catalogue

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"
STORE = "sql://warehouse/forecastdb/dbo.store"


def _colleague(tmp_path, catalogue):
    """Sam's catalogue: the same server under another source name, with their own notes."""
    from conftest import FAKE_DB

    from pdcat.config import SqlServerSource
    from pdcat.scan_sqlserver import ingest
    sam = Catalogue(tmp_path / "sam.db")
    sam.set_setting("author", "Sam")
    src = SqlServerSource(name="dw", server="TEST-SERVER", databases=["ForecastDB"])
    with sam.scan("dw"):
        ingest(sam, src, "ForecastDB", FAKE_DB)
    key = "sql://dw/forecastdb/dbo.salesdaily"
    sam.add_note(key, "Mondays arrive late.")
    curation.set_item_info(sam, key, purpose="Their purpose", owner="Sam")
    curation.set_column_info(sam, "sql://dw/forecastdb/dbo.store", "Region", description="Sales region")
    save_knowledge(sam, "Freight is always sea freight", "Air freight is in another system.", triggers="freight")
    return sam


def test_share_preview_merge_and_undo(catalogue, tmp_path):
    curation.set_item_info(catalogue, SALES, purpose="My purpose")
    sam = _colleague(tmp_path, catalogue)
    pack = S.read_share(S.to_share_json(sam))
    mapping = S.suggest_mapping(catalogue, pack)
    assert mapping == {"dw": "warehouse"}  # same server and database
    p = S.preview(catalogue, pack, mapping)
    assert p["sections"]["notes"]["new"] == 1 and p["sections"]["knowledge"]["new"] == 1
    assert p["sections"]["item_info"]["different"] == 1
    conflict = p["conflicts"][0]
    assert conflict["fields"][0] == {"field": "purpose", "mine": "My purpose", "theirs": "Their purpose"}
    result = S.merge(catalogue, pack, mapping, file_name="sam.json")
    note = [n for n in catalogue.notes(SALES)][0]
    assert note["body"] == "Mondays arrive late." and note["author"] == "Sam"
    info = curation.item_info(catalogue, SALES)
    assert info["purpose"] == "My purpose" and info["owner"] == "Sam"  # mine kept, gap filled
    assert curation.column_description(catalogue, STORE, "Region") == "Sales region"
    # Merging again changes nothing.
    again = S.merge(catalogue, pack, mapping)
    assert sum(again["counts"].values()) == 0
    S.undo(catalogue, again["batch"])
    S.undo(catalogue, result["batch"])
    assert not catalogue.notes(SALES)
    assert curation.item_info(catalogue, SALES)["owner"] is None
    assert curation.item_info(catalogue, SALES)["purpose"] == "My purpose"


def test_take_theirs_and_imported_scans(catalogue, tmp_path):
    curation.set_item_info(catalogue, SALES, purpose="My purpose")
    sam = _colleague(tmp_path, catalogue)
    sam.upsert_asset("sql://dw/forecastdb/dbo.extra", "table", "dbo.Extra", "dw", attrs={"object": "Extra"})
    sam.conn.commit()
    pack = S.make_share(sam, scans=["dw"])
    mapping = {"dw": "warehouse"}
    S.merge(catalogue, json.loads(json.dumps(pack, default=str)), mapping, take_theirs=True)
    assert curation.item_info(catalogue, SALES)["purpose"] == "Their purpose"
    extra = catalogue.get_asset("sql://warehouse/forecastdb/dbo.extra")
    assert extra["attrs"]["imported_from"] == "Sam"
    assert catalogue.get_asset(SALES)["attrs"].get("imported_from") is None  # I scanned it: mine kept


def test_sensitive_values_stay_out_of_share_files(catalogue):
    catalogue.save_profile(STORE, [{"name": "Region", "ordinal": 1, "data_type": "nvarchar", "row_count": 2,
                                    "null_count": 0, "distinct_count": 2, "min": "N", "max": "S",
                                    "top_values": [["N", 1]]}], "sample")
    curation.set_column_info(catalogue, STORE, "Region", sensitive=True)
    pack = S.make_share(catalogue, scans=["warehouse"])
    prof = [p for p in pack["scans"]["warehouse"]["profiles"] if p["column_name"] == "Region"]
    assert all(p["min_value"] is None and p["top_values"] in ("[]", None) for p in prof)


def test_copilot_pack(catalogue):
    curation.set_item_info(catalogue, SALES, purpose="Daily sales by store", grain="store per day",
                           lifecycle="in use")
    curation.set_item_info(catalogue, STORE, lifecycle="deprecated", replaced_by="dbo.StoreMaster")
    save_knowledge(catalogue, "Freight is always sea freight", "Air freight is elsewhere.", triggers="freight")
    save_metric(catalogue, "Forecast accuracy", definition="1 - WAPE", sources=[SALES])
    save_question(catalogue, "Weekly sales by region?", sql="SELECT 1 FROM dbo.SalesDaily")
    files = aipack.build(catalogue)
    assert {"00-start-here.md", "business-rules.md", "glossary.md", "example-questions.md",
            "agent-instructions.md"} <= set(files)
    data = files["data/warehouse-forecastdb.md"]
    assert "Daily sales by store" in data and "One row per: store per day" in data
    assert "DEPRECATED**: use dbo.StoreMaster instead" in data
    assert "TEST-SERVER" in files["00-start-here.md"]
    hidden = aipack.build(catalogue, hide_locations=True)
    assert not any("TEST-SERVER" in t for t in hidden.values())
    assert "Weekly sales by region?" in files["agent-instructions.md"]
    z = zipfile.ZipFile(io.BytesIO(aipack.to_zip(catalogue, fmt="docx")))
    assert "glossary.docx" in z.namelist()
    with zipfile.ZipFile(io.BytesIO(z.read("glossary.docx"))) as doc:
        assert "Forecast accuracy" in doc.read("word/document.xml").decode()


def test_openmetadata_export(catalogue):
    curation.set_column_info(catalogue, STORE, "Region", description="Sales region")
    ents = openmeta.entities(catalogue)
    kinds = [e["type"] for e in ents]
    assert kinds[:3] == ["databaseService", "database", "databaseSchema"]
    store = next(e["body"] for e in ents if e["type"] == "table" and e["body"]["name"] == "Store")
    assert store["databaseSchema"] == "warehouse.ForecastDB.dbo"
    assert store["columns"][0] == {"name": "Region", "dataType": "VARCHAR", "dataTypeDisplay": "nvarchar(50)",
                                   "dataLength": 50, "description": "Sales region"}
    # The procedure reads SalesDaily and writes ForecastOutput.
    assert any(e["type"] == "lineage" and e["body"]["from"].endswith("SalesDaily") for e in ents)
    assert openmeta.column_type("varchar(max)")["dataType"] == "TEXT"
    z = zipfile.ZipFile(io.BytesIO(openmeta.to_zip(catalogue)))
    assert set(z.namelist()) == {"entities.json", "load_openmetadata.py", "README.txt"}
