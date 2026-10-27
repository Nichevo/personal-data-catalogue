# Personal data catalogue

A small tool that runs on your own laptop and catalogues **your** data: the SQL Server
tables you can reach with your own login, your own Python projects, data files, Power BI
reports and Excel workbooks. You can search all of it, see which scripts and reports
read and write which tables, and build up what you know about it into a knowledge base
you can share with colleagues and hand to Copilot.

Nothing leaves your machine. The catalogue is one SQLite file, and your server names
and folder paths live only in a local `config.toml` that git ignores.

## Screenshots

All screenshots use made-up demo data (`python scripts/make_demo.py <folder>` builds it).

### Find

**Search** across tables, columns, scripts, files, reports, notes and knowledge. Each
result says what it matched on:

![Search page](docs/screenshots/search.png)

**A table page**: purpose, grain, owner and trust, the business project it belongs to,
a freshness check, and what needs reviewing. Further down: columns with value ranges,
notes, and which scripts, procedures and reports use it:

![Table page](docs/screenshots/table.png)

**A data file compared with the SQL table it came from**: types, ranges, nulls and
distinct counts side by side, and the scripts that write and read it:

![File page with comparison](docs/screenshots/compare.png)

**Dated file series**: monthly extracts become one entry, with the date range and any
missing months:

![File series page](docs/screenshots/file-series.png)

**Power BI reports**: pages, visuals, measures with their DAX, model tables, and the
SQL tables and files the report loads:

![Power BI report page](docs/screenshots/powerbi.png)

### Know

**Knowledge**: business rules, definitions and quirks that show up wherever their
trigger words do:

![Knowledge page](docs/screenshots/knowledge.png)

**Feature dictionary**: every model input, filled from your feature code and LightGBM
models, with checks for leakage, unused features and drift:

![Feature dictionary](docs/screenshots/features.png)

### Review

**To review**: a prioritised list of what needs your feedback, worked out after every
scan. Tick rows to mark them done, snooze or dismiss them:

![To review page](docs/screenshots/review.png)

**Coverage**: how well documented the catalogue is, and what to document next (with a
draft worked out from the scan):

![Coverage page](docs/screenshots/coverage.png)

**Issue hotspots**: which schemas, folders and tables have the most problems:

![Issue hotspots page](docs/screenshots/hotspots.png)

### Map

**Code map**: how your projects and home-built libraries connect, and the tables and
files they reach. Zoom, open and close groups, click a box to open it, and download as
PNG, SVG or Mermaid:

![Code map page](docs/screenshots/codemap.png)

**Database diagrams**: tables and how they join, split into subject areas worked out
from the joins. Rename areas and move tables; your changes are kept:

![Database diagrams page](docs/screenshots/erd.png)

**Inside a script**: every function in the file, or zoom into one to follow what it
calls (into your libraries too), what it returns, its docstring and the data it touches:

![Whole-script view](docs/screenshots/script-functions.png)

![Zoomed into one function](docs/screenshots/function-zoom.png)

**Environment and libraries**: what each project declares in requirements.txt,
environment.yml or pyproject.toml, compared with what the code really imports:

![Environment section](docs/screenshots/environment.png)

### Add

**Data folders**: point it at any folder of CSV, Parquet, Power BI and Excel files:

![Data folders page](docs/screenshots/folders.png)

**SQL scanning**: tables are only profiled when you ask. Approve them here; queries go
one at a time, and every query is logged:

![SQL scanning page](docs/screenshots/sql-scanning.png)

## New in version 2

- **Gentle SQL Server scanning.** Queries go one at a time with a pause between them, a
  time limit each, an optional quiet-hours window and a cap per run (all on the Settings
  page; defaults 3 s, 60 s, 25 tables). Tables are only profiled when you ask, through
  an approval queue, and tables over 500,000 rows are sampled. Every query is logged.
- **Faster pages** after big scans, with an optional timings panel.
- **Value ranges for SQL tables** as well as files, with range charts, and **data type
  checks** ("this text column holds dates", "whole numbers stored as float").
- **Company-wide keys** such as PlantID: suggested from column names across databases or
  marked by you, shown as badges, used to suggest joins.
- **Sensitive columns**: values are never stored, shown or exported.
- **Dated file series**: `sales_2026-01.csv`, `sales_2026-02.csv`… become one entry with
  the date range, gaps and layout changes. Dates in folder names work too.
