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

**To review**: a prioritised list of what needs your feedback, worked out after every scan.
Comment on an item (saved as a note), mark it done, snooze it or dismiss it:

![To review page](docs/screenshots/review.png)

**Issue hotspots**: which schemas, folders and tables have the most problems:

![Issue hotspots page](docs/screenshots/hotspots.png)

**Code map**: how your projects and home-built libraries connect, which library
functions each one calls, and the tables and files they reach. Download as Mermaid
(pastes into GitHub, Azure DevOps or Confluence) or Graphviz:

![Code map page](docs/screenshots/codemap.png)

**Inside a script**: zoom out to every function in the file, or into one function
to follow what it calls (into your libraries too), what it returns, its docstring and
the data it touches:

![Whole-script view](docs/screenshots/script-functions.png)

![Zoomed into one function](docs/screenshots/function-zoom.png)

**Environment and libraries**: what each project declares in requirements.txt,
environment.yml or pyproject.toml, compared with what the code really imports:

![Environment section](docs/screenshots/environment.png)

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
- **To review list**: after every scan the catalogue works out what needs your eyes:
  undocumented tables and columns, objects that are new, changed or have disappeared,
  odd values (mostly empty or constant columns, negative quantities, future dates),
  CSV/Parquet files whose values go outside their source table, files that look out of
  date, and table names in code that match nothing. It's ordered for big databases:
  things your own code uses, large tables, and tables with open issues come first, and
  "Only items my code uses" narrows it further. Your done/snoozed/dismissed choices stick,
  and problems that get fixed drop off by themselves. Also `pdcat review` in a terminal.
- **Issue hotspots**: schemas, folders and items ranked by open data issues, automatic
  warnings, disappearances and missing documentation.
- **Code map of your projects and home-built libraries**: every import is matched to
  the file that provides it, across projects and libraries (including relative imports),
  and labelled with the functions used. It records which tables and files each *function*
  touches, so "Data reached through libraries" shows exactly what a project ends up
  reading or writing via `common_utils.load_sales()` and further down the chain. Flow
  diagrams at project or file level, on the Code map page and on each project's page.
  Libraries you haven't added yet are spotted from editable installs and `sys.path`
  additions and can be added with one click; `pdcat diagram` writes Mermaid or DOT.
- **Zoom into scripts**: every function, method and nested function is listed with its
  parameters, docstring, what it calls and what it returns. Pick a function to see a
  diagram of its sub-functions (1 to 5 levels deep, following calls into your other
  projects and libraries), the tables and files each one uses, and what calls it, from
  this file or any other. Zoom back out to the whole script or the whole project.
  Links like `?asset=…&fn=train` open straight at a function.
- **Environments and packages**: reads `requirements*.txt`, conda `environment.yml`,
  `pyproject.toml` (including Poetry), `setup.cfg`, `setup.py` and `Pipfile`. Each
  project page shows the Python version, conda env and channels, local `-e` installs,
  the declared packages, packages imported but not declared (`sklearn` is matched to
  `scikit-learn` and so on) and packages declared but never imported. The Code map page
  has one table of packages across all projects, to spot different versions.
- **Search** across names, columns, descriptions, notes and tags.
- **Needs notes** view: undocumented tables, the ones your code uses most first.
- **Export everything** (tables, columns, links and notes) as CSV or JSON, from the
  web page or the command line, so you can move it into another tool later.

## Setup (Windows)

You need the Microsoft ODBC Driver for SQL Server (17 or 18). Most work laptops
already have it: search the Start menu for "ODBC Data Sources" and look under
**Drivers**. Read [SECURITY.md](SECURITY.md) for what the tool connects to and stores.

### With conda (recommended if you use Anaconda or Miniconda)

Open an **Anaconda Prompt** (or any terminal where `conda` works) and run:

```powershell
git clone https://github.com/Nichevo/personal-data-catalogue.git
cd personal-data-catalogue
conda env create -f environment.yml     # creates an environment called "pdcat"
conda activate pdcat
pip install -e . --no-deps              # adds the pdcat command; conda already installed the packages
pdcat init
```

- **Every new terminal:** run `conda activate pdcat` before `pdcat scan` or `pdcat ui`.
- **After pulling new code:** `conda activate pdcat`, then
  `conda env update -f environment.yml --prune`, then `pip install -e . --no-deps`.
- **Separate from your project environments:** pdcat has its own environment and
  never touches your forecasting or other environments. It reads your projects'
  `environment.yml` / `requirements.txt` files as text, so it doesn't need their
  packages installed.
- **Another name or location:** `conda env create -f environment.yml -n my-catalogue`,
  or `-p C:\envs\pdcat`.
- **Channel:** packages come from `conda-forge` only, which is free to use. If your
  network blocks conda-forge, ask IT for the internal mirror and add it with
  `conda config --add channels <mirror-url>`.
- **To remove it:** `conda deactivate`, then `conda env remove -n pdcat`.

### Or with a plain virtual environment

You need Python 3.11+.

```powershell
git clone https://github.com/Nichevo/personal-data-catalogue.git
cd personal-data-catalogue
py -m venv .venv
.venv\Scripts\activate
pip install -e .
pdcat init
```

### Then

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

[[python]]                         # a home-built library your projects import
name = "common-utils"
path = "C:/Users/you/code/common-utils"
role = "library"
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
pdcat review --used             # what needs your attention, starting with what your code uses
pdcat diagram --project forecasting --level projects --out map.mmd   # flow diagram as Mermaid
pdcat search "store sales"      # quick search from the terminal
pdcat export --format csv       # writes one CSV per table to .\catalogue-export
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
| `functions` | function in your code | `asset_key`, `function`, `line`, `parameters`, `returns`, `calls`, `doc` |
| `packages` | package a project declares or imports | `project`, `package`, `version`, `declared_in`, `status` |
| `profiles` | column of a profiled table or file | `asset_key`, `column_name`, `row_count`, `null_count`, `distinct_count`, `min_value`, `max_value`, `mean_value`, `top_values` |

Keys look like `sql://warehouse/yourdatabase/dbo.sales`,
`py://forecasting/src/features.py` and `files://extracts/sales.csv`. CSVs are UTF-8 with a BOM so Excel opens them cleanly.

## Good to know

- Security and privacy: see [SECURITY.md](SECURITY.md). In short: SQL Server access is
  read-only, code is parsed but never run, everything stays in a local unencrypted
  file, and value profiles store real data values.

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
pip install -e ".[dev]"                 # or, in the conda env: pip install -e . --no-deps
pytest
python scripts/make_demo.py C:\temp\pdcat-demo     # sample catalogue to try the app with
streamlit run src/pdcat/app.py -- --db C:\temp\pdcat-demo\catalogue.db
```

## Ideas for later

- Power BI: read `.pbix` / `.pbip` files to list which tables each report uses.
- Export straight into OpenMetadata if the team pilot goes ahead.
