"""Version 2.1: Copilot pack additions, test questions and the Copilot round trip."""
import io
import zipfile

from pdcat import aipack, curation, naming, suggestions
from pdcat import copilot_roundtrip as R

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"


def test_pack_has_the_new_files(catalogue):
    from pdcat.knowledge import save_metric
    db = "sql://warehouse/forecastdb"
    naming.add_rule(catalogue, db, "view", "prefix", "vw_", "Views start with vw_")
    col = catalogue.columns(SALES)[0]["name"]
    curation.set_column_extra(catalogue, SALES, col, "sap_name", "WERKS")
    save_metric(catalogue, "Forecast accuracy", definition="1 - WAPE")
    files = aipack.build(catalogue)
    assert "naming-rules.md" in files and "vw_" in files["naming-rules.md"]
    assert "sap.md" in files and "WERKS" in files["sap.md"]
    assert "test-questions.md" in files and "Forecast accuracy" in files["test-questions.md"]
    assert "reference-documents.md" not in files
    from pdcat.documents import add_document
    add_document(catalogue, "bp://x", "Design", "design.md", b"# Design\nUses dbo.SalesDaily.")
    assert "Uses dbo.SalesDaily" in aipack.build(catalogue, include_documents=True)["reference-documents.md"]


def test_test_questions_cover_different_things(catalogue):
    from pdcat.aipack import test_questions
    from pdcat.knowledge import save_metric
    save_metric(catalogue, "Fill rate", definition="Shipped over ordered")
    curation.set_item_info(catalogue, SALES, grain="store per day")
    qs = test_questions(catalogue)
    kinds = {q["about"] for q in qs}
    assert {"metric", "grain"} <= kinds
    assert all(q["answer"] for q in qs)


def test_round_trip(catalogue):
    items = R.gaps(catalogue)
    assert items and any(g["key"] == SALES for g in items)
    z = zipfile.ZipFile(io.BytesIO(R.gaps_zip(catalogue)))
    md = z.read("gaps-to-fill.md").decode()
    assert f"`{SALES}`" in md
    col = catalogue.columns(SALES)[0]["name"]
    rid = naming.add_rule(catalogue, "sql://warehouse/forecastdb", "table", "text", "", "Tables use proper case")
    reply = ("Here you go:\n```csv\nid,column,answer\n"
             f"{SALES},,Daily sales by store.\n{SALES},{col.lower()},The store's number.\n"
             "sql://nowhere/x,,Nope\n"
             f"rule:{rid},,Tables are PascalCase\n```")
    got = R.read_answers(catalogue, reply)
    assert got == {"added": 3, "unknown": 1}
    waiting = suggestions.open_suggestions(catalogue, "copilot")
    for s in waiting:
        suggestions.accept(catalogue, s["id"])
    assert curation.item_info(catalogue, SALES)["purpose"] == "Daily sales by store."
    assert curation.column_info(catalogue, SALES)[col]["description"] == "The store's number."
    rules = naming.rules(catalogue, "sql://warehouse/forecastdb")
    assert any(r["check_type"] == "case" for r in rules) and all(r["uid"] != rid for r in rules)
    assert not any(g["key"] == SALES and g["needs_item"] for g in R.gaps(catalogue))


def test_questions_for_colleagues_round_trip(catalogue, tmp_path):
    import json

    from pdcat import sharing as S
    from pdcat.knowledge import list_questions, save_question
    from pdcat.store import Catalogue
    save_question(catalogue, "Which table has returns?")
    save_question(catalogue, "Weekly sales?", answer="Use dbo.SalesDaily", sql="SELECT 1")
    pack = S.questions_share(catalogue)
    assert [q["question"] for q in pack["questions"]] == ["Which table has returns?"]
    colleague = Catalogue(tmp_path / "theirs.db")
    colleague.set_setting("author", "colleague")
    S.merge(colleague, S.read_share(json.dumps(pack, default=str)), {})
    [q] = list_questions(colleague)
    assert [x["uid"] for x in S.asked_of_me(colleague)] == [q["uid"]]
    colleague.conn.execute("UPDATE questions SET answer = 'dbo.Returns' WHERE uid = ?", (q["uid"],))
    colleague.conn.commit()
    answers = S.answers_share(colleague)
    assert len(answers["questions"]) == 1
    S.merge(catalogue, S.read_share(json.dumps(answers, default=str)), {})
    mine = {x["question"]: x for x in list_questions(catalogue)}
    assert mine["Which table has returns?"]["answer"] == "dbo.Returns"
    assert mine["Weekly sales?"]["answer"] == "Use dbo.SalesDaily"