- **Better links between code and data**: paths through variables, `os.path.join`,
  `pathlib`, f-strings, config files and `bcp`, each link saying how it was found; add or
  hide links by hand.
- **Duplicates**: the same file in two places, copied folders, mapped drives versus
  network paths, overlapping folder sources.
- **Power BI** (`.pbix`, `.pbit`, `.pbip`): pages, visuals and the measures they show,
  DAX, model tables, relationships, and the SQL tables and files each report loads.
  **Excel workbooks**: Power Query, Microsoft Query connections, which sheet each loads
  into, pivot tables, and SQL in macros.
- **Knowledge base**: knowledge notes that stand alone or appear wherever their trigger
  words do ("Freight is always sea freight"), a question library with worked queries, a
  metric glossary (Power BI measures can become metrics), business projects that group
  tables, scripts and reports, and an **ML feature dictionary** filled from your
  feature code and LightGBM models (with leakage, unused-feature and drift checks).
- **Keeping it tidy**: purpose, grain, refresh, status, trust and owner for every item,
  with drafts worked out from the scan; describe a column once and apply it everywhere;
  edit many items in one grid; a coverage score and "document these next"; notes that
  may be out of date are flagged; change history and freshness.
- **Code map and database diagrams you can zoom**: groups for projects, folders and
  databases that open and close, third-party packages kept in one group, PNG/SVG/Mermaid
  export. Database diagrams split tables into subject areas worked out from their joins.
- **Share and merge with colleagues**: send your notes and knowledge (and optionally
  scans) as one file; merging previews what's new, keeps your words where you differ,
  and can be undone.
- **Copilot pack**: everything the catalogue knows as Markdown or Word documents for a
  Microsoft 365 Copilot agent, with instructions and starter prompts to paste.
- **OpenMetadata export** with a loader script.
- **Backups** to a folder you choose (off until you pick one), and a safe upgrade from
  version 1 that keeps an untouched copy of your old catalogue.
- **Get started page** and an optional **no-install Windows build** for colleagues who
  don't use Python.

## Planned for version 2.1

- **"Called by" on script pages**: every other script that uses a script, in one list.
  That means scripts that import it (and which of its functions they call), and scripts
  that run it with `subprocess`, `os.system`, `runpy`, `python -m`, `%run` or
  `!python`. Run links also show on the code map.
- **Link files found in another folder**: when a script names `sales.csv` but the only
  `sales.csv` catalogued is in a different folder, link it anyway and flag it as a
  possible wrong folder (today the folders must match). When the same file name turns up
  in several folders, say so on the link and on the script and file pages (today the
  newest is picked without a word).
- **Business projects**: add a whole folder (a project, a sub-folder like
  `forecasting/src`, or a data folder) in one go, with an option to keep adding new files
  that appear there. Start a new business project straight from a table or script page,
  with that item already in it.
- **Mark items as not relevant**: one click hides a table such as `dbo.sysdiagrams` from
  search, the review list, coverage, diagrams and exports; scans keep it hidden, and a
  list lets you bring it back. Tables SQL Server tools create for themselves (marked
  `microsoft_database_tools_support`, like `sysdiagrams`) are skipped by default.
- **Clearer feature dictionary**: rename it "ML model features" and say up front that it
  lists the inputs to your machine learning models. Each row names the models that use the
  feature, the script that builds it and the table and column it comes from, each one a
  link, instead of a bare count of models.
