"""Setting up without editing files: the first-run page writes config.toml for you.

It checks for a SQL Server ODBC driver (needed to scan SQL Server, and not
something the catalogue can install), adds SQL Servers and Python project
folders to the config file, and tests a connection with one harmless query.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import DEFAULT_HOME, SqlServerSource, load_config

MINIMAL_CONFIG = '''\
# Personal data catalogue settings. This file stays on your machine.
# The app's "Get started" page adds to it; you can also edit it by hand
# (the README shows every setting).

db_path = {db}
'''
DRIVER_URL = "https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server"


def odbc_drivers() -> dict[str, Any]:
    """SQL Server ODBC drivers installed on this computer, best first."""
    try:
        import pyodbc
    except ImportError as e:
        return {"drivers": [], "error": f"pyodbc isn't installed ({e})"}
    try:
        found = [d for d in pyodbc.drivers() if "sql server" in d.lower()]
    except Exception as e:  # unixODBC missing etc.
        return {"drivers": [], "error": str(e)}

    def rank(d: str) -> tuple:
        digits = "".join(ch for ch in d if ch.isdigit())
        return (d.lower().startswith("odbc driver"), int(digits or 0))
    return {"drivers": sorted(found, key=rank, reverse=True), "error": None}


def ensure_config(path: str | Path | None) -> Path:
    """The config file, created with just the catalogue location if it doesn't exist yet."""
    path = Path(path).expanduser() if path else DEFAULT_HOME / "config.toml"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(MINIMAL_CONFIG.format(db=json.dumps(str(DEFAULT_HOME / "catalogue.db").replace("\\", "/"))),
                        encoding="utf-8")
    return path


def _append(path: Path, block: str) -> None:
    """Add a block, and put the file back if the result doesn't load."""
    before = path.read_text(encoding="utf-8")
    path.write_text(before.rstrip() + "\n\n" + block.strip() + "\n", encoding="utf-8")
    try:
        load_config(path)
    except Exception:
        path.write_text(before, encoding="utf-8")
        raise


def _s(v: str) -> str:
    return json.dumps(v)  # a TOML basic string


def add_sqlserver(path: Path, name: str, server: str, databases: list[str], driver: str,
                  trust_server_certificate: bool = True) -> None:
    name, server = name.strip(), server.strip()
    databases = [d.strip() for d in databases if d.strip()]
    if not (name and server and databases):
        raise ValueError("Give a short name, the server and at least one database.")
    _append(path, f"[[sqlserver]]\nname = {_s(name)}\nserver = {_s(server)}\n"
                  f"databases = [{', '.join(_s(d) for d in databases)}]\ndriver = {_s(driver)}\n"
                  f"trust_server_certificate = {'true' if trust_server_certificate else 'false'}")


def add_python(path: Path, name: str, folder: str, default_database: str = "", role: str = "project") -> None:
    name, folder = name.strip(), folder.strip().strip('"')
    if not (name and folder):
        raise ValueError("Give a short name and the project folder.")
    if not Path(folder).expanduser().is_dir():
        raise ValueError(f"Can't find a folder at {folder}")
    block = f"[[python]]\nname = {_s(name)}\npath = {_s(folder.replace(chr(92), '/'))}\nrole = {_s(role)}"
    if default_database.strip():
        block += f"\ndefault_database = {_s(default_database.strip())}"
    _append(path, block)


def test_connection(server: str, database: str, driver: str, trust: bool = True) -> str | None:
    """Connect and run SELECT 1. Returns None when it works, or the error."""
    from .scan_sqlserver import connect
    try:
        conn = connect(SqlServerSource(name="test", server=server, databases=[database], driver=driver,
                                       trust_server_certificate=trust), database)
        try:
            conn.cursor().execute("SELECT 1").fetchall()
        finally:
            conn.close()
        return None
    except Exception as e:
        return f"{type(e).__name__}: {e}"
