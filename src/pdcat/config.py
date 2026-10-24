"""Loading the local config.toml that lists your connections and projects.

The config file lives on your machine only (it is git-ignored), so server names
and paths never end up in the repo.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_HOME = Path("~/.pdcat").expanduser()

EXAMPLE_CONFIG = '''\
# Personal data catalogue settings. This file stays on your machine.

# Where the catalogue is stored (a single SQLite file).
db_path = "~/.pdcat/catalogue.db"

# One block per SQL Server you can reach with your own Windows login.
# "name" is a short label you choose; it is what the catalogue shows.
[[sqlserver]]
name = "warehouse"
server = "YOUR-SERVER\\\\INSTANCE"
databases = ["YourDatabase"]
driver = "ODBC Driver 18 for SQL Server"
# Most internal servers use self-signed certificates, so this is usually needed.
trust_server_certificate = true
# Optional extra ODBC settings, e.g. "Authentication=ActiveDirectoryInteractive;"
# extra = ""

# One block per Python project folder to scan.
[[python]]
name = "forecasting"
path = "C:/Users/you/code/forecasting"
# Folder names to skip (virtual environments are skipped by default).
exclude = ["data", "outputs"]
# Optional: which server/database bare table names in this project refer to,
# written as "<sqlserver name>/<database>". Helps match "FROM Sales" to the right table.
default_database = "warehouse/YourDatabase"
'''


@dataclass
class SqlServerSource:
    name: str
    server: str
    databases: list[str]
    driver: str = "ODBC Driver 18 for SQL Server"
    encrypt: bool = True
    trust_server_certificate: bool = True
    extra: str = ""


@dataclass
class PythonSource:
    name: str
    path: Path
    exclude: list[str] = field(default_factory=list)
    default_database: str | None = None


@dataclass
class Config:
    path: Path
    db_path: Path
    sqlserver: list[SqlServerSource]
    python: list[PythonSource]

    def source_names(self) -> list[str]:
        return [s.name for s in self.sqlserver] + [p.name for p in self.python]


def find_config(explicit: str | None = None) -> Path:
    """Pick the config file: --config, then $PDCAT_CONFIG, then ./config.toml, then ~/.pdcat/config.toml."""
    if explicit:
        return Path(explicit).expanduser()
    if os.environ.get("PDCAT_CONFIG"):
        return Path(os.environ["PDCAT_CONFIG"]).expanduser()
    local = Path("config.toml")
    if local.exists():
        return local
    return DEFAULT_HOME / "config.toml"


def load_config(path: Path) -> Config:
    if not path.exists():
        raise FileNotFoundError(f"No config at {path}. Run `pdcat init` to create one.")
    with path.open("rb") as f:
        raw = tomllib.load(f)

    sqlserver = []
    for item in raw.get("sqlserver", []):
        dbs = item.get("databases") or ([item["database"]] if "database" in item else [])
        if not dbs:
            raise ValueError(f"sqlserver '{item.get('name')}' needs a 'databases' list")
        sqlserver.append(SqlServerSource(
            name=item["name"],
            server=item["server"],
            databases=list(dbs),
            driver=item.get("driver", SqlServerSource.driver),
            encrypt=item.get("encrypt", True),
            trust_server_certificate=item.get("trust_server_certificate", True),
            extra=item.get("extra", ""),
        ))

    python = []
    for item in raw.get("python", []):
        python.append(PythonSource(
            name=item["name"],
            path=Path(item["path"]).expanduser(),
            exclude=list(item.get("exclude", [])),
            default_database=item.get("default_database"),
        ))

    cfg = Config(
        path=path,
        db_path=Path(raw.get("db_path", str(DEFAULT_HOME / "catalogue.db"))).expanduser(),
        sqlserver=sqlserver,
        python=python,
    )
    names = cfg.source_names()
    if "manual" in names:
        raise ValueError("'manual' is reserved for SQL you paste in; pick another source name")
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ValueError(f"Source names must be unique; repeated: {', '.join(sorted(dupes))}")
    return cfg
