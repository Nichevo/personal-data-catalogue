"""2.5.4: table description suggestions built from the names a table is given elsewhere."""
from __future__ import annotations

from pathlib import Path

from conftest import FAKE_DB, SRC

from pdcat import describedrafts as DD
from pdcat.config import PythonSource
from pdcat.scan_python import scan_project
from pdcat.scan_sqlserver import ingest

KEY = "sql://warehouse/forecastdb/dbo.t_mat01"


def _with_table(cat):
    objs = [{"schema_name": "dbo", "object_name": "T_MAT01", "object_type": "U ", "row_count": 5,
             "description": None, "definition": None}]
    cols = [{"schema_name": "dbo", "object_name": "T_MAT01", "column_name": n, "column_id": i, "type_name": "nvarchar",
             "max_length": 40, "precision": 0, "scale": 0, "is_nullable": True, "description": None}
            for i, n in enumerate(("MATNR", "WERKS", "LABST"), 1)]
    with cat.scan(SRC.name):
        ingest(cat, SRC, "ForecastDB", {**FAKE_DB, "objects": FAKE_DB["objects"] + objs,
                                        "columns": FAKE_DB["columns"] + cols})


def test_names_that_say_nothing_new_are_left_out():
    tells = DD._tells_more
    assert not tells("m", "T_MAT01", {}) and not tells("src", "T_MAT01", {}) and not tells("Query1", "T_MAT01", {})
    assert not tells("dp", "dim_plant", {}) and not tells("Plants", "dim_plant", {})
    assert tells("Materials", "T_MAT01", {}) and tells("StockOnHand", "T_MAT01", {})


def test_table_suggestion_uses_sql_alias_power_query_name_and_renamed_columns(catalogue, tmp_path: Path):
    _with_table(catalogue)
    assert DD.table_suggestion(catalogue, KEY)["text"] == "Mat01"  # all its own name says
    root = tmp_path / "p"
    root.mkdir()
    (root / "load.py").write_text(
        'import pandas as pd\n'
        'stock_sql = "SELECT Materials.MATNR AS Material, Materials.WERKS AS Plant FROM dbo.T_MAT01 AS Materials"\n'
        'df = pd.read_sql(stock_sql, conn)\n'
        'other_sql = "SELECT m.MATNR AS Material FROM dbo.T_MAT01 m"\n'
        'df2 = pd.read_sql(other_sql, conn)\n')
    scan_project(catalogue, PythonSource(name="p", path=root))
    from pdcat import colrenames, sqllibrary
    sqllibrary.build_library(catalogue)
    colrenames.find_renames(catalogue)
    sug = DD.table_suggestion(catalogue, KEY)
    assert sug is not None
    assert sug["text"].startswith("Materials. Has material and plant"), sug
    assert "called Materials in 1 place" in sug["source"] and "renamed" in sug["source"]
    assert [n["name"] for n in sug["names"]] == ["Materials"]  # the one-letter alias m is left out

    # a Power Query that loads the table counts too, and the most used name wins
    for report in ("pbi://a/Stock.pbix", "pbi://a/Stock2.pbix"):
        catalogue.add_link(report, "reads", KEY, "test", how="Power Query “Stock on hand”")
    catalogue.conn.commit()
    sug = DD.table_suggestion(catalogue, KEY)
    assert sug["text"].startswith("Stock on hand"), sug
    assert [n["name"] for n in sug["names"]] == ["Stock on hand", "Materials"]
