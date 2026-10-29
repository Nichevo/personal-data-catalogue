"""2.15: SQL with variables, placeholders, templates, GO batches and script-only lines is still read,
tables are found when sqlglot gives up, slow reading has a time limit, and the scan log counts fallbacks."""
import logging
import time

import pytest

from pdcat import postscan, sqlclean, sqllibrary, sqlparser
from pdcat.sqlrefs import extract_refs

needs_sqlglot = pytest.mark.skipif(not sqlparser.available(), reason="sqlglot is not installed")

PROC = """
CREATE OR ALTER PROCEDURE dbo.usp_LoadForecast @RunDate date = NULL AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @start date = DATEADD(week, -52, ISNULL(@RunDate, GETDATE()));
    DECLARE @rows int;
    BEGIN TRY
        BEGIN TRANSACTION;
        IF OBJECT_ID('tempdb..#hist') IS NOT NULL DROP TABLE #hist;
        SELECT s.Material, s.Plant, s.Week, SUM(s.Qty) AS qty
        INTO #hist
        FROM sales.WeeklySales s WITH (NOLOCK)
        JOIN md.Material m ON m.Material = s.Material
        WHERE s.Week >= @start
        GROUP BY s.Material, s.Plant, s.Week;
        MERGE fc.Forecast AS t
        USING #hist AS h ON t.Material = h.Material AND t.Plant = h.Plant AND t.Week = h.Week
        WHEN MATCHED THEN UPDATE SET t.Actual = h.qty
        WHEN NOT MATCHED THEN INSERT (Material, Plant, Week, Actual) VALUES (h.Material, h.Plant, h.Week, h.qty);
        SET @rows = @@ROWCOUNT;
        PRINT 'Merged ' + CAST(@rows AS varchar(20)) + ' rows';
        INSERT INTO etl.RunLog (Proc, Rows, RunAt) VALUES ('usp_LoadForecast', @rows, GETDATE());
        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        INSERT INTO etl.ErrorLog (Proc, Msg) VALUES ('usp_LoadForecast', ERROR_MESSAGE());
        THROW;
    END CATCH
END
GO
"""


def names(out):
    return {t["name"] for t in out["tables"]}


def relations(out):
    return {t["name"]: t["relation"] for t in out["tables"]}


def test_stored_procedure_with_variables_try_catch_and_merge():
    out = sqlparser.analyse(PROC)
    assert names(out) == {"sales.weeklysales", "md.material", "fc.forecast", "etl.runlog", "etl.errorlog"}
    assert relations(out)["fc.forecast"] == "writes"
    assert "dbo.usp_loadforecast" not in names(out)  # the procedure being made isn't a table it uses
    assert {j["right_table"] for j in out["joins"]} | {j["left_table"] for j in out["joins"]} >= {
        "sales.weeklysales", "md.material"}
    assert "control flow" in out["cleaned"]


@pytest.mark.parametrize("sql, expected", [
    ("SELECT * FROM {}.WeeklySales s JOIN md.Plant p ON p.Plant = s.Plant WHERE s.Week >= '{}'",
     {"weeklysales", "md.plant"}),                                                  # f-string, filled in as {}
    ("SELECT * FROM {schema}.WeeklySales WHERE Week >= {start}", {"weeklysales"}),  # str.format
    ("SELECT Material FROM md.Material WHERE Plant = ? AND Week >= ?", {"md.material"}),  # pyodbc
    ("SELECT Material FROM md.Material WHERE Plant = %s AND Week >= %(week)s", {"md.material"}),  # psycopg/pymssql
    ("SELECT Material FROM md.Material WHERE Plant = :plant", {"md.material"}),  # SQLAlchemy text()
    ("SELECT * FROM {} WHERE Plant = '{}'", set()),  # the whole name is a variable: nothing to name
])
def test_placeholders_from_python(sql, expected):
    out = sqlparser.analyse(sql)
    assert names(out) == expected
    assert out["parser"] == ("sqlglot" if sqlparser.available() else "regex")
    assert not any(c.startswith(sqlclean.MARK) for cols in out["columns"].values() for c in cols)


