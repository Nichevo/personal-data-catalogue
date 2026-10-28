# Commands

Everything here can also be done from the app (`pdcat ui`). The commands are handy for
scheduled scans, scripts, and quick lookups from a terminal.

Run them in the terminal where you installed the catalogue (on Windows: **Anaconda
Prompt** with `conda activate pdcat`, or the virtual environment you made). Every command
has built-in help with examples:

```powershell
pdcat --help            # the list of commands
pdcat scan --help       # one command's options and examples
```

## Quick start

| I want to… | Run |
| --- | --- |
| Set it up the first time | `pdcat init`, edit the config, then `pdcat scan` |
| Open the catalogue | `pdcat ui` |
| Refresh it after code or tables change | `pdcat scan` |
| See what needs my attention | `pdcat review` |
| Look something up without opening the app | `pdcat search "store sales"` |
| Measure the values in a table | `pdcat profile dbo.SalesDaily` |
| Send my notes to a colleague | `pdcat share --out mine.json` |
| Bring in a colleague's notes | `pdcat merge theirs.json --preview`, then without `--preview` |
| Give Copilot what the catalogue knows | `pdcat ai-export --out copilot-pack` |
| Get everything out as files | `pdcat export` |

## Which config file is used

Every command reads `config.toml` from the current folder, or else
`%USERPROFILE%\.pdcat\config.toml`. To use another one, put `--config` **before** the
command name:

```powershell
pdcat --config C:\work\catalogue\config.toml scan
```

The config says where the catalogue file is (`db_path`) and lists your SQL Servers,
Python projects and data folders. The README shows an example; `pdcat init` writes a
commented one.

---

## Setting up and opening

### `pdcat init`

Creates a starter `config.toml` in `%USERPROFILE%\.pdcat` (or at `--config`). It never
overwrites an existing one. Open the file, fill in your servers and folders, then scan.

You can skip this: `pdcat ui` on a new machine creates an empty config and opens the
**Get started** page, which fills it in for you.

### `pdcat ui`

Opens the catalogue in your browser at `http://localhost:8501`. Leave the terminal
window open while you use it; close it (or press Ctrl+C) to stop.

| Option | What it does |
| --- | --- |
| `--port 8600` | Use another port, for example if 8501 is taken |

The app only listens on your own machine (`localhost`), so nobody else can open it.

---

## Scanning

### `pdcat scan`

Reads everything in the config: SQL Server tables, views and procedures, your Python
projects and libraries, data folders, Power BI reports and Excel workbooks. Then it
works out the links between them and refreshes the review list.

```powershell
pdcat scan                                   # everything
pdcat scan --source forecasting              # one source, by the name in your config
pdcat scan --source warehouse --database Sales
pdcat scan --plan                            # print the SQL Server queries, send nothing
```

| Option | What it does |
| --- | --- |
| `--source NAME` | Only this source. Repeat it for more than one. |
| `--database DB` | Only this SQL Server database. Repeatable. |
| `--plan` | Print every query a SQL Server scan would send, without connecting. |

- Queries go to SQL Server one at a time, with the pause and time limit set on the
  app's **Settings** page (defaults: 3 seconds apart, 60 seconds each).
- If one source fails (a server is down, say), the others still scan, and the command
  ends with exit code 1.
- Nothing is ever deleted. Items that have gone are marked "missing" and keep their notes.
- Re-run it whenever your code or databases change. Unchanged files aren't re-read.

