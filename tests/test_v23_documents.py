"""Version 2.3: design documents read from folders (Word tables, PowerPoint, Excel), what they describe, and the
definitions in them offered as descriptions."""
from __future__ import annotations

import zipfile
from pathlib import Path

from pdcat import documents as D
from pdcat import suggestions
from pdcat.folderroles import save
from pdcat.scan_files import scan_folder

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"


def _docx(path: Path, paragraphs: list[str], table: list[list[str]] | None = None) -> None:
    def p(text: str) -> str:
        return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
    body = "".join(p(t) for t in paragraphs)
    if table:
        body += "<w:tbl>" + "".join(
            "<w:tr>" + "".join(f"<w:tc>{p(c)}</w:tc>" for c in row) + "</w:tr>" for row in table) + "</w:tbl>"
        body += p("After the table.")
    xml = f'<?xml version="1.0"?><w:document xmlns:w="w"><w:body>{body}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)


def _pptx(path: Path, slides: list[list[str]], notes: dict[int, str] | None = None) -> None:
    with zipfile.ZipFile(path, "w") as z:
        for i, paras in enumerate(slides, 1):
            body = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in paras)
            z.writestr(f"ppt/slides/slide{i}.xml", f'<p:sld xmlns:a="a" xmlns:p="p">{body}</p:sld>')
            if notes and i in notes:
                z.writestr(f"ppt/notesSlides/notesSlide{i}.xml",
                           f'<p:notes xmlns:a="a"><a:p><a:r><a:t>{notes[i]}</a:t></a:r></a:p><a:p><a:r><a:t>{i}</a:t></a:r></a:p></p:notes>')


def _xlsx(path: Path, sheets: dict[str, list[list[str]]]) -> None:
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with zipfile.ZipFile(path, "w") as z:
        names = list(sheets)
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}" xmlns:r="{rns}"><sheets>' + "".join(
            f'<sheet name="{n}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, n in enumerate(names)) + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i + 1}" Type="x" Target="worksheets/sheet{i + 1}.xml"/>'
                             for i in range(len(names))) + "</Relationships>")
        for i, n in enumerate(names):
            rows = "".join("<row>" + "".join(f'<c t="inlineStr"><is><t>{c}</t></is></c>' for c in row) + "</row>"
                           for row in sheets[n])
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", f'<worksheet xmlns="{ns}"><sheetData>{rows}</sheetData></worksheet>')


def test_word_tables_powerpoint_and_excel_read_as_text(tmp_path: Path):
    _docx(tmp_path / "d.docx", ["Design of the forecast model.", "It reads dbo.SalesDaily."],
          [["Field", "Meaning"], ["StoreId", "Identifier of the store in the ERP"], ["Units", "Units sold net of returns"]])
    text, note = D.extract_text("d.docx", (tmp_path / "d.docx").read_bytes())
    assert note is None and "Design of the forecast model." in text
    assert "StoreId\tIdentifier of the store in the ERP" in text and "After the table." in text
    _pptx(tmp_path / "p.pptx", [["Forecast model", "Inputs: dbo.SalesDaily"], ["Measures", "Sales[Net Units]"]],
          {2: "Explain the measure here"})
    text, note = D.extract_text("p.pptx", (tmp_path / "p.pptx").read_bytes())
    assert note is None and "--- Slide 1 ---" in text and "Sales[Net Units]" in text
    assert "Notes: Explain the measure here" in text
    _xlsx(tmp_path / "x.xlsx", {"Fields": [["Name", "Definition"], ["StoreId", "Identifier of the store"]],
                                "Other": [["a", "b"]]})
    text, note = D.extract_text("x.xlsx", (tmp_path / "x.xlsx").read_bytes())
    assert note is None and "--- Sheet Fields ---" in text and "StoreId\tIdentifier of the store" in text
    assert "--- Sheet Other ---" in text


def test_definitions_from_tables_and_lines():
    text = "Field\tMeaning\nStoreId\tIdentifier of the store\nPlantID: identifier of the plant in SAP\n" \
           "Region – sales region the store reports into\nName\tToo\nJust a sentence here: with a colon in it\n"
    pairs = dict(D.definitions(text))
    assert pairs["StoreId"] == "Identifier of the store"
    assert pairs["PlantID"] == "identifier of the plant in SAP"
    assert pairs["Region"] == "sales region the store reports into"
    assert "Name" not in pairs and "Just a sentence here" not in pairs


