"""The slow, controlled SQL Server pace: one query at a time, pauses, time limits, quiet hours, the queue."""
from datetime import datetime

import pytest
from conftest import FAKE_DB, SRC

from pdcat import pacing
from pdcat.scan_sqlserver import plan_scan, run_queue, scan_server

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"
STORE = "sql://warehouse/forecastdb/dbo.store"


class FakeCursor:
    def __init__(self, conn):
        self.connection = conn
        self.description = None
        self._rows = []

    def execute(self, sql):
        self.connection.sent.append(sql)
        if self.connection.fail_on and self.connection.fail_on in sql:
            raise RuntimeError("[HYT00] Query timeout expired")
        if "COUNT_BIG(*)" in sql:
            n = sum(1 for part in sql.split(",") if "[n" in part)
            self.description = [("__rows",)] + [(f"{p}{i}",) for i in range(n) for p in ("n", "d", "lo", "hi")]
            self._rows = [(100,) + tuple(v for _ in range(n) for v in (90, 5, "1", "9"))]
        elif "SELECT TOP" in sql:
            self.description = [("StoreId",), ("Units",)]
            self._rows = [(i % 5, float(i)) for i in range(50)]
        elif "sys.objects o" in sql and "OBJECT_DEFINITION" in sql or "NULL AS definition" in sql:
            self.description = [(k,) for k in FAKE_DB["objects"][0]]
            self._rows = [tuple(o.values()) for o in FAKE_DB["objects"]]
        elif "sys.columns c" in sql and "sys.foreign_keys" not in sql:
            self.description = [(k,) for k in FAKE_DB["columns"][0]]
            self._rows = [tuple(c.values()) for c in FAKE_DB["columns"]]
        else:
            self.description = [("kind",), ("schema_name",), ("object_name",), ("column_name",), ("ordinal",),
                                ("ref_schema",), ("ref_object",), ("ref_column",), ("constraint_name",)]
            self._rows = [("pk", "dbo", "Store", "StoreId", 1, None, None, None, "PK_Store")]

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, fail_on=None):
        self.sent, self.fail_on, self.timeout = [], fail_on, None

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        pass


@pytest.fixture
def no_wait(monkeypatch):
    waits = []
    monkeypatch.setattr(pacing.time, "sleep", lambda s: waits.append(s))
    return waits


def test_queries_are_paced_and_logged(catalogue, no_wait):
    conn = FakeConn()
    catalogue.set_setting("sql_pause_seconds", 5)
    scan_server(catalogue, SRC, connector=lambda src, db: conn, progress=lambda *_: None)
    assert len(conn.sent) == 3                      # objects, columns, keys: one query each
    assert len(no_wait) == 2 and all(0 < w <= 5 for w in no_wait)
    log = pacing.query_log(catalogue)
    assert [r["purpose"] for r in log] == ["scan keys", "scan columns", "scan objects"]
    assert conn.timeout == 60
    assert catalogue.get_asset(STORE)["attrs"]["primary_key"] == ["StoreId"]


def test_skip_definitions_setting(catalogue):
    catalogue.set_setting("sql_scan_definitions", False)
    plan = plan_scan(catalogue, SRC)
    assert "OBJECT_DEFINITION" not in plan[0]["query"] and len(plan) == 3


def test_quiet_hours():
    assert pacing.in_quiet_hours("07:00-09:30", datetime(2026, 1, 5, 8, 15))
    assert not pacing.in_quiet_hours("07:00-09:30", datetime(2026, 1, 5, 9, 30))
    assert pacing.in_quiet_hours("22:00-06:00", datetime(2026, 1, 5, 23, 0))
    assert not pacing.in_quiet_hours("", datetime(2026, 1, 5, 23, 0))


def test_nothing_is_profiled_until_approved(catalogue, no_wait):
    conn = FakeConn()
    pacing.request_profiles(catalogue, [SALES, STORE])
    assert pacing.queue_state(catalogue, SALES) == "queued"
    result = run_queue(catalogue, [SRC], progress=lambda *_: None, connector=lambda s, d: conn)
    assert result["done"] == 0 and conn.sent == []
    pacing.set_queue_state(catalogue, [SALES], "approved")
    result = run_queue(catalogue, [SRC], progress=lambda *_: None, connector=lambda s, d: conn)
    assert result == {"done": 1, "failed": 0, "stopped": None, "left": 0}
    assert pacing.queue_state(catalogue, SALES) == "done"
    p = {r["column_name"]: r for r in catalogue.profile(SALES)}
    assert p["Units"]["histogram"]  # range chart from the sample, no extra query
    assert len(conn.sent) == 2


def test_big_tables_are_sampled_and_runs_are_capped(catalogue, no_wait):
    conn = FakeConn()
    catalogue.set_setting("sql_sample_over_rows", 1000)  # SalesDaily has 1,200 rows
    catalogue.set_setting("sql_max_tables_per_run", 1)
    pacing.request_profiles(catalogue, [SALES, STORE], approve=True)
    result = run_queue(catalogue, [SRC], progress=lambda *_: None, connector=lambda s, d: conn)
    assert result["done"] == 1 and result["left"] == 1 and "limit of 1" in result["stopped"]
    assert "SELECT TOP (100000) * FROM [dbo].[SalesDaily]" in conn.sent[0]


def test_a_timeout_stops_the_run(catalogue, no_wait):
    conn = FakeConn(fail_on="[SalesDaily]")
    pacing.request_profiles(catalogue, [SALES, STORE], approve=True)
    result = run_queue(catalogue, [SRC], progress=lambda *_: None, connector=lambda s, d: conn)
    assert "timed out" in result["stopped"]
    assert pacing.queue_state(catalogue, SALES) == "failed"
    assert pacing.queue_state(catalogue, STORE) == "approved"   # waits for the next run
    assert pacing.query_log(catalogue)[0]["status"] == "timeout"


def test_stop_from_the_app(catalogue, no_wait):
    pacing.request_profiles(catalogue, [SALES], approve=True)
    catalogue.set_setting("sql_stop_requested", True)
    p = pacing.Pacer(catalogue, "warehouse")
    with pytest.raises(pacing.PacingStopped):
        p.query(FakeConn().cursor(), "SELECT 1", purpose="test")
