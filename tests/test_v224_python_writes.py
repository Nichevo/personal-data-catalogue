"""Version 2.24: table writes from functions called more than once, shared upload functions in other files,
loops over table names, and more write statements in SQL inside Python."""
from pathlib import Path

from pdcat.config import PythonSource
from pdcat.scan_python import scan_project, scan_python_source
from pdcat.sqlrefs import extract_refs, looks_like_sql
from pdcat.store import Catalogue


def _writes(code: str) -> set[str]:
    return {ref for rel, ref, _ in scan_python_source(code).refs if rel == "writes"}


MERGE_HELPER = '''
def to_sql_value(v):
    return v

def merge_rows(cursor, schema, table, df):
    target = f"[{schema}].[{table}]"
    sql = f"""MERGE {target} AS t USING (SELECT ?) AS s ON t.id = s.id
    WHEN NOT MATCHED THEN INSERT VALUES (s.id);"""
    for row in df.itertuples(index=False):
        cursor.execute(sql, tuple(to_sql_value(value) for value in row))
'''


def test_each_call_of_a_merge_helper_gives_its_own_table():
    code = MERGE_HELPER + '''
merge_rows(cur, "dbo", "StockLevels", a)
merge_rows(cur, "stg", "Orders", b)
merge_rows(cur, schema="dbo", table="Forecast", df=c)
'''
    # each call's schema stays with its own table: no stg.stocklevels or dbo.orders
    assert _writes(code) == {"table:dbo.stocklevels", "table:stg.orders", "table:dbo.forecast"}


def test_helper_that_returns_the_sql_and_methods():
    code = '''
def build(table):
    return f"MERGE {table} AS t USING (SELECT 1) s ON 1=0 WHEN NOT MATCHED THEN INSERT VALUES (1);"
def write(cursor, table, df):
    sql = build(table)
    cursor.execute(sql, tuple(df))
write(c, "dbo.A", a)
write(c, "dbo.B", b)

class Loader:
    def save(self, df, name):
        df.to_sql(name, self.engine, schema="dbo")
    def run(self):
        self.save(x, "C")
        self.save(y, "D")
'''
    assert _writes(code) == {"table:dbo.a", "table:dbo.b", "table:dbo.c", "table:dbo.d"}


def test_loops_over_table_names():
    assert _writes('for t in ["sales", "stock"]:\n    df.to_sql(t, engine)') == {"table:sales", "table:stock"}
    assert _writes('for name, d in {"sales": a, "stock": b}.items():\n    d.to_sql(name, engine)') == \
        {"table:sales", "table:stock"}
    code = ('TABLES = [("dbo", "A"), ("stg", "B")]\n'
            'def up(cur, schema, table):\n    cur.execute(f"TRUNCATE TABLE {schema}.{table}")\n'
            'for s, t in TABLES:\n    up(cur, s, t)\n')
    assert _writes(code) == {"table:dbo.a", "table:stg.b"}


def test_more_write_statements():
    for sql, table in [("INSERT dbo.Sales (a) VALUES (?)", "dbo.sales"), ("DELETE dbo.Sales WHERE d < ?", "dbo.sales"),
                       ("BULK INSERT dbo.Sales FROM 'c:/x.csv'", "dbo.sales"), ("DROP TABLE IF EXISTS dbo.Sales", "dbo.sales"),
                       ("ALTER TABLE dbo.Sales ADD c int", "dbo.sales")]:
        assert looks_like_sql(sql), sql
        assert ("writes", table) in {(r.relation, r.name) for r in extract_refs(sql)}, sql
    assert {(r.relation, r.name) for r in extract_refs("{CALL dbo.LoadSales (?)}")} == {("calls", "dbo.loadsales")}
    for prose in ("Please drop table owners a note", "alter table settings in the menu"):
        assert not looks_like_sql(prose)


def test_other_writers():
    assert _writes('class A:\n    T = "dbo.A"\n    def go(self):\n        cur.execute(f"INSERT INTO {self.T} VALUES (1)")') \
        == {"table:dbo.a"}
    assert _writes('df.write_database(table_name="dbo.Sales", connection=c)') == {"table:dbo.sales"}
    assert _writes('df.write.mode("overwrite").saveAsTable("lake.sales")') == {"table:lake.sales"}
    code = ('from sqlalchemy import Table, insert\nt = Table("Sales", meta, schema="dbo")\nconn.execute(insert(t))\n'
            'x = []\nx.insert(0, 1)\n')
    assert _writes(code) == {"table:dbo.sales"}


def test_scripts_calling_a_shared_upload_get_their_own_tables(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "etl").mkdir(parents=True)
    (root / "etl" / "__init__.py").write_text("")
    (root / "etl" / "upload.py").write_text(MERGE_HELPER + '\nif __name__ == "__main__":\n'
                                            '    merge_rows(None, "dbo", "TestTable", None)\n')
    for t in ("StockLevels", "Orders", "Forecast"):
        (root / f"load_{t}.py").write_text(f'from etl.upload import merge_rows\nSCHEMA = "dbo"\n'
                                           f'def main(cur, df):\n    merge_rows(cur, SCHEMA, "{t}", df)\n')
    (root / "load_more.py").write_text('import etl.upload as up\n'
                                       'up.merge_rows(cursor=c, schema="stg", table="Plants", df=d)\n'
                                       'c.execute(open("sql/tidy.sql").read())\n')
    (root / "sql").mkdir()
    (root / "sql" / "tidy.sql").write_text("DELETE FROM dbo.Stage\n")
    cat = Catalogue(tmp_path / "c.db")
    try:
        scan_project(cat, PythonSource(name="proj", path=root))
        writes = {}
        for r in cat.conn.execute("SELECT src_key, dst_ref FROM links WHERE relation = 'writes'"):
            writes.setdefault(r["src_key"].rsplit("/", 1)[-1], set()).add(r["dst_ref"])
        assert writes["load_StockLevels.py"] == {"table:dbo.stocklevels"}
        assert writes["load_Orders.py"] == {"table:dbo.orders"}
        assert writes["load_Forecast.py"] == {"table:dbo.forecast"}
        assert writes["load_more.py"] == {"table:stg.plants"}
        assert writes["upload.py"] == {"table:dbo.testtable"}
        run = cat.conn.execute("SELECT dst_key FROM links WHERE relation = 'runs'").fetchone()
        assert run["dst_key"].endswith("sql/tidy.sql")
        import json
        attrs = json.loads(cat.conn.execute("SELECT attrs FROM assets WHERE name = 'etl/upload.py'").fetchone()[0])
        assert "merge_rows" not in attrs.get("function_refs", {})  # not handed to every importer
    finally:
        cat.close()


def test_sql_files_run_from_python():
    for code in ('cur.execute(open("sql/load.sql").read())',
                 'sql = open("sql/load.sql").read()\ncur.execute(sql)',
                 'from pathlib import Path\nconn.execute(text(Path("sql/load.sql").read_text()))'):
        runs = scan_python_source(code).attrs.get("runs")
        assert runs and runs[0]["target"] == "sql/load.sql", code
