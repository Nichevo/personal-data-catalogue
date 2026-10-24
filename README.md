# Personal data catalogue

A small tool that runs on your own laptop and catalogues **your** data: the SQL Server
tables you can reach with your own login, and your own Python projects. You can
search all of it, see which scripts read and write which tables, and keep notes
alongside it.

Nothing leaves your machine. The catalogue is one SQLite file, and your server names
and folder paths live only in a local `config.toml` that git ignores.

## What it does

- **Scans SQL Server** with your Windows login: tables, views and stored procedures,
  their columns and types, row counts, and any existing `MS_Description` descriptions.
  It reads view and procedure definitions to find which tables they read and write.
- **Scans Python projects**: `.py` files, notebooks (`.ipynb`) and `.sql` files. It
  records each file's docstring, functions and imports, and finds the tables and files
  the code uses: SQL in strings and f-strings, `read_sql_table` / `to_sql`,
  `read_csv` / `to_parquet` and friends, `open()`, `joblib.dump/load`, `save_model`.
- **Matches table names in code to scanned tables**, so a table page shows "used by
  `features.py` (reads)" and a script page shows the tables it touches.
- **Paste in SQL by hand** ("Add SQL" in the web page): give it a title and a note, and
  it becomes a searchable item linked to the tables it reads and writes, so it shows
  up under "Used by" on those tables. Pasted SQL is never touched by scans.
- **Notes and tags** on any table, column, view, script or project. Notes are stored
  separately from scanned data, so re-scanning never touches them.
- **Search** across names, columns, descriptions, notes and tags.
- **Needs notes** view: undocumented tables, the ones your code uses most first.
- **Export everything** (tables, columns, links and notes) as CSV or JSON, from the
  web page or the command line, so you can move it into another tool later.

## Setup (Windows)

You need Python 3.11+ and the Microsoft ODBC Driver for SQL Server (17 or 18). Most
work laptops already have it; check "ODBC Data Sources" → Drivers.

```powershell
git clone https://github.com/Nichevo/personal-data-catalogue.git
cd personal-data-catalogue
py -m venv .venv
.venv\Scripts\activate
pip install -e .

pdcat init
```

`pdcat init` creates `%USERPROFILE%\.pdcat\config.toml`. Open it and fill in your
servers and project folders:

```toml
db_path = "~/.pdcat/catalogue.db"

[[sqlserver]]
name = "warehouse"                 # a short label you choose
server = "YOUR-SERVER\\INSTANCE"
databases = ["YourDatabase"]
driver = "ODBC Driver 18 for SQL Server"
trust_server_certificate = true

[[python]]
name = "forecasting"
path = "C:/Users/you/code/forecasting"
exclude = ["data", "outputs"]
default_database = "warehouse/YourDatabase"   # where bare table names like "Sales" live
```

If your server uses Azure AD sign-in instead of Windows login, add
`extra = "Authentication=ActiveDirectoryInteractive;"` to that block.

## Use

```powershell
pdcat scan                      # scan everything in the config
pdcat scan --source forecasting # or just one source
pdcat ui                        # opens the catalogue in your browser
pdcat search "store sales"      # quick search from the terminal
pdcat export --format csv       # writes assets/columns/links/notes CSVs to .\catalogue-export
pdcat export --format json --out C:\temp\catalogue
```

Re-run `pdcat scan` whenever your code or databases change. Anything that has
disappeared is marked "missing" rather than deleted, and its notes are kept.

## Export format

| File | One row per | Key columns |
| --- | --- | --- |
| `assets` | table, view, procedure, database, project, script, notebook, pasted SQL (`sql_snippet`, text in `attrs.sql`) | `key`, `kind`, `name`, `source`, `description`, `attrs` (JSON) |
| `columns` | column of a table or view | `asset_key`, `name`, `data_type`, `description` |
| `links` | "this reads/writes/calls that" | `src_key`, `relation`, `dst`, `matched`, `evidence` (file:line) |
| `notes` | your note | `asset_key` (`<key>#<column>` for column notes), `body`, `tags` |

Keys look like `sql://warehouse/yourdatabase/dbo.sales` and
`py://forecasting/src/features.py`. CSVs are UTF-8 with a BOM so Excel opens them cleanly.

## Good to know

- Table detection in code is a pattern match, not a full SQL parser. Dynamic table
  names (`f"FROM {table}"` where `table` isn't a constant) are skipped, and the
  **Unmatched tables** view lists names that didn't match anything, so you can spot
  missing servers or false positives.
- The scanner only reads system catalogue views, so it sees exactly what your login
  can see and changes nothing on the server.
- Keep secrets out of `config.toml`. Windows login means there is no password to store.

## Development

```powershell
pip install -e ".[dev]"
pytest
```

## Ideas for later

- Power BI: read `.pbix` / `.pbip` files to list which tables each report uses.
- Export straight into OpenMetadata if the team pilot goes ahead.
