"""Version 2.1: descriptions drafted from comments, docstrings and rename comments."""
from pathlib import Path

from pdcat import commentdrafts, suggestions
from pdcat.config import PythonSource
from pdcat.scan_python import scan_project


def test_sql_header_and_column_comments():
    sql = ("/* Author: someone\n * Daily sales by store, net of returns.\n */\nCREATE VIEW dbo.vSales AS\n"
           "SELECT s.StoreId,  -- the store\n       s.Units AS qty, -- units sold\n  FROM dbo.Sales s")
    assert commentdrafts.header_comment(sql) == "Daily sales by store, net of returns."
    assert commentdrafts.header_comment("-- Loads the stock table\n-- every night\nCREATE PROCEDURE x AS") == \
        "Loads the stock table every night"
    assert commentdrafts.column_comments(sql) == {"StoreId": "the store", "qty": "units sold"}


def test_suggestions_from_views_scripts_and_renames(catalogue, tmp_path: Path):
    view = "sql://warehouse/forecastdb/ml.vforecastlatest"
    a = catalogue.get_asset(view)
    catalogue.set_columns(view, [{"name": "StoreId", "ordinal": 1}])
    col = "StoreId"
    catalogue.upsert_asset(view, "view", a["name"], a["source"], parent=a["parent"], attrs={
        **a["attrs"], "definition": f"-- Latest forecast per store\nCREATE VIEW x AS SELECT {col}, -- the key\n FROM t"})
    catalogue.conn.commit()
    root = tmp_path / "r"
    (root / "out").mkdir(parents=True)
    (root / "out" / "w.csv").write_text("qty\n1\n")
    (root / "job.py").write_text('"""Builds the weekly extract."""\nimport pandas as pd\n'
                                 'df = pd.read_csv("in.csv")\n'
                                 'df = df.rename(columns={\n    "Units": "qty",  # units sold\n})\n'
                                 'df.to_csv("out/w.csv")\n')
    scan_project(catalogue, PythonSource(name="r", path=root))
    from pdcat import colrenames
    colrenames.find_renames(catalogue)
    commentdrafts.find_descriptions(catalogue)
    found = {(s["payload"]["key"], s["payload"].get("column")): s for s in suggestions.open_suggestions(
        catalogue, "description")}
    assert found[(view, None)]["payload"]["text"] == "Latest forecast per store"
    assert found[(view, col)]["payload"]["text"] == "the key"
    assert found[("py://r/job.py", None)]["payload"]["text"] == "Builds the weekly extract."
    out = catalogue.conn.execute("SELECT dst_key FROM links WHERE relation = 'writes' AND src_key = 'py://r/job.py'").fetchone()[0]
    assert found[(out, "qty")]["payload"]["text"] == "units sold"
    suggestions.accept(catalogue, found[(view, None)]["id"])
    from pdcat.curation import item_info
    assert item_info(catalogue, view)["purpose"] == "Latest forecast per store"
    commentdrafts.find_descriptions(catalogue)
    assert (view, None) not in {(s["payload"]["key"], s["payload"].get("column"))
                                for s in suggestions.open_suggestions(catalogue, "description")}
