"""Version 2.1: reference documents on business projects, whole folders, and starting a project from an item."""
import io
import zipfile
from pathlib import Path

import pytest

from pdcat import bprojects as B
from pdcat import documents as D

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"


def docx(paragraphs: list[str]) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p.replace('&', '&amp;').replace('<', '&lt;')}</w:t></w:r></w:p>"
                   for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="x"><w:body>'
                                        f"{body}</w:body></w:document>")
    return buf.getvalue()


def test_documents_keep_versions_and_link_items(catalogue):
    uid_p = B.save_project(catalogue, "Demand forecasting")
    owner = f"bp://{uid_p}"
    data = docx(["Forecast design", "Reads dbo.SalesDaily every Monday & writes ml.ForecastOutput.",
                 "SELECT StoreId, SUM(Units) AS units FROM dbo.SalesDaily GROUP BY StoreId"])
    uid = D.add_document(catalogue, owner, "Design", "design.docx", data)
    v1 = D.version(catalogue, uid)
    assert "every Monday & writes" in v1["text"]
    names = {f["key"] for f in D.mentions(catalogue, v1["text"])}
    assert SALES in names
    assert [d["uid"] for d in D.documents_mentioning(catalogue, SALES)] == [uid]
    D.add_version(catalogue, uid, "design-v2.md", b"# Design v2\nNow reads dbo.Store only.")
    d = D.get(catalogue, uid)
    assert [v["version"] for v in d["versions"]] == [2, 1]
    assert D.documents_mentioning(catalogue, SALES) == []  # only the latest version counts
    assert any(r["key"] == f"doc://{uid}" for r in catalogue.search("design v2"))
    assert "**dbo.Store**" in D.highlight("Now reads dbo.Store only.", ["dbo.store"])


def test_sql_in_documents_goes_in_the_library(catalogue):
    from pdcat import sqllibrary
    uid = D.add_document(catalogue, "bp://x", "Notes", "notes.md",
                         b"Some text.\n\n```sql\nSELECT StoreId FROM dbo.Store\n```\n\nSELECT * FROM dbo.SalesDaily\n")
    sqllibrary.build_library(catalogue)
    rows = sqllibrary.queries(catalogue, item=f"doc://{uid}")
    assert len(rows) == 2


def test_pdf_without_pypdf_is_kept_with_a_note(monkeypatch):
    import builtins
    real = builtins.__import__

    def no_pypdf(name, *a, **kw):
        if name == "pypdf":
            raise ImportError
        return real(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", no_pypdf)
    text, note = D.extract_text("x.pdf", b"%PDF-1.4")
    assert text == "" and "pypdf" in note


def test_whole_folders_and_following_new_files(catalogue, tmp_path: Path):
    from pdcat.config import PythonSource
    from pdcat.scan_python import scan_project
    root = tmp_path / "fc"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n")
    (root / "b.py").write_text("x = 1\n")
    scan_project(catalogue, PythonSource(name="fc", path=root))
    assert "py://fc/src" in B.folder_choices(catalogue)
    uid = B.save_project(catalogue, "Forecasting")
    assert B.add_folder(catalogue, uid, "py://fc/src") == 1
    (root / "src" / "c.py").write_text("x = 2\n")
    scan_project(catalogue, PythonSource(name="fc", path=root))
    B.remove_member(catalogue, uid, "py://fc/src/a.py")
    assert B.sync_folders(catalogue) == 1
    assert {m["key"] for m in B.members(catalogue, uid)} == {"py://fc/src/c.py"}  # removed ones stay out


def test_item_page_starts_a_business_project(catalogue, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("PDCAT_DB", str(catalogue.path))
    app = AppTest.from_file(str(Path(__file__).parents[1] / "src" / "pdcat" / "app.py"), default_timeout=15)
    app.query_params["asset"] = SALES
    app.run()
    assert not app.exception
    form_inputs = [t for t in app.text_input if t.label == "Business project name"]
    form_inputs[0].input("Store sales")
    [b for b in app.button if b.label == "Start it with this item"][0].click().run()
    assert not app.exception
    [p] = [p for p in B.list_projects(catalogue) if p["name"] == "Store sales"]
    assert [m["key"] for m in B.members(catalogue, p["uid"])] == [SALES]
    app.run()
    assert not app.exception


def test_document_page(catalogue, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    uid_p = B.save_project(catalogue, "Demand forecasting")
    uid = D.add_document(catalogue, f"bp://{uid_p}", "Design", "design.md", b"Reads dbo.SalesDaily.")
    monkeypatch.setenv("PDCAT_DB", str(catalogue.path))
    app = AppTest.from_file(str(Path(__file__).parents[1] / "src" / "pdcat" / "app.py"), default_timeout=15)
    app.query_params["asset"] = f"doc://{uid}"
    app.run()
    assert not app.exception
    assert app.title[0].value.endswith("Design")
    assert any("**dbo.SalesDaily**" in m.value for m in app.markdown)
    app.query_params["asset"] = f"bp://{uid_p}"
    app.run()
    assert not app.exception
    assert any(b.label == "📄 Design" for b in app.button)