To refresh the catalogue on its own every week, see [Scan on a schedule](#scan-on-a-schedule).

### `pdcat profile`

Measures what is in SQL tables: value ranges, nulls, distinct counts, common values and
range charts. Scans only read the structure, so this is the step that touches data.
It is read-only and uses `NOLOCK`, so it never blocks anyone.

```powershell
pdcat profile dbo.SalesDaily dbo.Store       # these tables
pdcat profile --used                         # every table your code or pasted SQL uses
pdcat profile --used --source warehouse      # ...on one server only
pdcat profile --queue                        # the tables approved under Add › Sources › SQL scanning
pdcat profile --used --plan                  # print the queries, send nothing
```

| Option | What it does |
| --- | --- |
| `TABLE ...` | Table names like `dbo.SalesDaily`. |
| `--used` | Every table your code or pasted SQL uses. |
| `--source NAME` | Only tables on this SQL Server source. Repeatable. |
| `--queue` | Run what's approved in the app's queue. |
| `--plan` | Print the queries, without connecting. |

- Tables over 500,000 rows are measured from a sample (the limit is on the Settings page).
- It stops at the per-run cap and during quiet hours (both on the Settings page). The rest
  wait in the queue for next time, and the command says how many are left.
- Values from columns marked sensitive are never kept, shown or exported.

---

## Finding things

### `pdcat search`

```powershell
pdcat search "store sales"
pdcat search plant --kind table --limit 5
```

| Option | What it does |
| --- | --- |
| `text` | Words to look for in names, columns, descriptions, notes, knowledge and code. |
| `--kind KIND` | Only one kind: `table`, `view`, `procedure`, `script`, `project`, `data_file`, `file_series`, `report`, `measure`, `workbook`, `knowledge`, `question`, `metric`, `feature`, `key`, `bproject` and so on. |
| `--limit N` | Show at most N matches (default 20). |

Each match shows its kind, name, key and the first line of its description:

```text
table        dbo.SalesDaily  [4 note(s)]
             sql://warehouse/salesdw/dbo.salesdaily
             Daily unit sales per store and product, loaded nightly from the POS feed.
knowledge    Freight is always sea freight
             kb://6855c00baa624f488d208204dfe919c0
```

### `pdcat review`

The same prioritised list as the app's **To review** page: odd values, data that looks
out of date, tables that changed since your notes, missing descriptions and so on.

```powershell
pdcat review
pdcat review --used --limit 10
```

| Option | What it does |
| --- | --- |
| `--used` | Only items your code uses. |
| `--limit N` | Show at most N items (default 30). |

```text
[Odd values] Odd values in dbo.SalesDaily
    Units has negative values (min -24); Revenue has negative values (min -310.5)
[Data looks out of date] dbo.SalesDaily: newest SaleDate is 2026-10-23
    4 days old when last measured (2026-10-27); expected within 3 days. Is the load still running?
```

To comment on an item, mark it done or snooze it, use the app.

### `pdcat diagram`

Writes the code map as text you can paste elsewhere. In the app, the **Code map** page
shows the same thing as a diagram you can zoom and open up.

```powershell
pdcat diagram --out map.mmd                                     # everything, one box per script
pdcat diagram --project forecasting --level projects --out map.mmd
pdcat diagram --format dot --no-data --out code.dot             # Graphviz, code only
```

| Option | What it does |
| --- | --- |
| `--project NAME` | Start from this project or library (default: everything). |
| `--level modules` or `projects` | One box per script (default) or one per project. |
| `--no-data` | Leave out tables and files. |
| `--format mermaid` or `dot` | Mermaid (default) pastes straight into GitHub, Azure DevOps and Confluence. `dot` is for Graphviz. |
| `--out FILE` | Write to a file (default: print it). |

---

## Sharing

### `pdcat share`

Writes your notes, descriptions and knowledge (knowledge notes, questions, metrics,
features, company-wide keys, business projects) to one JSON file for a colleague.

```powershell
pdcat share --out mine.json
pdcat share --out mine.json --scan warehouse     # also send your scan of one source
```

| Option | What it does |
| --- | --- |
| `--out FILE` | File to write (default `catalogue-share.json`). |
| `--scan SOURCE` | Also include your scan of this source, for a colleague who can't scan it themselves. Repeatable. |

The file holds no passwords and no values from sensitive columns. It does hold your
server names and notes, so send it only to people who may see those.

### `pdcat merge`

Brings a colleague's share file into your catalogue. **Always preview first.**

```powershell
pdcat merge theirs.json --preview                # see what would change
pdcat merge theirs.json                          # merge it
pdcat merge theirs.json --map dw=warehouse       # their source "dw" is your "warehouse"
pdcat merge theirs.json --take-theirs            # where you differ, use their words
```

| Option | What it does |
| --- | --- |
| `file` | The `.json` file they sent. |
| `--map THEIRS=YOURS` | When you named a source differently. Most are matched for you by server, database or folder. Repeatable. |
| `--take-theirs` | Where you both wrote something different, use theirs. By default yours is kept. |
| `--preview` | Show the counts, change nothing. |

```text
From Sam (2026-10-27). Their sources: dw -> warehouse, extracts -> extracts
  notes: 3 new, 1 fill gaps, 1 different, 6 same
  knowledge: 2 new, 0 fill gaps, 0 different, 0 same
Preview only: nothing was changed.
```

"Fill gaps" means they wrote something where you had nothing. "Different" means you
both wrote something; yours is kept unless you use `--take-theirs`. Each merge can be
undone on the app's **Share with colleagues** page, under **Merged files**.

---

## Exporting

### `pdcat ai-export`

Writes the **Copilot pack**: what the catalogue knows, as documents a Microsoft 365
Copilot agent can use as knowledge, plus the instructions and starter prompts to paste
into the agent.

```powershell
pdcat ai-export --out C:\temp\copilot-pack
pdcat ai-export --out C:\temp\copilot-pack --docx --hide-locations
```

| Option | What it does |
| --- | --- |
| `--out FOLDER` | Folder to write to (default `copilot-pack`). |
| `--docx` | Word files instead of Markdown. |
| `--hide-locations` | Leave out server names and file locations. |
| `--values` | Include value ranges and common values. Sensitive columns are always left out. |
| `--documents` | Include the text of reference documents kept with business projects. |

Put the folder on OneDrive or SharePoint, add it to a Copilot agent as knowledge, and
paste in `agent-instructions`. Since 2.1 the pack also has naming-rules, sap, sql-queries
and test-questions files. The app's **Copilot pack** page does the same with a
download button.

### `pdcat export`

Everything in the catalogue as plain files, for Excel, Power BI or another tool.

```powershell
pdcat export                                     # CSVs in .\catalogue-export
pdcat export --format json --out C:\temp\catalogue
```

| Option | What it does |
| --- | --- |
| `--format csv` or `json` | One CSV per table (default), or a single `catalogue.json`. |
| `--out FOLDER` | Folder to write to (default `catalogue-export`). |

The README's **Export format** section lists the files and their columns. CSVs are
UTF-8 with a BOM, so Excel opens them cleanly. Values of sensitive columns are never
exported.

### `pdcat openmetadata`

A zip for loading into OpenMetadata: services, databases, schemas, tables and columns
with your descriptions, the metric glossary, and lineage from your scripts.

```powershell
pdcat openmetadata --out om.zip
```

Unzip it and run the loader inside it against your OpenMetadata server. It needs the
`requests` package and a bot token from OpenMetadata, and is safe to run again. Put the
token in an environment variable rather than on the command line, so it doesn't end up
in your command history:

```powershell
$env:OPENMETADATA_TOKEN = "<bot token>"
python load_openmetadata.py https://your-openmetadata:8585 --dry-run   # list what it would send
python load_openmetadata.py https://your-openmetadata:8585
```

---

## Scan on a schedule

Windows Task Scheduler can refresh the catalogue for you, say every Monday at 7am.

**The quick way (2.1):** on the app's **Settings** page, under **Scan on a schedule**,
pick a day and time and click **Schedule the weekly scan**. The app writes a batch file
next to your catalogue that uses the same Python as the app (no conda paths to fill in),
creates a task called `pdcat weekly scan`, and from then on shows when it last ran,
whether it worked, the end of `scan.log`, and a button to remove it. The task is the same
one the steps below make by hand (it keeps the name `pdcat weekly scan`), so use one way
or the other.

**By hand:** the repo has a ready-made batch file, `scripts\windows\pdcat-weekly-scan.bat`, that runs
`pdcat scan` and then `pdcat profile --queue` (the tables you approved under
**Add › Sources › SQL scanning**), and writes everything to `%USERPROFILE%\.pdcat\scan.log`.

**1. Copy the batch file and check two lines.** Copy it to `%USERPROFILE%\.pdcat\`
and open it in Notepad:

- `CONDA_BASE`: where conda is installed. In Anaconda Prompt, run `conda info --base`
  and paste what it prints (often `C:\Users\you\miniconda3` or `...\anaconda3`).
- `PDCAT_CONFIG`: your `config.toml`. The default, `%USERPROFILE%\.pdcat\config.toml`,
  is where `pdcat init` and the **Get started** page put it.

If you used a virtual environment instead of conda, replace the `call ... activate.bat`
line with `call C:\path\to\venv\Scripts\activate.bat`.

**2. Try it once by hand.** Double-click the batch file, then open `scan.log`. It
should end with the scan's summary. Fix anything it complains about before scheduling.

**3. Create the task.** In an ordinary Command Prompt (not as administrator), run:

```bat
schtasks /Create /TN "pdcat weekly scan" /SC WEEKLY /D MON /ST 07:00 /TR "%USERPROFILE%\.pdcat\pdcat-weekly-scan.bat"
```

Change `/D MON /ST 07:00` for another day and time (`/D MON,THU` for twice a week, or
`/SC DAILY` without `/D` for every day). Or use the Task Scheduler app: **Create Basic
Task**, pick **Weekly**, choose **Start a program** and browse to the batch file.

The task runs as you, while you're logged on, so it uses your own Windows login for
SQL Server and can reach your mapped drives, the same as when you run `pdcat scan`
yourself. If the laptop is off at the time, tick **Run task as soon as possible after
a scheduled start is missed** in the task's **Settings** tab and it runs when you're
back.

**Checking on it**

- `scan.log` has every run, with the date and the result.
- `schtasks /Query /TN "pdcat weekly scan" /V /FO LIST` shows the last run time and its
  result (`0` worked, `1` something failed, `2` couldn't start; see [Exit codes](#exit-codes)).
- In the app, the sidebar shows when each source was last scanned.
- Run it now: `schtasks /Run /TN "pdcat weekly scan"`. Remove it:
  `schtasks /Delete /TN "pdcat weekly scan"`.

The scan goes as gently as a manual one: one SQL query at a time with the pause, time
limit, per-run cap and quiet hours from the **Settings** page. It never deletes
anything. You can leave the app open, but pick a time when you won't be editing in it:
saving a note while a scan is writing can fail with "database is locked" (save again
once the scan finishes).

## Exit codes

For scheduled tasks and scripts: `0` means it worked, `1` means it ran but something
failed (a source didn't scan, a table didn't profile), and `2` means it couldn't start
(no config, an unknown source name, nothing to profile).
