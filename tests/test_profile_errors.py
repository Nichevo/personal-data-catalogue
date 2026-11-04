"""Profiling carries on past columns, tables and databases the server can't read."""
import re

import pytest
from conftest import SRC

from pdcat import pacing
from pdcat.profiling import safe_text
from pdcat.review import type_hints_for
from pdcat.scan_sqlserver import run_queue

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"
STORE = "sql://warehouse/forecastdb/dbo.store"
CONVERT = "('22018', '[22018] [Microsoft][ODBC Driver 17 for SQL Server][SQL Server]Error converting data type nvarchar to numeric. (8114) (SQLExecDirectW)')"


class Cursor:
    """Answers the profiling queries; any query that names a column in `bad` fails like the server would."""

    def __init__(self, conn):
        self.connection, self.description, self._rows = conn, None, []

    def execute(self, sql):
        self.connection.sent.append(sql)
        for col, err in self.connection.bad.items():
            if col in sql or (sql.startswith("SELECT TOP") and " * " in sql and err is not UnicodeDecodeError):
                if err is UnicodeDecodeError:
                    raise UnicodeDecodeError("utf-8", b"ab\x9a", 2, 3, "invalid start byte")
                raise RuntimeError(err)
        if self.connection.table_broken and "[SalesDaily]" in sql:
            raise RuntimeError("Invalid object name 'dbo.SalesDaily'.")
        if "COUNT_BIG(*)" in sql:
            names = re.findall(r"AS \[(\w+)\]", sql.split(" FROM ")[0])
            self.description = [(n,) for n in names]
            self._rows = [tuple(100 if n == "__rows" else 90 if n[0] == "n" else 5 if n[0] == "d" else "1"
                                for n in names)]
        else:
            cols = re.search(r"SELECT TOP \(\d+\) (.*?) FROM", sql).group(1)
            names = ["StoreId", "Units"] if cols == "*" else [c.strip("[]") for c in cols.split(", ")]
            self.description = [(n,) for n in names]
            self._rows = [tuple(i % 5 if n == "StoreId" else float(i) for n in names) for i in range(50)]

    def fetchall(self):
        return self._rows


class Conn:
    def __init__(self, bad=None, table_broken=False):
        self.sent, self.bad, self.table_broken, self.timeout = [], bad or {}, table_broken, None

    def cursor(self):
        return Cursor(self)

    def close(self):
        pass


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(pacing.time, "sleep", lambda s: None)


def _run(catalogue, conn, keys=(SALES,)):
    pacing.request_profiles(catalogue, list(keys), approve=True)
    lines = []
    result = run_queue(catalogue, [SRC], progress=lines.append, connector=lambda s, d: conn)
    return result, "\n".join(lines)


def test_a_column_the_server_cant_convert_is_left_out_and_the_rest_profiled(catalogue, no_wait):
    conn = Conn(bad={"[Units]": CONVERT})
    result, log = _run(catalogue, conn)
    assert result["done"] == 1 and result["failed"] == 0
    p = {r["column_name"]: dict(r) for r in catalogue.profile(SALES)}
    assert p["StoreId"]["distinct_count"] == 5 and p["StoreId"]["top_values"] != "[]"
    assert p["Units"]["min_value"] is None
    assert "Error converting data type nvarchar to numeric" in p["Units"]["hints"]
    assert "1 column(s) could not be read" in p["StoreId"]["method"]
    q = {r["asset_key"]: r for r in pacing.queue(catalogue, ("done",))}
    assert "Units" in q[SALES]["error"]
    assert "Units" in log
    assert type_hints_for([p["Units"]], SALES) == []  # not raised as a data type question


def test_text_that_isnt_utf8_does_not_stop_the_table(catalogue, no_wait):
    assert safe_text(b"Ko\x9aice") == "Košice"
    assert safe_text("Košice".encode()) == "Košice" and safe_text(None) is None
    conn = Conn(bad={"[StoreId]": UnicodeDecodeError})
    result, _ = _run(catalogue, conn)
    assert result["done"] == 1
    p = {r["column_name"]: dict(r) for r in catalogue.profile(SALES)}
    assert "UnicodeDecodeError" in p["StoreId"]["hints"] and p["Units"]["distinct_count"] == 5


def test_a_broken_table_fails_alone_and_the_run_carries_on(catalogue, no_wait):
    conn = Conn(table_broken=True)
    result, _ = _run(catalogue, conn, (SALES, STORE))
    assert result["failed"] == 1 and result["done"] == 1 and result["stopped"] is None
    assert pacing.queue_state(catalogue, SALES) == "failed"
    assert pacing.queue_state(catalogue, STORE) == "done"
    assert sum("[SalesDaily]" in s for s in conn.sent) == 2  # whole table, then a row count: no column hunt


def test_a_database_that_cant_be_reached_fails_its_tables_only(catalogue, no_wait):
    def connector(src, db):
        raise RuntimeError("Login failed")
    pacing.request_profiles(catalogue, [SALES], approve=True)
    result = run_queue(catalogue, [SRC], progress=lambda *_: None, connector=connector)
    assert result["failed"] == 1 and result["stopped"] is None
    assert "Login failed" in pacing.queue(catalogue, ("failed",))[0]["error"]


def test_safe_decoding_is_registered_on_the_connection():
    from pdcat.scan_sqlserver import safe_decoding

    class Odbc:
        SQL_CHAR, SQL_VARCHAR, SQL_LONGVARCHAR = 1, 12, -1

    class C:
        def __init__(self):
            self.conv = {}

        def add_output_converter(self, t, f):
            self.conv[t] = f

    c = C()
    safe_decoding(c, Odbc)
    assert set(c.conv) == {1, 12, -1} and c.conv[12](b"\x9a") == "š"