- **Copilot pack test questions**: the Copilot pack page lists ten or so questions built
  from your own catalogue ("Which scripts write ml.ForecastOutput?", "What does Forecast
  accuracy mean?", "What replaces dbo.Store?") with the answer the pack holds, so you can
  ask Copilot and check it gets them right. They also go in the pack as a test file.
- **Import from an Excel sheet**: upload a workbook, pick the sheet, and say what each row
  is: a SQL query (title, SQL, notes become pasted SQL linked to the tables it uses, or
  question library entries), a metric (name, definition, formula, e.g. how forecast
  accuracy is calculated), or a knowledge note. Match columns to fields, preview, then
  import; each item remembers which workbook it came from. A sheet of free text can be
  imported whole as one knowledge note.
- **Reference documents on business projects**: attach design documents (Word, PDF,
  Markdown, text) to a business project. Add a new version at any time and every older
  version is kept, newest first, with its date. Tables, scripts and other catalogued
  items the document names are highlighted in the text and listed as links, and those
  items show the document on their own pages. Document text is searchable and can go in
  the Copilot pack.
- **Menu guide**: a link that always shows in the sidebar, opening a page that explains
  every section and page in the menu in a line each, with a button to open it.
- **Installed conda environments**: read the conda environments on your machine (from
  each environment's `conda-meta` folder, without running conda) and record every
  installed package with its exact version and channel. Each environment gets its own
  page, you can link a project to the environment it runs in, and the project page
  compares what's installed with what the code imports and what `environment.yml` asks
  for (missing, extra, or a different version).
- **Import a schedule list**: bring in a CSV that lists scheduled jobs (a Task Scheduler
  export, a job list, a hand-kept sheet). You pick which columns hold the job name, the
  command and the timing. Each job is matched to scripts by the command or path it runs,
  or by a script, folder or project name in any column. A clear match is linked; a
  likely one is shown as a suggestion for you to confirm or change; anything else is
  listed as unmatched so you can link it by hand. Script pages then show when they run.
- **Find more output files**: spot files a script writes in ways the scan misses today:
  `df.to_csv(filepath)` where `filepath` is passed in as a function argument (followed
  back to the call that gives the real path), imported from another module
  (`from settings import OUTPUT_DIR`), an argparse default, a `self.` attribute or
  returned by a helper function; paths looked up in a config dictionary in code;
  every branch of an if/else; `pd.ExcelWriter`, `Path.write_text` and
  `Path.open`, `np.save`, `shutil.copy`, polars `write_csv`/`write_parquet` and openpyxl
  `wb.save`.
- **Suggested metrics**: the Metric glossary suggests metrics found in your code and
  reports: functions such as `wape()` or `forecast_accuracy()`, named calculations
  (`mape = ...`), computed SQL columns (`SUM(x) / SUM(y) AS fill_rate`), and Power BI
  measures not yet in the glossary. Each suggestion shows where it was found and the
  formula, and you tick the ones to add; the rest can be dismissed so they don't come
  back.

## Upgrading from version 1

Install the new version the same way as before (`pip install -e .` or
`conda env update -f environment.yml --prune` then `pip install -e . --no-deps`) and run
`pdcat ui`. The first time it opens your catalogue it upgrades it in place and keeps an
untouched copy of the old file next to it (the path is shown in the app and on the
Settings page). Nothing is removed; every note is kept. Rescan to fill in the new
details (`pdcat scan`).

Version 2 needs Streamlit 1.62 or later; both install commands above update it. With an
older Streamlit the app can stop with "Exception in ASGI application … Expected
http.response.start", which comes from older Streamlit clashing with a newer Starlette.
To update just Streamlit: `pip install -U "streamlit>=1.62"` (or
`conda install -c conda-forge "streamlit>=1.62"` in a conda environment).

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
- **Search** across names, columns, descriptions, notes, knowledge and tags.
- **Needs notes** view: undocumented tables, the ones your code uses most first.
- **Export everything** (items, columns, links, notes, profiles and your knowledge) as
  CSV or JSON, from the web page or the command line, so you can move it into another
  tool later.

Version 2 adds Power BI and Excel, the knowledge base, sharing, the Copilot pack and
more: see [New in version 2](#new-in-version-2).

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

Most of the time you only need two commands:

```powershell
pdcat scan      # read your databases, projects, folders, reports and workbooks
pdcat ui        # open the catalogue in your browser (leave the window open while you use it)
```

Re-run `pdcat scan` whenever your code or databases change. Anything that has
disappeared is marked "missing" rather than deleted, and its notes are kept.

Everything else can be done from the app, or from the terminal:

| Command | What it does |
| --- | --- |
| `pdcat init` | Create a starter config file |
| `pdcat scan` | Scan everything in the config (`--source NAME` for one; `--plan` to see the SQL first) |
| `pdcat ui` | Open the catalogue in your browser |
| `pdcat profile dbo.SalesDaily` | Measure value ranges, nulls and common values of tables (`--used` for all your code uses) |
| `pdcat review` | What needs your attention, most important first |
| `pdcat search "store sales"` | Search from the terminal |
| `pdcat diagram --out map.mmd` | The code map as Mermaid or Graphviz text |
| `pdcat share --out mine.json` | Your notes and knowledge in one file, for a colleague |
| `pdcat merge theirs.json --preview` | See what a colleague's file would change; drop `--preview` to merge |
| `pdcat ai-export --out copilot-pack` | The Copilot pack (`--docx` for Word, `--hide-locations`) |
| `pdcat export` | Everything as CSV (or `--format json`) |
| `pdcat openmetadata --out om.zip` | Tables, descriptions, glossary and lineage for OpenMetadata |

**[Command reference](docs/commands.md)**: every option, with examples and what the
output looks like. In the terminal, `pdcat --help` lists the commands and
`pdcat <command> --help` shows one command's options and examples.

## Export format

| File | One row per | Key columns |
| --- | --- | --- |
| `assets` | table, view, procedure, database, project, script, notebook, data file, file series, report, measure, workbook, ML model, pasted SQL (`sql_snippet`, text in `attrs.sql`) | `key`, `kind`, `name`, `source`, `description`, `attrs` (JSON) |
| `columns` | column of a table or view | `asset_key`, `name`, `data_type`, `description` |
| `links` | "this reads/writes/calls that" | `src_key`, `relation`, `dst`, `matched`, `evidence` (file:line) |
| `notes` | your note, data issue or example | `asset_key` (`<key>#<column>` for column notes), `note_type`, `status`, `body`, `tags`, `author` |
| `profiles` | column of a profiled table or file | `asset_key`, `column_name`, `row_count`, `null_count`, `distinct_count`, `min_value`, `max_value`, `mean_value`, `top_values`, `histogram`, `percentiles` |
| `item_info` | item you've described | `asset_key`, `purpose`, `grain`, `refresh`, `lifecycle`, `trust`, `owner`, `sensitive` |
| `column_info` | column you've described or marked sensitive | `asset_key`, `column_name`, `description`, `sensitive` |
| `relationships` | join between two tables | `src_key`, `src_columns`, `dst_key`, `dst_columns`, `how`, `state` |
| `knowledge`, `questions`, `metrics` | knowledge note, worked question, metric | `title` / `question` / `name`, then the body, SQL or definition |
| `features` | ML feature in the dictionary | `name`, `project`, `description`, `window`, `known_at_prediction`, `models` |
| `keys`, `key_columns` | company-wide key, and the columns that hold it | `name`, `asset_key`, `column_name` |
| `business_projects`, `business_project_members` | business project, and the items in it | `name`, `purpose`, `asset_key` |
| `functions` | function in your code | `asset_key`, `function`, `line`, `parameters`, `returns`, `calls`, `doc` |
| `packages` | package a project declares or imports | `project`, `package`, `version`, `declared_in`, `status` |
| `series_files` | file in a dated file series | `series_key`, `file`, `period`, `rows`, `layout_differs` |
| `change_history` | change seen by a scan | `asset_key`, `at`, `change`, `detail` |

Values from columns marked sensitive are never exported.

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
- Profiling runs a few aggregate queries per table (with `NOLOCK`, so it never blocks
  anyone), one at a time at the pace set on the Settings page. Tables over the sampling
  threshold (500,000 rows by default) are measured from a `TOP (n)` sample.
- Power BI `.pbix` files keep their model compressed. Pages, visuals and queries are
  always read; for the DAX and model tables install the optional readers
  (`pip install -e ".[office]"`, which adds `pbixray` and `olefile`), or save a copy as
  `.pbit` or `.pbip`.
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

## No-install Windows build (optional)

For colleagues who don't use Python, `packaging/windows/build.ps1` makes a zip holding a
complete Python environment (conda-pack) and a **Start catalogue** launcher. They unzip
it and double-click; the Get started page does the rest. GitHub Actions builds it and
attaches it to each release (`.github/workflows/windows-build.yml`). Their catalogue
lives in `%USERPROFILE%\.pdcat`, so a newer zip just replaces the old folder. They
still need Microsoft's ODBC driver to scan SQL Server.

The graph viewer's JavaScript (Cytoscape, ELK and two Cytoscape extensions) is copied
into `src/pdcat/ui/graph/vendor` with its licences, so the app needs no internet.
