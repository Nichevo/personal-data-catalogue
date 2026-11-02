"""Version 2.5: likely / check / unlikely bands and deciding long suggestion lists in one go."""
from pathlib import Path

import pytest

from pdcat import suggestions
from pdcat.triage import key_column_bands, triage

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).parents[1] / "src" / "pdcat" / "app.py")
SALES = "sql://warehouse/forecastdb/dbo.salesdaily"
STORE = "sql://warehouse/forecastdb/dbo.store"


def _sap(i: int, col: str, field: str, how: str, key: str = SALES) -> dict:
    return {"id": f"sap:{i}", "subject": key, "title": f"x.{col} is SAP {field}", "score": 2,
            "detail": f"Suggested because the column name is {how}.",
            "payload": {"key": key, "column": col, "sap_name": field}}


def test_sap_bands_from_match_strength_and_your_past_answers(catalogue):
    suggestions.sync(catalogue, "sap", [
        _sap(1, "WERKS", "WERKS", "the SAP field name"),
        _sap(2, "Plant_Name", "WERKS", "a usual English name for it"),
        _sap(3, "Plant_Name", "WERKS", "a usual English name for it", key=STORE),
        _sap(4, "MARC_MATNR", "MATNR", "contains the SAP field name"),
    ])
    rows = {r["id"]: r for r in triage(catalogue, "sap", suggestions.open_suggestions(catalogue, "sap"))}
    assert rows["sap:1"]["band"] == "likely"
    assert rows["sap:2"]["band"] == "check"
    assert rows["sap:1"]["group"] == "SAP WERKS"
    suggestions.reject(catalogue, "sap:2")  # no, Plant_Name isn't the plant code
    rows = {r["id"]: r for r in triage(catalogue, "sap", suggestions.open_suggestions(catalogue, "sap"))}
    assert rows["sap:3"]["band"] == "unlikely"
    assert "rejected" in rows["sap:3"]["why"]
    assert list(rows)[0] == "sap:1"  # likely first


def test_metric_bands():
    rows = [
        {"id": "m1", "title": "Forecast accuracy", "score": 3, "payload": {
            "name": "Forecast accuracy", "formula": "1 - abs(a-f)/a", "places": ["a", "b", "c"]}},
        {"id": "m2", "title": "Col1", "score": 1, "payload": {"name": "Col1", "formula": "a/b", "places": ["a"]}},
        {"id": "m3", "title": "Fill rate", "score": 1, "payload": {"name": "Fill rate", "formula": "x/y",
                                                                   "places": ["SQL “q”"]}},
    ]

    class NoHistory:
        class conn:
            @staticmethod
            def execute(*a):
                return []

    out = {r["id"]: r for r in triage(NoHistory, "metric", rows)}
    assert out["m1"]["band"] == "likely"
    assert out["m2"]["band"] == "unlikely"
    assert out["m3"]["band"] == "check" and out["m3"]["group"] == "Computed SQL columns"


def test_key_columns_banded_by_data_type():
    confirmed = [{"data_type": "int"}, {"data_type": "int"}]
    sugg = [{"key_name": "PlantID", "data_type": "int"}, {"key_name": "PlantID", "data_type": "bigint"},
            {"key_name": "PlantID", "data_type": "datetime"}]
    assert [b for b, _, _ in key_column_bands(confirmed, sugg)] == ["likely", "check", "unlikely"]


@pytest.fixture
def app(catalogue, monkeypatch):
    monkeypatch.setenv("PDCAT_DB", str(catalogue.path))
    return AppTest.from_file(APP, default_timeout=15)


def test_accept_all_except_ticked(app, catalogue):
    from tests.test_app import goto
    suggestions.sync(catalogue, "sap", [_sap(i, f"WERKS_{i}", "WERKS", "contains the SAP field name")
                                        for i in range(1, 61)])
    app.run()
    goto(app, "Suggestions")
    assert not app.exception
    app.checkbox(key="sp-sap-t-sap:3").check().run()
    [b for b in app.button if b.label.startswith("Yes, accept 59")][0].click().run()
    assert not app.exception
    left = suggestions.open_suggestions(catalogue, "sap")
    assert [s["id"] for s in left] == ["sap:3"]


def test_key_page_confirms_columns_in_bulk(app, catalogue):
    from pdcat import keys as K
    catalogue.conn.execute("INSERT INTO columns (asset_key, name, ordinal, data_type) VALUES (?, 'StoreID', 0, 'int')",
                           (STORE,))
    catalogue.conn.commit()
    name = K.mark_key(catalogue, "StoreId", STORE, "StoreID")
    assert K.key_columns(catalogue, name, "suggested")
    app.query_params["asset"] = f"key://{name}"
    app.run()
    assert not app.exception
    [b for b in app.button if b.label.startswith("Yes, confirm")][0].click().run()
    assert not app.exception
    assert not K.key_columns(catalogue, name, "suggested")
