"""Version 2.20: the Copilot pack builds when the SQL query library holds tables as nested objects."""
import io
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from pdcat import aipack, sqllibrary
from pdcat.config import PythonSource
from pdcat.scan_python import scan_project
from pdcat.store import Catalogue
from tests.test_v21_sql import SALES, STORES, _project, cat  # noqa: F401


def test_copilot_pack_lists_sql_library_tables_by_name(cat, tmp_path: Path):  # noqa: F811
    scan_project(cat, PythonSource(name="proj", path=str(_project(tmp_path))))
    assert sqllibrary.build_library(cat)
    # an unmatched table (no key) and odd values must not stop the export either
    uid = sqllibrary.queries(cat)[0]["uid"]
    extra = [{"name": "dbo.Unknown", "key": None, "relation": "reads", "columns": []}, {"name": None}, "dbo.Plain"]
    tables = json.loads(cat.conn.execute("SELECT tables FROM sql_queries WHERE uid = ?", (uid,)).fetchone()[0])
    cat.conn.execute("UPDATE sql_queries SET tables = ? WHERE uid = ?", (json.dumps(tables + extra), uid))
    cat.conn.commit()

    files = aipack.build(cat)
    text = files["sql-queries.md"]
    assert "Uses: " in text and "dbo.Sales" in text and "dbo.Unknown" in text and "dbo.Plain" in text
    assert "{'name'" not in text and "None" not in text
    for fmt in ("md", "docx"):
        with zipfile.ZipFile(io.BytesIO(aipack.to_zip(cat, fmt=fmt))) as z:
            assert z.namelist()


def test_pack_name_copes_with_any_value(tmp_path: Path):
    c = Catalogue(tmp_path / "c.db")
    c.upsert_asset(STORES, "table", "dbo.Stores", "dw", attrs={"schema": "dbo"})
    c.conn.commit()
    p = aipack.Pack(c)
    assert p.name({"name": "x", "key": STORES}) == p.name(STORES) == "salesdb.dbo.Stores"
    assert p.name({"name": "dbo.Other", "key": None}) == "dbo.Other"
    assert p.name(None) == "" and p.name(3) == "3"
    c.close()


def test_messy_items_never_stop_the_pack(tmp_path: Path, monkeypatch):
    c = Catalogue(tmp_path / "c.db")
    c.upsert_asset("file://x/a.csv", "data_file", "a.csv", "x", attrs={"row_count": "about 2m", "path": "x/a.csv"})
    c.upsert_asset("pbi://r", "report", "Sales\x0b report", "pbi", attrs={
        "path": "r.pbix", "pages": [{"name": "P1", "visuals": [{"title": None, "type": None}, {"type": "card"}]},
                                    {"name": None}, "odd"]})
    c.upsert_asset("pbi://r/m", "measure", "Units", "pbi", parent="pbi://r",
                   attrs={"expression": {"dax": "SUM(x)"}, "used_on": ["P1", {"page": "P2"}]})
    c.conn.commit()
    files = aipack.build(c)
    assert "about 2m rows" in "".join(files.values())
    assert "P1 (visual, card)" in files["reports.md"]
    with zipfile.ZipFile(io.BytesIO(aipack.to_zip(c, fmt="docx"))) as z:
        with zipfile.ZipFile(io.BytesIO(z.read("reports.docx"))) as d:
            xml = d.read("word/document.xml").decode("utf-8")
    assert "Sales report" in xml and "\x0b" not in xml
    ElementTree.fromstring(xml)  # Word opens it only when it is valid XML

    # a part that fails is left out and listed in start-here, and the rest of the pack is still made
    def broken(p):
        raise ValueError("bad item")
    monkeypatch.setattr(aipack, "glossary", broken)
    files = aipack.build(c)
    assert "glossary.md" not in files and "reports.md" in files
    assert "Left out of this pack" in files["00-start-here.md"] and "bad item" in files["00-start-here.md"]
    c.close()