def test_design_folder_role_reads_documents_during_the_scan(catalogue, tmp_path: Path):
    root = tmp_path / "project"
    (root / "design").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "data" / "x.csv").write_text("a,b\n1,2\n")
    _docx(root / "design" / "model design.docx", ["Design of the forecast model. It reads dbo.SalesDaily."],
          [["Field", "Meaning"], ["StoreId", "Identifier of the store in the ERP"]])
    (root / "design" / "~$model design.docx").write_bytes(b"lock")
    (root / "other.docx").write_bytes(b"")
    catalogue.add_folder("project", str(root))
    save(catalogue, root, {"design": "design"}, confirmed=True, mode="single")
    counts = scan_folder(catalogue, "project", root)
    assert counts["documents"] == 1 and counts["files"] == 1
    docs = D.documents_in(catalogue, "project")
    assert [d["title"] for d in docs] == ["model design"] and docs[0]["owner"] == "files://project"
    # named in: the document shows on the table it names
    assert D.documents_mentioning(catalogue, SALES)[0]["title"] == "model design"
    # a changed file becomes a new version, an unchanged one is left alone
    scan_folder(catalogue, "project", root)
    assert D.get(catalogue, docs[0]["uid"])["versions"][0]["version"] == 1
    import os
    _docx(root / "design" / "model design.docx", ["Version two of the design."])
    os.utime(root / "design" / "model design.docx", (1_800_000_000, 1_800_000_000))
    scan_folder(catalogue, "project", root)
    d = D.get(catalogue, docs[0]["uid"])
    assert d["versions"][0]["version"] == 2


def test_definitions_become_suggested_descriptions_and_accept_writes_them(catalogue, tmp_path: Path):
    _docx(tmp_path / "d.docx", ["Fields"], [["Field", "Meaning"], ["StoreId", "Identifier of the store in the ERP"],
                                            ["Units", "Already described, so not offered"]])
    uid = D.add_document(catalogue, SALES, "Fields", "d.docx", (tmp_path / "d.docx").read_bytes())
    n = D.suggest_definitions(catalogue)
    assert n == 1
    s = suggestions.open_suggestions(catalogue, "doc_definition")[0]
    assert s["payload"]["column"] == "StoreId" and s["subject"] == SALES and "Fields" in s["detail"]
    from pdcat import describedrafts as DD
    drafts = {r["column"]: r for r in DD.column_suggestions(catalogue, SALES)}
    assert drafts["StoreId"]["text"] == "Identifier of the store in the ERP" and "Fields" in drafts["StoreId"]["source"]
    suggestions.accept(catalogue, s["id"])
    from pdcat.curation import column_description
    assert column_description(catalogue, SALES, "StoreId") == "Identifier of the store in the ERP"
    assert D.documents_describing(catalogue, SALES)[0]["uid"] == uid


def test_single_marked_file_and_describes(catalogue, tmp_path: Path):
    root = tmp_path / "folder"
    root.mkdir()
    _pptx(root / "deck.pptx", [["Forecast model design", "Owner: planning team"]])
    catalogue.add_folder("folder", str(root))
    scan_folder(catalogue, "folder", root)
    assert D.documents_in(catalogue, "folder") == []
    D.mark_design_file(catalogue, str(root / "deck.pptx"))
    assert D.document_files(catalogue, root) == [root / "deck.pptx"]
    counts = D.scan_folder_documents(catalogue, root, "folder", "files://folder")
    assert counts["new"] == 1
    d = D.documents_in(catalogue, "folder")[0]
    from pdcat.bprojects import save_project
    uid = save_project(catalogue, "Forecasting", purpose="Weekly forecast")
    D.set_describes(catalogue, d["uid"], f"bp://{uid}")
    assert [x["uid"] for x in D.documents_describing(catalogue, f"bp://{uid}")] == [d["uid"]]
    assert "Forecast model design" in (D.version(catalogue, d["uid"])["text"] or "")
    D.mark_design_file(catalogue, str(root / "deck.pptx"), marked=False)
    assert D.document_files(catalogue, root) == []


def test_measures_are_matched_by_their_bracket_names(catalogue):
    catalogue.upsert_asset("files://f/r.pbix", "report", "r.pbix", "f", attrs={"pages": [], "tables": [], "measures": []})
    catalogue.upsert_asset("files://f/r.pbix/measures/sales.net units", "measure", "Sales[Net Units]", "f",
                           parent="files://f/r.pbix", attrs={"table": "Sales", "name": "Net Units"})
    catalogue.conn.commit()
    found = D.mentions(catalogue, "The deck shows [Net Units] and Sales[Net Units] on page 2.")
    assert any(f["key"] == "files://f/r.pbix/measures/sales.net units" for f in found)
