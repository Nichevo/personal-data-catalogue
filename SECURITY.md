# Security

This is a personal tool. It runs on your own PC, uses your own logins, and keeps
everything it learns in a file on your disk. This page says what it touches, what it
stores, and the risks to know about.

## What it connects to

- **SQL Server, read-only.** Connections are opened with `readonly=True` and, by
  default, your Windows login (`Trusted_Connection`). A scan only reads the system
  catalogue views (`sys.objects`, `sys.columns`, extended properties, module
  definitions). It never creates, changes or deletes anything on the server.
- **`pdcat profile`** is the only command that reads table *contents*. It runs one
  aggregate query per table plus a `TOP 10000` sample, with `NOLOCK` so it never blocks
  other work. Table and column names are bracket-quoted (`]` escaped), so odd object
  names can't change the query. It only runs when you ask for it.
- **Your Python projects and data folders, read-only.** Code is read as text and parsed
  with Python's `ast` module. **Nothing is imported or executed**, including `setup.py`
  (parsed, not run) and notebooks (cells are parsed, not run). CSV and Parquet files are
  read with pandas to count rows and profile values. Pickles and models are never
  loaded.
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

The catalogue is **not encrypted**. It's protected only by your Windows account's file
permissions, the same as the CSV extracts you already keep locally. So:

- Keep `catalogue.db` and exports on your own drive, not on a shared drive or in a
  synced folder others can read.
- Don't profile tables with personal or confidential data unless you're allowed to keep
  extracts of that data locally. Use `pdcat profile <table>` for the specific tables
  you need, rather than `--used` across everything.
- Exports (`pdcat export`, the download buttons) contain the same information. Handle
  them like data extracts.
- To remove everything, delete the catalogue file and your exports. Nothing is kept
  anywhere else.

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

## Dependencies

It uses Streamlit, pandas, pyarrow, pyodbc and PyYAML from PyPI or conda-forge. Keep
them updated (`conda env update -f environment.yml --prune`, or
`pip install -U -e .`) to pick up security fixes. Check with your IT team whether your
organisation needs a licence for Anaconda's `defaults` channel. This project only uses
`conda-forge`, which is free.

## Reporting a problem

If you find a security problem in the code, open a GitHub issue that describes it
without real server names, paths or data. If you think something private was
committed, contact the repository owner directly instead of opening a public issue.