def test_dbt_jinja_model():
    sql = """{{ config(materialized='incremental') }}
{% set plants = ['1000', '2000'] %}
select p.ebeln, p.matnr, v.name
from {{ source('sap', 'ekpo') }} p
join {{ ref('dim_vendor') }} v on v.lifnr = p.lifnr
{% if is_incremental() %} where p.aedat > (select max(aedat) from {{ this }}) {% endif %}
"""
    out = sqlparser.analyse(sql)
    assert names(out) == {"sap.ekpo", "dim_vendor"}
    assert "templates" in out["cleaned"]
    assert {r.name for r in extract_refs(sql)} == {"sap.ekpo", "dim_vendor"}


def test_sqlcmd_script_with_go_batches():
    sql = ":setvar Db Staging\nUSE $(Db)\nGO\nINSERT INTO $(Db).fc.Forecast SELECT * FROM sales.WeeklySales\nGO 2\n"
    out = sqlparser.analyse(sql)
    assert relations(out) == {"fc.forecast": "writes", "sales.weeklysales": "reads"}
    assert "GO batches" in out["cleaned"]
    assert {r.name for r in extract_refs(sql)} == {"fc.forecast", "sales.weeklysales"}


def test_cursor_and_dynamic_sql():
    sql = """DECLARE @tbl sysname, @sql nvarchar(max);
DECLARE c CURSOR LOCAL FAST_FORWARD FOR SELECT TableName FROM etl.LoadList WHERE Active = 1;
OPEN c;
FETCH NEXT FROM c INTO @tbl;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @sql = N'INSERT INTO stage.Snapshot SELECT * FROM sales.Orders WHERE Region = ''EU''';
    EXEC sp_executesql @sql;
    FETCH NEXT FROM c INTO @tbl;
END
CLOSE c;
DEALLOCATE c;"""
    out = sqlparser.analyse(sql)
    assert {"etl.loadlist", "stage.snapshot", "sales.orders"} <= names(out)
    assert "c" not in names(out)
    assert "SQL in strings" in out["cleaned"]
    assert "c" not in {r.name for r in extract_refs(sql)}


def test_odbc_escapes_and_query_hints():
    assert names(sqlparser.analyse("SELECT * FROM md.Plant WHERE d > {ts '2024-01-01 00:00:00'} "
                                   "OPTION (OPTIMIZE FOR (@p UNKNOWN))")) == {"md.plant"}
    out = sqlparser.analyse("{call dbo.usp_LoadForecast(?)}")
    assert out["fallbacks"] == 0


def test_temp_tables_table_variables_and_use_are_not_tables():
    sql = ("USE Planning; DECLARE @ids TABLE (id int); INSERT INTO @ids SELECT id FROM md.Material; "
           "SELECT * INTO #t FROM @ids i JOIN sales.Orders o ON o.id = i.id; SELECT * FROM ##shared")
    assert names(sqlparser.analyse(sql)) == {"md.material", "sales.orders"}


@needs_sqlglot
def test_statements_sqlglot_cannot_read_are_read_simply(monkeypatch):
    real = sqlparser._parse

    def picky(text):  # an older sqlglot that can't read MERGE
        if "merge" in text.lower():
            raise ValueError("unsupported")
        return real(text)
    monkeypatch.setattr(sqlparser, "_parse", picky)
    sqlparser._analyse.cache_clear()
    out = sqlparser.analyse("SELECT a FROM md.Plant; MERGE fc.Forecast AS t USING stage.Fc AS s ON t.id = s.id "
                            "WHEN MATCHED THEN UPDATE SET t.v = s.v;")
    sqlparser._analyse.cache_clear()
    assert out["parser"] == "mixed" and out["fallbacks"] == 1
    assert names(out) == {"md.plant", "fc.forecast", "stage.fc"}


