# Personal data catalogue

A small tool that runs on your own laptop and catalogues **your** data: the SQL Server
tables you can reach with your own login, and your own Python projects. You can
search all of it, see which scripts read and write which tables, and keep notes
alongside it.

Nothing leaves your machine. The catalogue is one SQLite file, and your server names
and folder paths live only in a local `config.toml` that git ignores.

## Screenshots

All screenshots use made-up demo data (`python scripts/make_demo.py <folder>` builds it).

**Search** across tables, columns, scripts, files, notes and tags:

![Search page](docs/screenshots/search.png)

**A table page**: notes, a data issue on a column, pasted example values, value ranges
per column, and which scripts, procedures and pasted SQL use it:

![Table page](docs/screenshots/table.png)

**A CSV/Parquet file compared with the SQL table it came from**:

![File page with comparison](docs/screenshots/compare.png)

**Data folders**: point it at any folder of CSV/Parquet files:

![Data folders page](docs/screenshots/folders.png)

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
- **CSV and Parquet files as data sources**: files inside your projects, plus any other
  folder you add from the **Data folders** page (paste the folder path, it scans every
  CSV/Parquet in it). Each file records its size, modified date, row count and columns.
  Scripts are linked to the files they write, including dated names like
  `f"outputs/forecast_{month}.csv"` (matched to the newest file).
- **Value profiles**: nulls, distinct counts, min/max, mean and most common values per
  column. Data files are profiled when scanned; SQL tables with `pdcat profile`.
- **Compare side by side**: on a file or table page, compare its columns, ranges and
  null rates with another one. It suggests where the data came from (the tables read by
  the script that wrote the file), so you can see what changed between the core SQL
  table and your local extract.
- **Notes, data issues and example values** on any table, column, view, file, script or
  project. Data issues stay open until you mark them resolved and are listed on their
  own page. Example values keep their layout, so you can paste sample rows or ranges.
  Notes are stored separately from scanned data, so re-scanning never touches them.
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
pdcat profile dbo.SalesDaily    # measure value ranges of a table (read-only)
pdcat profile --used            # ...or of every table your code or pasted SQL uses
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
| `notes` | your note, data issue or example | `asset_key` (`<key>#<column>` for column notes), `note_type`, `status`, `body`, `tags` |
| `profiles` | column of a profiled table or file | `asset_key`, `column_name`, `row_count`, `null_count`, `distinct_count`, `min_value`, `max_value`, `mean_value`, `top_values` |

Keys look like `sql://warehouse/yourdatabase/dbo.sales`,
`py://forecasting/src/features.py` and `files://extracts/sales.csv`. CSVs are UTF-8 with a BOM so Excel opens them cleanly.

## Good to know

- Table detection in code is a pattern match, not a full SQL parser. Dynamic table
  names (`f"FROM {table}"` where `table` isn't a constant) are skipped, and the
  **Unmatched tables** view lists names that didn't match anything, so you can spot
  missing servers or false positives.
- The scanner only reads system catalogue views, so it sees exactly what your login
  can see and changes nothing on the server.
- `pdcat profile` runs one aggregate query per table (with `NOLOCK`, so it never blocks
  anyone) plus a `TOP 10000` sample for most-common values. On very large tables the
  aggregate still scans the whole table, so profile those deliberately.
- Large data files are profiled from their first 200,000 rows (`max_rows` in the
  config). Parquet row counts are always exact; for big CSVs the count is a line count.
  Unchanged files are not re-read on the next scan.
- Keep secrets out of `config.toml`. Windows login means there is no password to store.

## Development

```powershell
pip install -e ".[dev]"
pytest
python scripts/make_demo.py C:\temp\pdcat-demo     # sample catalogue to try the app with
streamlit run src/pdcat/app.py -- --db C:\temp\pdcat-demo\catalogue.db
```

## Ideas for later

- Power BI: read `.pbix` / `.pbip` files to list which tables each report uses.
- Export straight into OpenMetadata if the team pilot goes ahead.
