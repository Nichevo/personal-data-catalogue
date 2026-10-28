"""Version 2.1: the weekly scan in Task Scheduler (the commands and parsing; schtasks only runs on Windows)."""
from pathlib import Path

import pytest

from pdcat import taskscheduler as T


def test_batch_and_commands(tmp_path: Path):
    text = T.batch_text(r"C:\envs\pdcat\python.exe", r"C:\cfg\config.toml", r"C:\cat\scan.log")
    assert r'"C:\envs\pdcat\python.exe" -c' in text and r'--config "C:\cfg\config.toml" scan' in text
    assert "profile --queue" in text and "\r\n" in text
    batch = T.write_batch(tmp_path, None)
    assert batch.exists() and "--config" not in batch.read_text()
    cmd = T.create_command(batch, "FRI", "06:30")
    assert cmd[:3] == ["schtasks", "/Create", "/F"] and "FRI" in cmd and "06:30" in cmd and T.TASK_NAME in cmd
    with pytest.raises(ValueError):
        T.create_command(batch, "XYZ")
    with pytest.raises(ValueError):
        T.create_command(batch, "MON", "7am")


def test_parse_query():
    out = ("Folder: \\\nHostName: PC\nTaskName: \\pdcat weekly scan\nNext Run Time: 03/11/2026 07:00:00\n"
           "Status: Ready\nLast Run Time: 27/10/2026 07:00:01\nLast Result: 0\nSchedule Type: Weekly\n"
           "Start Time: 07:00:00\nDays: MON\n")
    s = T.parse_query(out)
    assert s["worked"] is True and s["last_run"].startswith("27/10/2026") and "Weekly" in s["schedule"]
    assert T.parse_query(out.replace("Last Result: 0", "Last Result: 2"))["worked"] is False
    assert T.parse_query(out.replace("Last Result: 0", "Last Result: 267011"))["worked"] is None


def test_schedule_list_from_an_excel_sheet():
    from test_v21_excel_import import xlsx

    from pdcat import schedules
    data = xlsx({"Notes": [["nothing here"]],
                 "Schedule": [["Weekly jobs"], ["Job", "Runs", "When"],
                              ["Forecast", "python forecast.py", "Mon 07:00"], ["", "", ""],
                              ["Stock", "stock.bat", "Daily 06:00"]]})
    cols, rows = schedules.read_sheet(data, "Schedule")
    assert cols == ["Job", "Runs", "When"]
    assert [r["Job"] for r in rows] == ["Forecast", "Stock"]
    assert schedules.guess_columns(cols)["command"] == "Runs"


def test_schedules_page_takes_an_xlsx(catalogue, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from test_app import goto
    monkeypatch.setenv("PDCAT_DB", str(catalogue.path))
    app = AppTest.from_file(str(Path(__file__).parents[1] / "src" / "pdcat" / "app.py"), default_timeout=30)
    goto(app.run(), "Schedules")
    assert not app.exception
    [up] = [u for u in app.get("file_uploader") if u.proto.label.startswith("Schedule list")]
    assert ".xlsx" in list(up.proto.type)