@needs_sqlglot
def test_commands_are_read_again_from_their_sql():
    command = sqlparser.exp.Command(this="SOMETHING", expression=" ODD INSERT INTO fc.Out SELECT * FROM fc.Source)")
    got = sqlparser._rescue(command)
    assert [type(g).__name__ for g in got] == ["Insert"]


@needs_sqlglot
def test_slow_reading_runs_out_of_time(monkeypatch):
    def slow(text):
        time.sleep(0.5)
        return []
    monkeypatch.setattr(sqlparser, "_parse", slow)
    monkeypatch.setattr(sqlparser, "PARSE_SECONDS", 0.1)
    sqlparser._analyse.cache_clear()
    started = time.monotonic()
    out = sqlparser.analyse("SELECT * FROM md.Plant\nGO\nSELECT * FROM md.Material")
    sqlparser._analyse.cache_clear()
    assert time.monotonic() - started < 0.45
    assert out["timed_out"] and names(out) == {"md.plant", "md.material"}


def test_no_sqlglot_warnings_in_the_console(caplog):
    with caplog.at_level(logging.WARNING):
        sqlparser.analyse("BEGIN DISTRIBUTED TRANSACTION xyz; GRANT SELECT ON md.Plant TO reader; "
                          "SELECT * FROM md.Plant")
    assert not [r for r in caplog.records if r.name.startswith("sqlglot")]


def test_without_sqlglot_the_cleaned_sql_is_read_simply(monkeypatch):
    monkeypatch.setattr(sqlparser, "sqlglot", None)
    sqlparser._analyse.cache_clear()
    out = sqlparser.analyse("SELECT * FROM {{ ref('dim_vendor') }} v JOIN {}.Orders o ON o.v = v.id")
    sqlparser._analyse.cache_clear()
    assert out["parser"] == "regex"
    assert names(out) == {"dim_vendor", "orders"}


@needs_sqlglot
def test_scan_log_counts_how_sql_was_read(catalogue, monkeypatch):
    catalogue.save_snippet("Plants", "SELECT * FROM md.Plant WHERE Plant = ?")
    catalogue.save_snippet("Odd", "SELECT a FROM md.Material; MERGE fc.Forecast AS t USING stage.Fc AS s "
                                  "ON t.id = s.id WHEN MATCHED THEN UPDATE SET t.v = s.v;")
    real = sqlparser._parse
    monkeypatch.setattr(sqlparser, "_parse", lambda t: (_ for _ in ()).throw(ValueError()) if "merge" in t.lower()
                        else real(t))
    sqlparser._analyse.cache_clear()
    postscan.after_scan(catalogue)
    sqlparser._analyse.cache_clear()
    reading = catalogue.setting("after_scan_runs")[-1]["sql_reading"]
    assert reading["exact"] >= 1 and reading["partly"] == 1 and reading["simple"] == 0
    assert reading["cleaned"]["variables"] == 1
    assert sqllibrary.reading_summary(reading) == f"{reading['exact']} read exactly, 1 partly, 0 simply"
    odd = next(q for q in sqllibrary.queries(catalogue) if q["name"] == "Odd")
    assert sqllibrary.read_label(odd["analysis"]) == "partly"


def test_library_reads_simply_after_repeated_time_outs(catalogue, monkeypatch):
    for i in range(4):
        catalogue.save_snippet(f"q{i}", f"SELECT * FROM md.T{i}")
    monkeypatch.setattr(sqllibrary, "MAX_TIMEOUTS", 2)
    calls = []

    def fake(sql, exact=True):
        calls.append(exact)
        return {"tables": [], "joins": [], "columns": {}, "renames": [], "select_star": True,
                "parser": "regex", "timed_out": exact, "version": sqlparser.VERSION}
    monkeypatch.setattr(sqlparser, "analyse", fake)
    sqllibrary.build_library(catalogue)
    assert calls[:2] == [True, True] and len(calls) >= 4 and not any(calls[2:])
    deferred = [q["analysis"].get("deferred") for q in sqllibrary.queries(catalogue)]
    assert deferred.count(True) == len(calls) - 2  # read exactly on the next build
