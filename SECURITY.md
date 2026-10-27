# Security

This is a personal tool. It runs on your own PC, uses your own logins, and keeps
everything it learns in a file on your disk. This page says what it touches, what it
stores, and the risks to know about.

## What it connects to

- **SQL Server, read-only.** Connections are opened with `readonly=True` and, by
  default, your Windows login (`Trusted_Connection`). A scan only reads the system
  catalogue views (`sys.objects`, `sys.columns`, extended properties, module
  definitions). It never creates, changes or deletes anything on the server.
- **Profiling** is the only thing that reads table *contents*. It runs a few aggregate
  queries per table (big tables are sampled with `TOP (n)`), with `NOLOCK` so it never
  blocks other work. Table and column names are bracket-quoted (`]` escaped), so odd
  object names can't change the query. It only runs for tables you name or approve in
  the queue.
- **Every query is paced and logged.** One query at a time, a pause between queries,
  a time limit on each, an optional quiet-hours window, a cap on tables per run, and a
  stop switch (Settings page). `pdcat scan --plan` and `pdcat profile --plan` print the
  exact queries without connecting, and the SQL scanning page shows the log of what ran.
- **Your Python projects and data folders, read-only.** Code is read as text and parsed
  with Python's `ast` module. **Nothing is imported or executed**, including `setup.py`
  (parsed, not run) and notebooks (cells are parsed, not run). CSV and Parquet files are
  read with pandas to count rows and profile values. Pickles and models are never
  loaded; LightGBM models are read as text.
- **Power BI and Excel files, read-only.** They're opened as the zip files they are and
  their XML read. Excel macros are decompressed and searched for SQL, **never run**, and
  passwords in connection strings are replaced with `***` before anything is stored.
- **No other network traffic.** The tool doesn't phone home. `pdcat ui` starts
  Streamlit with usage statistics turned off and bound to `localhost`, so other machines
  on the network can't open it.

## What it stores, and where

Everything goes into one SQLite file (`db_path` in your config, by default
`%USERPROFILE%\.pdcat\catalogue.db`) and into any export files you create.

| Stored | Sensitive? |
| --- | --- |
| Server, database, schema, table and column names, view and procedure definitions | Internal metadata: treat it like your SQL code |
| File paths of your projects and data files | Can reveal your username and folder layout |
| Docstrings, function names, environment files | Same as your code |
| **Value profiles**: min/max, most common values, sample rows in "example" notes | **Real data values.** If a column holds personal or confidential data, its top values and ranges end up in the catalogue |
| Your notes and data-issue comments | Whatever you write |
| Power BI queries and DAX, Excel connection strings (passwords removed) and SQL from macros | Same as your code |
| The query log (each SQL Server query sent, when, how long) | Internal metadata |

The catalogue is **not encrypted**. It's protected only by your Windows account's file
permissions, the same as the CSV extracts you already keep locally. So:

- Keep `catalogue.db` and exports on your own drive, not on a shared drive or in a
  synced folder others can read.
- Don't profile tables with personal or confidential data unless you're allowed to keep
  extracts of that data locally. Use `pdcat profile <table>` for the specific tables
  you need, rather than `--used` across everything.
- Exports (`pdcat export`, the download buttons) contain the same information. Handle
  them like data extracts.
- **Mark sensitive columns or items** (column editor or "About this item"). Their values
  are deleted from the catalogue, never measured again, never shown, and left out of
  every export, share file and Copilot pack. The Sensitive data page suggests columns
  whose names look personal (email, phone, salary…).
- **Backups** are off until you pick a folder. Backups are copies of the catalogue, so
  put them somewhere only you (or your team) can read.
- **Share files and the Copilot pack** contain your notes and descriptions, and server
  names and paths unless you hide them. No data values go into either unless you tick
  "include value ranges" for the Copilot pack, and never for sensitive columns. A share
  file can include your scans if you choose. Share them only with people allowed to see
  that metadata.
- To remove everything, delete the catalogue file, its backups and your exports. Nothing
  is kept anywhere else.

## Credentials

- With Windows login (the default) or Azure AD interactive sign-in, **no password is
  stored anywhere**.
- If you must use SQL authentication, don't put the password in `config.toml`. Use
  Windows login or Azure AD instead. If there's no other way, keep `config.toml`
  readable only by you and never commit or share it.
- The tool never writes connection strings into the catalogue or exports.

## This repository is public

The code is on public GitHub. Your own details never belong in it:

- `config.toml`, `*.db` and `.pdcat/` are in `.gitignore`. Server names, project paths
  and connection settings live only in your config file.
- Screenshots and the demo (`scripts/make_demo.py`) use made-up names (`DEMO-SQL01`,
  `SalesDW`, and so on).
- Before you push changes of your own, run `git status` and check that no config,
  catalogue or export file is staged. Don't paste real server names, paths, data or
  screenshots of real data into issues, pull requests or commit messages.
- If something private is ever pushed, deleting it in a later commit isn't enough
  (it stays in history and may already be cached). Make the repo private, remove it
  from history, and change anything that needs changing (for example, ask for a
  password reset).

## The web page

- It listens on `localhost` only. Don't start it with `--server.address 0.0.0.0` or
  put it behind a proxy. It has no login of its own, so anyone who can reach it can
  read and edit the catalogue.
- Notes, docstrings and descriptions are shown as Markdown with HTML turned off, so
  pasted text can't run scripts in the page.
- "Data folders" and "Add library" scan any folder you type in, using your own file
  permissions. They only read files.
- The diagrams are drawn by JavaScript shipped inside the package
  (`src/pdcat/ui/graph/vendor`, copied unmodified from npm with their licences), so the
  page loads nothing from the internet.

## Dependencies

It uses Streamlit, pandas, pyarrow, pyodbc and PyYAML from PyPI or conda-forge, and
optionally `pbixray` and `olefile` to read Power BI models and Excel macros. Keep
them updated (`conda env update -f environment.yml --prune`, or
`pip install -U -e .`) to pick up security fixes. Check with your IT team whether your
organisation needs a licence for Anaconda's `defaults` channel. This project only uses
`conda-forge`, which is free.

## Reporting a problem

If you find a security problem in the code, open a GitHub issue that describes it
without real server names, paths or data. If you think something private was
committed, contact the repository owner directly instead of opening a public issue.
