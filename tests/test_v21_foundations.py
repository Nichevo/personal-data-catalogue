"""Version 2.1 groundwork: suggestions, groups, edit history with undo, link marks, not relevant, SQL reading."""
from pdcat import curation, edits, groups, links, sqlparser, suggestions

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"
STORE = "sql://warehouse/forecastdb/dbo.store"


def script_key(cat):
    return next(r[0] for r in cat.conn.execute("SELECT key FROM assets WHERE kind = 'script'"))


def test_a_decided_suggestion_stays_decided(catalogue):
    found = [{"id": "family:soh", "subject": SALES, "title": "SOH family",
              "payload": {"type": "family", "name": "SOH", "members": {SALES: "daily", STORE: "weekly"}}}]
    assert suggestions.sync(catalogue, "family", found) == 1
    suggestions.reject(catalogue, "family:soh")
    suggestions.sync(catalogue, "family", found)
    assert suggestions.open_suggestions(catalogue, "family") == []
    assert suggestions.state(catalogue, "family:soh") == "rejected"


def test_accepting_a_suggestion_runs_its_handler(catalogue):
    suggestions.sync(catalogue, "family", [{"id": "family:soh", "subject": SALES, "title": "SOH family", "payload": {
        "type": "family", "name": "SOH", "members": {SALES: "daily", STORE: "weekly"}}}])
    suggestions.accept(catalogue, "family:soh")
    g = groups.for_item(catalogue, STORE)
    assert len(g) == 1 and g[0]["name"] == "SOH" and {m["label"] for m in g[0]["members"]} == {"daily", "weekly"}


def test_suggestions_no_longer_found_are_gone_and_come_back(catalogue):
    one = {"id": "metric:wape", "subject": "x", "title": "WAPE"}
    suggestions.sync(catalogue, "metric", [one])
    suggestions.sync(catalogue, "metric", [])
    assert suggestions.state(catalogue, "metric:wape") == "gone"
    suggestions.sync(catalogue, "metric", [one])
    assert suggestions.state(catalogue, "metric:wape") == "open"


def test_versions_hide_older_scripts_from_search(catalogue, tmp_path):
    a = script_key(catalogue)
    catalogue.upsert_asset(a + "_v1", "script", "features_v1.py", "forecasting", attrs={})
    catalogue.rebuild_search()
    assert any(r["key"] == a + "_v1" for r in catalogue.search("features"))
    uid = groups.create(catalogue, "versions", "features.py", {a: "current", a + "_v1": "v1"}, main_key=a)
    assert groups.older_versions(catalogue) == {a + "_v1"}
    assert not any(r["key"] == a + "_v1" for r in catalogue.search("features"))
    assert any(r["key"] == a + "_v1" for r in catalogue.search("features", include_hidden=True))
    groups.remove_member(catalogue, uid, a + "_v1")
    assert groups.get(catalogue, uid) is None  # a group of one is no group


def test_not_relevant_items_leave_search_and_review(catalogue):
    from pdcat.review import open_tasks, refresh_tasks
    refresh_tasks(catalogue)
    assert any(t["asset_key"] == STORE for t in open_tasks(catalogue, limit=1000))
    curation.set_relevance(catalogue, STORE, "not relevant")
    refresh_tasks(catalogue)
    assert not any(t["asset_key"] == STORE for t in open_tasks(catalogue, limit=1000))
    assert not any(r["key"] == STORE for r in catalogue.search("store"))
    curation.set_relevance(catalogue, STORE, "")
    assert any(r["key"] == STORE for r in catalogue.search("store"))


def test_edit_history_undoes_descriptions_and_notes(catalogue):
    curation.set_item_info(catalogue, SALES, purpose="daily sales")
    curation.set_item_info(catalogue, SALES, purpose="daily sales per store")
    last = edits.history(catalogue, SALES)[0]
    assert last["old_value"] == "daily sales" and last["new_value"] == "daily sales per store"
    edits.undo(catalogue, last["id"])
    assert curation.item_info(catalogue, SALES)["purpose"] == "daily sales"

    curation.set_column_info(catalogue, SALES, "Units", description="units sold")
    edits.undo(catalogue, edits.history(catalogue, SALES)[0]["id"])
    assert curation.column_info(catalogue, SALES)["Units"]["description"] is None

    nid = catalogue.add_note(SALES, "first")
    catalogue.update_note(nid, "second")
    edits.undo(catalogue, edits.history(catalogue, SALES)[0]["id"])
    assert catalogue.conn.execute("SELECT body FROM notes WHERE id = ?", (nid,)).fetchone()[0] == "first"
    catalogue.delete_note(nid)
    edits.undo(catalogue, edits.history(catalogue, SALES)[0]["id"])
    assert [n["body"] for n in catalogue.notes(SALES)] == ["first"]


def test_unimportant_links_are_marked_and_undoable(catalogue):
    src = script_key(catalogue)
    links.set_link_mark(catalogue, src, "reads", STORE, "unimportant")
    _, _, read_by = links.links_for(catalogue, STORE, catalogue.get_asset(STORE))
    assert read_by and all(l["unimportant"] for l in read_by if l["src_key"] == src)
    assert any(r["change"] == "not important" for r in links.changed_links(catalogue))
    edits.undo(catalogue, edits.history(catalogue, STORE)[0]["id"])
    assert links.unimportant(catalogue) == set()


def test_removed_links_stay_removed_and_undo(catalogue):
    src = script_key(catalogue)
    links.hide_link(catalogue, src, "reads", STORE)
    assert not any(l["src_key"] == src for l in catalogue.links_to(STORE))
    edits.undo(catalogue, edits.history(catalogue, STORE)[0]["id"])
    assert links.changed_links(catalogue) == []


def test_sql_reading_finds_join_fields_with_or_without_sqlglot(monkeypatch):
    sql = ("SELECT s.Units AS qty, st.Region FROM dbo.SalesDaily s JOIN dbo.Store st ON st.StoreId = s.StoreId "
           "WHERE s.Units > 0")
    for parse in (sqlparser.analyse, sqlparser._with_regex):
        out = parse(sql)
        assert {t["name"] for t in out["tables"]} == {"dbo.salesdaily", "dbo.store"}
        j = out["joins"][0]
        assert {j["left_column"], j["right_column"]} == {"StoreId"}
        assert out["renames"][0]["alias"] == "qty"
        assert "Region" in out["columns"]["dbo.store"]


def test_sql_reading_never_raises():
    for sql in ("", "SELECT", "this is not sql", "EXEC dbo.usp_x @a = 1", "SELECT * FROM [weird table] t"):
        out = sqlparser.analyse(sql)
        assert "tables" in out
