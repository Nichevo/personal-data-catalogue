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

**ML model features**: the inputs to your machine learning models, filled from your feature code and LightGBM
models, with checks for leakage, unused features and drift:

![ML model features](docs/screenshots/features.png)

### Review

**To review**: a prioritised list of what needs your feedback, worked out after every
scan. Tick rows to mark them done, snooze or dismiss them:

![To review page](docs/screenshots/review.png)

**Coverage**: how well documented the catalogue is, and what to document next (with a
draft worked out from the scan):

![Coverage page](docs/screenshots/coverage.png)

**Gold standards** (version 2.6): reports, measures and SQL you marked as the one to trust, and
the related copies, same-named measures and look-alike queries that differ from them.

**Change tracking** (version 2.9): every change in one list with filters, grouping and saved views; graphs of
table growth, files in folders, report changes and catalogue growth; and your change-tracking files linked to
the tables they track, with changes by column.

**Issue hotspots** (Review › Health): which schemas, folders and tables have the most
problems:

![Issue hotspots page](docs/screenshots/hotspots.png)

### Map

**Code map**: how your projects and home-built libraries connect, and the tables and
files they reach. Zoom, open and close groups, click a box to open it, and download as
PNG, SVG or Mermaid:

![Code map page](docs/screenshots/codemap.png)

**Data flow**: an ETL-style picture of where data comes from (APIs, scraped web sites,
database connections, files, cloud storage, SharePoint, SAP) and the path it follows
through your scripts and reports to the tables and files they write:

![Data flow page](docs/screenshots/dataflow.png)

**Power BI map**: every Power BI report, drawn bigger the more pages, visuals and measures
it has, with the tables and files it loads, the scripts that write those and where their
data comes from. Older versions are folded into the current report, and reports that load
nothing catalogued (or couldn't be read) are flagged. A table lists the same reports with
their counts, as CSV too.

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

**Data folders** (Add › Sources): point it at any folder of CSV, Parquet, Power BI and
Excel files:

![Data folders page](docs/screenshots/folders.png)

**SQL scanning** (Add › Sources): tables are only profiled when you ask. Approve them here; queries go
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

## New in version 2.28

**Drill into the subqueries of a SQL query, and open any subquery as SQL of its own.**

- **Subqueries on the field diagram.** A query with CTEs (`WITH recent AS (...)`), derived tables
  (`FROM (SELECT ...) x`) or subqueries in WHERE, SELECT, IN and EXISTS now draws each one as a box that opens with
  a click (or **Open all**). The tables a subquery reads sit inside its box, nested as in the SQL, so a subquery
  inside a derived table sits inside that derived table's box. The rest of the query is the *Outer query* box.
  Subquery boxes start closed; each says how many things are inside it.
- Each box shows what the subquery **gives** back (its columns), a CTE points to the part of the query that **uses**
  it, and a derived table is joined to the outer table it matches on (`Material = Material`). A table read in two
  places is drawn in both. CTE names are no longer drawn as tables.
- **Subqueries (N)** list under the diagram, on query pages, pasted SQL pages and view / procedure pages: each with
  its tables, the CTEs it uses, the columns it gives, and its SQL.
- **Open on its own** gives a subquery its own page: its SQL with the CTEs it needs put in front as a `WITH`, so it
  reads on its own; a warning when it still refers to the outer query (a correlated subquery, e.g. `p.Material`);
  its tables and fields, field diagram and checks; **Download .sql**; and **Save as its own SQL**, which keeps it like
  pasted SQL, with its own name, notes and place in the query library.
- Read from the SQL text, so it works with or without sqlglot, and nothing is run. Comments and text in quotes are
  ignored. Nothing needs rescanning.

## New in version 2.27

**Choose which reports a gold standard is compared with, and mark Power BI reports as old.**

- **Old versions are left out by default.** A gold standard is now compared with current reports only: older
  versions in a Versions group, reports marked 🗄️ old or 🙈 not relevant, and reports in a folder whose role is
  archive are no longer flagged as "differs". Before, a gold report was always compared with its own older versions.
- **🎯 Compare with**, on every gold standard's page: *Current reports only* (the default), *All reports, old
  versions too*, or *Only the reports I pick*, plus a list of reports to leave out. For a gold measure the list is
  the reports holding the other measures; for SQL, the related queries and views. The box says how many related
  reports are not compared.
- **🚫 Don't compare this report** and **🗄️ Mark report old** buttons on each item that differs, so a stray
  archived copy can be dropped in one click. The first only affects that gold standard; the second marks the report
  old everywhere.
- **🗄️ Mark old now means "old version" everywhere**, for Python scripts, notebooks, tables, views, data files and
  Power BI reports alike. The item keeps its notes and still shows in its Versions group and on its own page, but is
  left out of search, the Find lists, the code map, data flow, the Power BI pages and gold standard comparisons, the
  same as an older version in a Versions group. Tick **Show older versions** (on the Tables page: **Show old
  tables**) to list them; ↩️ Not old on the item's page brings it back for good.
- **Data source differences are checked.** A measure with the same DAX now differs when a table it reads (directly
  or through the measures it builds on) loads from another server, database, table, file, folder or native SQL,
  e.g. "Sales loads from somewhere else", with the two sources side by side. A gold report gets a *Data sources*
  section for every model table that loads from somewhere else in the other report. Power Query parameters (a
  ServerName parameter, say) are filled in first, and queries a table builds on are followed.
- **The gold standard is green, the item that differs is red** in the line-by-line difference view (before, a plain
  diff coloured the gold standard's lines red).
- Leaving reports out also takes them off Review › Gold standards (new *compared with* and *not compared*
  columns) and the To review list. Every change can be undone from the gold standard's edit history, and the
  gold standards export has a new *scope* column.

## New in version 2.26

**Change graphs go per day, week or month, never per hour.**

- **Graphs no longer show hourly blocks.** Table growth, files in folders, catalogue growth, report changes, change
  volume, tracked changes and a table's "Rows over time" put readings into days, weeks or months. A line shows the
  last reading in each day, week or month; a bar adds up the changes in it, and quiet ones show as gaps. Axis
  labels are dates only.
- **A "Per" choice on Review › Change tracking › Graphs**: Auto, Day, Week or Month. Auto picks days for up to about
  three months of history, weeks up to about a year, then months, so the graphs stay readable as history grows. The
  caption under each graph says which one is in use.
- Weeks start on Monday. Scans still show one dot per scan (the tooltip keeps its time), on a day/week/month axis.

## New in version 2.25

**Profiling SQL Server keeps going when a table or column can't be read.**

- **Text that isn't UTF-8 no longer stops a table.** Characters such as "š" or "é" in `char`/`varchar` columns can
  reach Python in the Windows code page, which failed with "'utf-8' codec can't decode byte 0x9a". Text is now read
  as UTF-8 when it is, otherwise in the Windows code page, and any byte that still doesn't fit becomes "?".
- **A column the server can't read is left out, not the whole table.** A view that casts bad text to a number
  ("Error converting data type nvarchar to numeric") fails on any query that reads that column. pdcat now checks
  whether the table can be counted at all, then reads the columns in halves (and halves of halves) to find the ones
  that fail. Everything else is profiled; the failing columns keep their place with "could not be read: …" in the
  type check column, and the profile's method line says how many were left out. It sends at most 40 extra queries
  per table, at the usual pace, and a time-out still stops the hunt.
- **One table or database never stops the run.** A table that can't be read at all is marked *failed* with the
  server's message and the run moves on; a database that can't be reached fails only its own tables.
- **Choose how many tables one run profiles.** A "Tables this run" box next to ▶️ Run on the SQL scanning page, and
  `pdcat profile --queue --max 800`, let one run take more (or fewer) tables than the "Most tables profiled per run"
  setting, without changing it. Queries still go one at a time with the usual pauses.
- Nothing about this changes what is sent: still read-only `SELECT`s with `NOLOCK`, logged in the query log.

To profile tables that failed before, approve them again on the SQL scanning page (✓ Approve all includes failed
tables), or name them: `pdcat profile dbo.SalesDaily`.

## New in version 2.24

**Every table a write function is used for is found.** When one function builds the SQL from the schema and table
it's given, for example `sql = f"MERGE [{schema}].[{table}] ..."` then `cursor.execute(sql, tuple(...))`, and it is
called three times with three tables, only the first call's table was found. Now:

- Each call counts, in the same file and from other scripts. A script that calls a shared upload function in
  another file of the project writes the table it passes in, not the table worked out inside the shared file.
  Each call's schema stays with its own table (never `dbo` from one call with the table of another).
- The shared function itself no longer hands one table to every script that imports it.
- Loops over a list of tables in the code count too: `for table in ["Sales", "Stock"]: df.to_sql(table, ...)`
  and `for name, df in frames.items(): upload(cur, "dbo", name, df)`.
- `if`/`else` branches that pick a table name give both tables for `to_sql`.

**More write statements are recognised** in SQL inside Python: `INSERT dbo.Sales (...)` without `INTO`,
`DELETE dbo.Sales WHERE ...` without `FROM`, `BULK INSERT`, `DROP TABLE`, `ALTER TABLE`, and ODBC
`{CALL dbo.LoadSales (?)}`; also class-level names (`TABLE = "dbo.Sales"` then `self.TABLE`), polars
`write_database`, Spark `saveAsTable` / `insertInto`, and SQLAlchemy `insert(sales)` / `sales.delete()` on a
`Table("Sales", meta, schema="dbo")`. A script that runs a `.sql` file (`cursor.execute(open("load.sql").read())`)
now shows **Runs** that SQL file.

Rescan your Python projects to pick these up. The tool still only reads your code; nothing is run.

## New in version 2.23.1

**`pdcat profile --queue` says why nothing ran.** It only runs tables that have been approved, but tables a
profiling rule or *Queue them now* adds wait for your approval first, so the command could finish with
"Profiled 0 table(s)" and no reason. It now tells you how many tables are waiting for approval, and
`pdcat profile --queue --approve` approves them and runs them in one go. Tables skipped because their SQL Server
source isn't in your `config.toml` are now counted too.

## New in version 2.23

**Profiling no longer fails after you filter the list.** On Add › Sources › Profiling, ticking tables and then
typing in *Contains*, changing a filter, or setting a rule that took tables out of the list could stop the page
with "list index out of range": the ticks were kept for rows that weren't there any more. The ticks are now
cleared whenever the list shown changes, so a rule is never set on tables you didn't tick. The SQL scanning queue
and the To review table work the same way, and every other table you can pick a row in now ignores a stale pick
instead of failing.

## New in version 2.22

**Data flow opens quickly.** Map › Data flow no longer draws the whole catalogue when you open it, which was too
slow on a big catalogue. It now waits for you to pick a project or folder in **Start from**, and the diagram and the
*Where data comes from* table then cover just that project or folder (still followed upstream and downstream).
To draw everything, pick **Everything** at the top of the list.

**Tables with dots in their name are found.** A table whose own name holds dots, such as `dbo.[FAX.FAXX.Sales]`,
was scanned but never matched to the scripts, SQL files, views and Power BI queries that use it, because the name
was read as database `FAX`, schema `FAXX`, table `Sales`. Names are now tried every way they can be split, so these
links are made. If a name could be either of two scanned tables, the link is left for you to pick rather than
guessed. No rescan is needed: links are matched again at the end of your next scan.

## New in version 2.21

**ML opportunities** (Knowledge › ML opportunities): machine learning projects your data could support, how ready
each one is, and what a data scientist would need to start. Everything comes from what your scans already found.
Nothing is run against your servers and nothing in your data or tables is changed: where a figure needs a query,
the page gives you read-only SQL to copy and run yourself.

- **12 supply chain recipes**, checked against your catalogue after each scan: demand forecasting, safety stock,
  lead time, stock-out and OTIF risk, supplier risk, master data anomalies, slow movers and obsolescence, order
  promising, warehouse labour and slotting, returns, price and promotion effects, and transport and freight cost.
  A **planner assistant (AI agent)** recipe is there as an example of a different kind of tool. Each recipe looks
  for its columns by SAP field name and by friendly name (`EBELN` or `PurchaseOrderNumber`), and the page shows
  both, so you can always find the original. Switch recipes off, change them or add your own under the Recipes tab.
- **Readiness score** out of 100 from six checks: a target to predict, history depth, grain, features that join,
  freshness and business pain (Power BI measures that already show the problem). Mark a check Confirmed or
  override it. How much each check counts is set under Settings › ML opportunities (target and history count
  double by default; two years of history counts as enough).
- **+ New opportunity** needs only a name. Pick the kind of tool (forecast model such as LightGBM, a yes or no
  prediction, anomaly detection, optimisation, an AI agent, or rules first), the status (idea to live), owner,
  value and effort, then add tables and columns with their role (target, feature, key, date).
- **Splits**: split an opportunity into Target A and Target B (lead time from order date, lead time from
  confirmation). A split copies everything at the time of the split, then goes its own way.
- **Business projects**: link an opportunity to a business project; the project's tables that share key columns
  are suggested, and the project's page lists its opportunities.
- **Related Power BI measures** with how complex each is (Complex, Medium, Simple from the DAX). Tick the ones that
  are related; **Use as target** gives a SQL view script to copy, nothing is created.
- **Baseline to beat**: when a plan and an actual column are found (planned and actual lead time, forecast and
  sales), a read-only SQL to work out today's error, and a box to type the figure in.
- **Warnings**: columns that may leak the answer (filled in only after the event), and target columns with many
  blanks or drifting values (from profiles).
- **Value and readiness** chart, **Shared tables** (tables many opportunities need, worth getting right first)
  and **feature ideas** (tables that join to the target but no script reads yet).
- **Data scientist pack**: a zip with a README, the tables, columns (with SAP names), joins, related measures and
  starter and baseline SQL.

**Table types**: tables and datasets can be marked **Snapshot** (the same rows copied on each date, such as stock
on hand every day), **Transaction** (one row per event) or **Dimension** (one row per thing). A type is suggested
after each scan as Likely or Check with its reason; accept it under Suggestions, or set it on the table's page or
in Bulk edit. Find › Tables and datasets has a table type filter, and a snapshot table counts as history for an ML
opportunity whose target keeps only today's values.

No new libraries are needed.

## New in version 2.10

**Slow SQL** (Review › Slow SQL): every query, view and procedure the catalogue found that may run slowly, in one
place. Nothing is run and nothing connects to a server: these are patterns in the SQL text, checked against what
your scans already know (row counts, column types, which views read which).

- **Worst first**: queries are ordered by the biggest table they read (row counts from your SQL Server scans),
  then by how many slow checks they trip, so a `SELECT *` on a 50-million-row table comes before one on a lookup
  table. Pick a check to see only those, and pick a row to open the query with each check's reason and fix.
- **By check**: one group per check with how many queries have it, "Fine here" per query and "Accept everywhere"
  for a pattern that's fine in your SQL.
- **CSV download** of every finding (query, where it is, check, reason, biggest table, rows).
- **New slow checks**:
  - a subquery in the SELECT list (runs once per row);
  - a user function such as `dbo.fnClean(x)` in the column list, WHERE or JOIN (also once per row);
  - `OR` across different columns in WHERE, and any `OR` in a join (stops index seeks);
  - `ORDER BY` inside a view or subquery, including `TOP 100 PERCENT ... ORDER BY` (sorts for nothing);
  - joining on joined-up values (`a.x + a.y = b.z`);
  - `COUNT(*) > 0` used to test for rows (`EXISTS` stops at the first);
  - very long `IN (...)` lists (50 values or more);
  - query and index hints (`WITH (INDEX(...))`, `FORCESEEK`, `HASH JOIN`, `OPTION (MAXDOP ...)`...);
  - a text column compared with a number (`WHERE StoreCode = 1234` on a varchar column), using the scanned
    column types;
  - views built on views three or more levels deep, with the chain shown (`vSales → vSalesBase → vRaw`);
  - a big table read with no `WHERE` or `TOP` (1 million rows by default; change it under Settings on the page).
    View definitions are left out, since the query reading the view usually filters it.
- The SQL Server good-practice pack (Knowledge) has a new article on these patterns; load the pack again to add it
  (your own edits to its articles are kept).
- **Find › SQL queries** has a **Check** filter: one check, or any wrong-result, risky or slow check.
- Slow findings still stay off To review (it lists wrong-result and risky ones), so it doesn't fill up.
- No rescan needed: saved check results are worked out again for every query the first time you open a list.
- Maybe later, as a separate opt-in idea: reading real execution plans or Query Store from SQL Server.

## New in version 2.9

**Change tracking** (Review › Change tracking): every change in one place, with graphs over time and your own
change-tracking files linked to the tables they track. Nothing needs rescanning: the first time you open it,
the change history you already have is turned into readings for the graphs.

- **All changes:** what scans found (new, dropped and changed items and columns), report and workbook changes,
  row counts, your edits, git commits, tracked changes on the columns you watch, and profiles that moved a lot,
  as one list. Filter by when, what, where, kind of item, business project, tag, report group, gold standards
  or followed items, and by any word. **Hide noise** (on by default) leaves out items marked not relevant or
  old, older versions in a version group, and row counts that moved less than 5%. Group by one or two of day,
  week, month, what, change, kind, where, item, business project, folder or column. Save a set of filters as a
  view, and download what you see as CSV. Click a row to open the item.
- **Graphs:** table growth (pick tables; jumps of more than half up or down are marked, with the biggest
  growers and shrinkers), files in folders (files and size per folder over time, files added and per week,
  including files only listed in history folders and log folders), report changes per week by part (pages,
  visuals, measures...), catalogue growth by kind of item and how many are described, scans (time taken and
  items found), change volume per week, and tracked changes.
- **Change trackers:** files and tables that record changes to another table are found by name (a prefix you
  set, `ChangesX_` to start with, so `ChangesX_TABLEPlant.csv` tracks `TABLEPlant`; or words like changes,
  audit, history and `_CT`) and by their columns. Two layouts are understood: one row per changed value
  (`PlantID, ColumnName, ValueBefore, ValueAfter, FieldName, Table, ChangeDate`, SAP CDPOS, audit logs) and
  copies of whole rows with a change date or operation (CDC, history tables, snapshots). Each is linked to the
  table it tracks from the name after the prefix, its Table column, a key both share (PlantID) and the columns
  it reports, as a suggestion you confirm or reject (or link by hand, or mark "Not a tracker"). Scripts that
  write a tracker are shown as "Made by".
- **Read change summary** counts each tracker's changes per day, column and kind: an empty value before is a
  new addition, an empty value after a removal. Files are read in chunks; after the first read only rows from
  the last change date onwards are read again, so a long file that keeps growing stays quick, and file
  trackers are brought up to date after each scan. A SQL tracker gets one paced, read-only GROUP BY (in the
  query log), only when you press Read. Only counts and the keys that changed are kept, never the values.
- **Changes by column:** changes, share, changed / new / removed, keys affected and the last change for each
  column, with the FieldName shown next to SAP-coded column names (and offered as the column's business
  name in Suggestions). Star the columns you care about: watched columns feed All changes, the graphs and the
  table's History tab. Until you pick, the two that change most are watched.
- A tracker not linked to a table yet is on To review.

**Item pages:** the History tab shows rows over time and the changes trackers recorded for the table, with
a way into Change tracking. The Columns tab has a **Profile history** box: each profile is now kept in a
compact form when it moves (rows, nulls, distinct values, min, max, mean), so you can see how a column moved.
The profile you already had is kept as the first one.

**Home:** "Since your last visit" counts the changes since then, by kind, from the same list.

**Profiling** (Add › Sources › Profiling): every SQL Server table with its rows, whether your code uses it,
when it was last profiled and how many profiles are kept. Tick tables and give them a rule: once, weekly,
monthly or never. A rule only puts a table in the queue when it's due (checked after each scan); the queue
still waits for your approval under SQL scanning, at the same careful pace. Profiles that moved a lot in the
last 90 days are listed (null share up or down 10 points, distinct values up or down by half, a latest date
that stopped moving).

**Database size** (Settings › Database size): the catalogue file's size, what takes the space, a rough yearly
estimate for change tracking, and **Tidy up**: every reading is kept for 90 days, then one a week to a year,
then one a month (both can be changed), the query log keeps its newest 20,000 queries, and old row-count lines
in the change history are thinned to one a month, then the file is compacted. A warning shows over 1 GB.
Change tracking adds roughly 20 to 70 MB a year to a catalogue the size of yours before thinning.

**Export and Copilot pack:** the export has row counts over time, scan counts, profile history, change
trackers and tracked changes (counts only), and profile rules. The Copilot pack has a change-tracking page:
which tables have a tracker and which columns change most.

## New in version 2.8.3

A review of everything changed since 2.11, before building 2.9. No new features; the app is quicker on a big
catalogue and twenty small bugs are fixed. Nothing needs rescanning.

**Quicker** (timed on a made-up catalogue of 2,900 tables and views, 2,300 pieces of SQL and 3,300 files):

- **After-scan steps take half the time:** 66 s → 30 s on a rescan, 140 s → 98 s on the first scan.
  - SQL checks are saved, so a rescan only checks SQL that is new or changed (28 s → 4.5 s). They also no
    longer look up each column's type and profile one query at a time.
  - Finding descriptions in SQL comments skips lines with no comment (13 s → under 2 s).
  - Finding metrics in SQL no longer runs a pattern on every character (14 s → 7 s).
- **SQL queries page:** 16 s → 2 s, using the saved check results.
- **Database diagrams:** 11 s → 5 s. Each table's keys are looked up once, not by going through every
  relationship for every table.
- **Gold standard boxes** on measure and report pages read every measure once per change to the catalogue,
  not on every click.

**Fixed:**

- Script subway maps: functions that call each other could be hidden as "no data" helpers, depending on the
  order Python happened to visit them. Functions at the edge of a focused map, whose calls weren't followed,
  are no longer called helpers either.
- Web and cloud data sources: a rescan kept the old addresses and dropped new ones once ten had built up.
  The ten-address cap now holds when two lists are merged.
- Functions reading a web address (`pd.read_csv("https://…")`) no longer show it as a file on the function
  map.
- Home-built loaders: an entry with a `file` key no longer turns into two modules, a `module_path` to the
  file now tells copies apart by its folder, and a label such as "Management Tools" isn't taken for a
  module. When two same-named copies can't be told apart, calls aren't linked to whichever was scanned
  first (the script page already said they weren't).
- Metric suggestions: names like "New customers", "Temperature" or "Sales 2024" are no longer put under
  Unlikely as working columns, and what you accepted or rejected before now wins over that rule.
- The Kind of metric grouping matches whole words, so "Sales percentage" isn't Time, "Latest forecast"
  isn't Service, and "Returns" isn't Stock.
- When pbixray is installed but can't read a report, the report says so, instead of asking you to install
  pbixray.
- A top-level-only folder scan no longer counts the files in log folders below it.
- In trees of dated day folders, a folder made ahead of time (or mistyped, like 20261230) no longer counts
  as the newest, which had left the real recent folders unread. Quarter folders (2026Q2, Q2 2026) are
  recognised.
- Gold standards: undoing "Remove gold standard" brings back its "OK to differ" answers. Review text keeps
  measure names and DAX as written (it lower-cased them).
- Power BI model tables and queries with a "/" in their name aren't split into a file name and folder.
- A database or file whose name has `_` or `%` in it can no longer reach into another with a similar name
  when it is rescanned or kept.
- History folders and the Archive folder role use one list of names (prev, prior, past and older were
  history but not archive).
- A scan now counts as a change, so pages cached by catalogue revision refresh after it.

## New in version 2.8.2

- **The counts at the foot of the sidebar open their lists.** Click "🗃️ 120 table" to go straight to
  🔎 Find › Tables and datasets, "🧮 300 measure" to Find › Power BI on the Measures view, and so on for
  Python, Power BI reports, model tables, Power Query, the report register, SQL queries, packages, conda
  environments, business projects, knowledge, questions, metrics, ML features and keys. It works from an
  item page too. Counts with no list page of their own stay as plain text underneath.
- The 📝 notes and 📋 to review counts open What's new › Recent notes and To review.

## New in version 2.8.1

- **Daily dated folders aren't all walked.** When a folder's subfolders are named by date in any format
  (`2026`, `05 2026`, `2026-05`, `May 2026`, `20260514`, or `2026/05/14`), and the deepest of them are days,
  the scan finds the newest day with a few folder listings and walks only the last 31 days of folders before it
  (Settings › Data files). Older dated folders are not opened at all; the scan says how many there were and
  from when to when. Trees of month or year folders are small and are still walked in full.
- **Log folders are counted, not read.** Folders named `log`, `logs`, `logging`, `logfiles` and the like get a
  count of their files and the first and last modified dates; nothing inside is read. Each shows up as a
  "Log folder" item. Switch it off in Settings.
- Files that an earlier scan found in folders that are now skipped are kept, not marked missing.
- The folder's card lists the folders that weren't opened. "Read every file" walks and reads everything.

## New in version 2.8

Folders that keep every past file (weekly reports saved as
`Files/2026/05 2026/20260414/archived/converted_REPORT20260414.xlsx`, thousands of them) took hours to scan,
because the date sampling from 2.14 only ever covered CSV and Parquet files: every Excel workbook was opened.

- **Workbooks and Power BI reports are sampled by date too.** In a big folder (more than 2,000 files of any
  kind) workbooks and reports that differ only by a date become one **dated series** across the whole folder
  tree, however deep the dated subfolders go. Only the newest of each series are opened; the rest are listed
  by name, size and modified date. The series page shows the period covered, missing periods and every file,
  with buttons to open the newest ones.
- **History folders.** Folders named `archive`, `archived`, `old`, `backup`, `history`, `previous`,
  `superseded` and the like are treated as history at any depth: in a dated series only the newest file is
  opened, the rest are listed. Give a folder the role **Archive / history** in 📂 Folder roles to make it
  history, or any other role to have it read in full. Switch name matching off in Settings.
- **Dated folders are understood better.** Month folders with a space (`05 2026`) are read as dates, and a
  deeper dated folder (`20260414`) wins over the ones above it, so these files form one series.
- **A cap on workbooks and reports opened per scan** (300 by default, Settings). New or changed ones beyond
  that are left for the next scan, newest first, and the next scan carries on where this one stopped.
- **You can see what was skipped.** Before reading, the scan says how many files will be listed and why;
  afterwards the folder's card shows each history folder with its file count, file types and date range.
  "Read every file" on the folder's card turns all of this off for that folder.
- A workbook read in an earlier scan that is now only listed keeps what was found, rather than being marked
  missing.

No rescan is forced: the next scan of a big or history folder is simply much faster.

## New in version 2.7

- **Long suggestion lists come in groups.** With thousands of SAP meanings or metrics found in code,
  Suggestions now opens on a short list of groups instead of the rows (lists over 200). Pick how to
  organise them with **Organise by**:
  - SAP meanings: column name (“Plant → WERKS”), SAP field, SAP area (sales, purchasing, planning,
    warehouse...), table or file, folder or database.
  - Metrics found in code: first word of the name (Sales…, Forecast…), kind of metric (forecast accuracy
    and error, service, stock and coverage, money...), the script, report or query it was found in, its
    folder, and where it was found (Python, SQL, Power BI).
  Each group shows how many are likely, to check or unlikely, with **Open** to look through it and tick
  the wrong ones, **Accept N** / **Reject N** for the whole group, and **Likely N** to accept only the
  likely ones in it. Each asks once before it does anything. **List** gives the old single list.
- **Repeats merged.** The same SAP meaning on many tables (a Plant column meaning WERKS in 300 tables)
  is one row, “the same suggestion on 300 items”, and accepting or rejecting it does all of them.
  Switch **Merge repeats** off to see them one by one.
- **File names apart from their folders.** Search results, the Find lists (Python, Power BI, Tables
  and datasets) and Power BI model tables, queries and columns now have **Name** (the file name only),
  **In** (the report a measure or table is in, file name only) and **Folder** (shortened to its last parts,
  “…/Monthly/Archive”). Switch on **Full folder paths** to see the whole path. Measures across reports
  shows report file names with their folders in a column of their own, and item pages show the file name
  as the title with the folder underneath.
- No rescan needed. SAP suggestion titles lose their folder part after the next scan.

## New in version 2.6

- **Gold standards.** Mark a Power BI report, a measure, an SQL query or a view as the one to trust
  (**☆ Mark as gold standard** under the item's title, with an optional note on why). Related items are
  then checked against it, and the ones that differ say so:
  - a **measure**: measures with the same name in other reports, and measures with the same DAX under
    another name. Same DAX is followed one step down too: `[Total Units] / 7` in two reports only gives the
    same number when `[Total Units]` is the same in both. The difference shows as a line-by-line DAX diff,
    plus a different format string.
  - a **report**: its other versions and copies that share most of its pages, measures, tables and
    sources, with the measures, tables, sources and pages that are missing, extra or changed. Every
    measure in a gold report counts as a gold measure too, so the same-named measure in any other report
    is checked against it. A measure you mark yourself wins over a gold report's.
  - **SQL** (a query from the SQL library, a view or a procedure): SQL with the same name you gave it, or
    that reads mostly the same tables and reads alike, with the tables, columns, joins and filters (WHERE)
    that are missing or extra, and a text diff.
- The gold item's page lists what matches and what differs. The other item's page shows
  "Differs from the gold standard …" with how, and a link to the standard.
- **Review › Gold standards** lists them all with counts. **To review** gets a new check,
  "Differs from the gold standard". **Measures across reports** has a "⭐ Make this the gold standard" button
  for each calculation of a clashing measure.
- **OK to differ**: a difference that is on purpose can be marked so. It shows again when that item changes.
- **The standard itself changing** is flagged ("changed since it was marked", with the diff) until you
  accept it as the new standard.
- Gold standards and OK-to-differ marks are in the JSON and CSV exports, and marking one can be undone from the
  item's edit history. No rescan is needed.

## New in version 2.5.4

- **Table descriptions from the names a table is given elsewhere.** A suggested purpose for an
  undescribed table used to come only from its own name, so `T_MAT01` became "Mat01". Now it also
  uses the names the table is called by in your code and reports: an alias in SQL
  (`FROM dbo.T_MAT01 AS Materials`) and the Power Query that loads it in a Power BI report or Excel
  workbook ("Stock on hand"). The name used most often wins, and the To review item lists the others
  under "Called elsewhere". One-letter aliases, initials (`dp` for `dim_plant`) and generic words
  (`src`, `cte`, `Query1`) are left out.
- **What its columns are renamed to is added.** `SELECT MATNR AS Material, WERKS AS Plant` gives
  "Has material and plant". Abbreviations from Knowledge › Abbreviations are spelled out. No rescan
  needed: this uses what earlier scans already found.

## New in version 2.5.3

- **Calls far down a long script are found.** Only the first 60 different calls in a function (or in a
  script's top-level code) were kept, so in a long script a call like `model_configs = train_sub_models()`
  near the bottom could be missed. Up to 500 are kept now. Rescan your Python projects
  (`pdcat scan --force`) to pick this up.
- **Loaded functions stay on the map.** With helpers hidden, a function from another file (such as one
  brought in by ModuleManager) was counted as a helper when it reached no table or file one level
  down. Those functions are often a script's main steps, so they now only count as helpers by name or
  when used from three or more places.

## New in version 2.5.2

- **ModuleManager dictionaries found in more shapes.** Scripts that load functions with a home-built
  loader fed a dict of modules and functions (no import lines) are now read when the dict:
  is inside a function such as `main()`; is passed straight into the loader's call; is built with
  `dict(...)`; is filled in one module at a time (`modules["Management"] = {...}`); or is a list
  like `[{"module": "Management", "functions": [...]}]`. The function list can be a list, a
  `"f1, f2"` string, a dict of names, or a variable holding one; any key with "func" in its name
  counts (functions, funcs, function_list, funcs_to_load...). A module given as a file path
  (`C:\code\tools\Management.py`) is matched by its file name. When the loader's result is kept
  (`mm = ModuleManager(modules_to_load)`), calls like `mm.function1()` count as calls to
  function1. Rescan your Python projects (`pdcat scan --force`) to pick this up.
- **The right copy of a loaded module.** When several scanned files share the loaded module's name
  (a live copy, a backup folder, an old version), the one in the dict's `local_folder` /
  `sys_folder` now wins, then the current version. Before, whichever copy was scanned first was
  used, so calls could link to the wrong file or to none.
- **Helpers or process steps.** Inside this script (subway map and boxes) now hides helper
  functions by default, with a **Show helpers** switch. A function is a process step when you
  zoomed into it, nothing calls it (main, the script's own code), or it reads or writes a table or
  file itself. Of the rest, helpers are those that never lead to a table or file, are used from
  three or more places, or are named like helpers (`_private`, `get_`, `is_`, `format_`, `parse_`,
  `log`...). Each step's card lists the helpers it uses ("+2 helpers: _clean_name, log_step") and
  takes over anything they read or write, so no table or file drops off the map. A list under the
  switch says why each one counted as a helper.
- **Loaded by a helper.** A script's **Inside** tab lists each module its loader brings in, the
  functions, the folders, and the scanned file it was matched to, or why not: no file of that name
  was scanned (add the folder as a Python source with role library), or several were and none sits
  in the named folders.
- **Table diagrams in a ring.** On a table's or view's page, the diagram now puts the table in the
  middle with the tables it joins to around it (up to 12 on an inner ring, the rest on an outer
  one) instead of one tall column. Diagrams with three or fewer neighbours keep the old layout.

## New in version 2.5.1

- **Loops on the function subway map.** A function's card on Inside this script › Subway map now
  says "↻ in a loop: load, clean" when it calls those functions inside a `for` / `while` loop or a
  comprehension (a call in the loop's own header, like `for x in load():`, runs once and doesn't
  count), or "↻ 2 loops" when its loops call nothing else. A function that calls itself gets
  "↻ calls itself" and a dashed loop beside its stop. Rescan your Python projects once to fill this
  in (Add › Sources, or `pdcat scan --force`).

## New in version 2.5

- **Tidier item pages.** Every item page (table, view, file, script, report, folder, project) now
  has a line of counts at the top ("3 to review", "4 notes", "42 columns", "read by 14", "joins 6")
  and the same tabs: **Overview** (purpose, To review, notes, About this item, suggestions),
  **Columns** or **Contents**, **Connections** (uses, made by, read by, joins, called by, SQL using
  it, diagrams and data flow), **Inside** (functions, SQL in the file, report pages, measures and
  model, sheets, environment) and **History** (over time, change log, edit history, details). Tabs
  with nothing in them are left out. Only the open tab is drawn, so busy pages open faster. Click a
  count to jump to its tab. The app remembers the last tab you used for each kind of item, so if
  you always check a table's joins, every table opens on Connections. A link to a function opens
  the script on Inside.
- **Long lists cut to 10.** Read by, Joins to, Uses, Created or loaded by, Called by, To review,
  report pages and a project's contents show the first 10, with a filter box and **Show all**.
- **Deciding many suggestions at once.** Suggested SAP meanings, suggested metrics, every list on
  the Suggestions page, suggested company-wide keys and the columns that probably carry a key now
  work the same way: filter, tick only the few that are wrong, then **Accept all except ticked**
  (it asks once to be sure and shows progress). You can still tick a few and accept or reject just
  those. Long lists show 50 at a time.
- **Likely, check or unlikely.** Each suggestion is marked 🟢 likely, 🟡 check or 🔴 unlikely with
  a one-line reason, and you can show one band at a time, so you can accept all the likely ones in
  one go and look properly at the rest. The marks use how strong the match is (a column named
  exactly WERKS is likely; one that only contains it needs a check), what you decided before (if
  you rejected Plant_Name as WERKS once, the next Plant_Name is unlikely), and for metrics how
  many places a calculation is found and whether its name looks like a working column. Key columns
  are marked by whether their data type matches the columns already confirmed. A **Group** picker
  narrows the list to one SAP field, one kind of metric or one key.
- **About page: high level or detailed.** The app map on Home › About has a switch. **Detailed**
  draws one stop per part of the code, named module.function (for example
  `scan_sqlserver.scan_server`), so you can see which piece does what and find it. A test checks
  every name on the map still exists, so the drawing keeps up with the code.

## New in version 2.4.1

- **Inside a script as a subway map.** On a Python script's page, under **Inside this script**,
  pick **Draw as: Subway map**. The functions run top to bottom in call order (a function sits
  above the ones it calls). Each function that nothing else in the view calls starts a coloured
  line through everything it calls, so a helper used from two places is an interchange where two
  lines meet. Tables and files are lines too, as on the data flow map. Works for the whole script
  and when you zoom into one function (that function gets a yellow card). Laid out automatically;
  click a table, file or another file's function to open it; the map downloads as SVG.
- **Script versions grouped automatically.** Scripts in one folder whose names differ only by a
  version or date at the end (forecast_v2.py, forecast_v3.py, forecast_old.py) are now grouped
  without waiting for a yes, like reports in 2.17. The current one is the one other code uses or a
  schedule runs, else the newest; when two versions are both in use it stays a suggestion. Older
  versions no longer show on the code map or the data flow (boxes or subway map). Data flow has a
  **Show older versions** switch that draws them too, labelled "(older version)" with a grey card
  on the subway map; an older version's own page still shows its flow. Functions from an older
  file on the inside-a-script view are labelled the same way. Change the current version or break
  a group up on the group's page; the Settings switch for script versions turns it off.
- No rescan is needed: versions are grouped once when the app opens.

## New in version 2.4

- **About page** under Home: what the app is, its version, a subway map of how its parts fit
  together (what you bring in, the parts of the app that use it, and what comes out), and the
  release notes from this README, newest first.
- **Data flow as a subway map.** On Data flow, pick **Draw as: Subway map**. Each table, file or
  outside source is a coloured line running down the page through the scripts and reports that
  use it, in run order, laid out automatically. A table that a script reads and writes back makes
  a loop, and row counts show on table lines. Click a name to open it; the map downloads as SVG.
- No rescan is needed.

## New in version 2.3

- **Power BI impact, from the files alone.** Nothing calls the Power BI service. A *thin report*
  (a live connection to a published dataset, no model of its own) is matched to the scanned
  .pbix or .pbip that holds that model by the dataset's name, by the report register's dataset
  column, or by hand on the report's page. A model's page lists its **connected reports**, and
  every measure and column is looked up across the model and those reports: which visuals show
  it, which measures and calculated columns need it (every step), and what would change with it.
  A measure's page shows the chain both ways. Model table pages and the new **Power BI › Columns**
  list say what uses each column.
- **"Not used in scanned files".** Measures, model tables, columns, Power Query queries and
  database tables and views that nothing scanned reads or shows are hidden behind a
  **+N not used in scanned files** switch on the lists (the switch is remembered). They are never
  called unused or safe to delete: a report that wasn't scanned may use them. They stay in the
  catalogue and in search, and come back to the lists when a later scan finds a use.
- **Broken visuals as check items.** A visual that asks for a field the model doesn't have (as
  read from the model file, with its date) becomes one *check* item per report on To review,
  naming the page, the visual and the field. The field may have been renamed or removed, or the
  model file you scanned may be older than the published one.
- **Add many folders at once.** On Sources, paste a list of paths, pick every subfolder of a
  parent, or load a list file (one path per line, or a CSV with path, name and subfolders
  columns). A preview shows each folder's name, file count, size, overlaps with folders you
  already scan and how it will be treated (master folder or single project); untick what you
  don't want, then one **Add and scan**. The list of your folders can be downloaded as that CSV.
- **Design documents.** A folder role **design** (suggested for `design/`, `specs/`,
  `requirements/`): every Word, PowerPoint, Excel and PDF file in it is read as a reference
  document during the folder scan (Word tables are read as rows, PowerPoint notes too), and a
  changed file becomes a new version. A single file in any folder can be marked as a design
  document on the folder's page. A document can say what it **describes** (a business project, a
  report, a table), and the Design documents box sits at the top of that item's page.
  Definitions found in documents ("StoreId: identifier of the store") are offered as column and
  measure descriptions.
- **Power BI report register.** Keep your list of published reports (one CSV row per report:
  uniqueID, name, description, owner, audience, tags, statusName, powerBIAppID, webUrl, dataset,
  datasetId, publishedDateTime, modifiedDateTime; other columns are kept too) in a folder you
  scan, open the data file and pick **Use as the Power BI report register**. Each row becomes a
  searchable entry (by owner, audience, app, status), is matched to the scanned .pbix of the same
  name (change it by hand), and shows at the top of the report's page as key context. The owner
  and description fill the report's own fields where they are empty. **Power BI › Register**
  lists every row with filters and the rows nothing matched. The register is read again after
  every scan (a switch on Settings).
- **To review, less overwhelming.** The page starts with the counts by check (pick one to see
  its items), what you did this week, and five to start with. Data type, sensitive, odd value and
  unmatched table items are now **one per table** (or per server and database prefix) instead of
  one per column, and what you already dealt with stays dealt with. Answers stick: "this data
  type is our standard", "these values are placeholders" (0, -1, 9999, N/A…), "names like this are
  usually sensitive / never sensitive", "this whole table has nothing sensitive", "ignore tables
  with this prefix". **Changed since last scan** says what changed (columns added or removed,
  types changed, a view's definition) and a row count or a date alone is no longer an item.
  **Suggested descriptions** for tables and columns come from the same name elsewhere, design
  documents, SAP meanings and the name itself, accepted with a tick and applied to every other
  undescribed column of that name. Noisy checks (naming rules, old items) are off by default;
  turn them on under Settings › To review.
- **Summaries** under Find: one row per Power BI file (connected reports, pages, visuals, tables,
  columns, calculated columns, measures, relationships, queries, data sources), per table (columns,
  rows, keys, relationships, scripts and reports that use it, described columns) and per Python
  project (scripts, notebooks, functions, lines, tables and files read and written, imports),
  grouped by folder, report group, business project, app, owner, workspace, database, schema or
  project, with totals per group and overall, a split into report pages, and a CSV download.
- **Scan scope.** Pick on Settings which sources a plain `pdcat scan` covers; `--all` scans
  everything and `--ask` asks at the start of a run, with the saved scope used after 60 seconds.
- **Likely values for metrics.** A metric's page shows the values it usually takes, measured from
  the profile of the column it is built on (one input: its 1st to 99th percentile and median) or
  estimated from the quartiles of its inputs (a ratio or difference, marked as not measured). Your
  own **usual range** and a note ("below 0.7 usually means a load failed") win over both, are
  searchable and go into the Copilot pack.
- No rescan is needed for any of this; a rescan fills in design documents and the register.

## New in version 2.20

- **Copilot pack export fixed.** Building the pack stopped with "unhashable type: 'dict'" when the
  SQL query library had queries in it, because each query's tables are stored with their columns
  and joins, not as plain names. The SQL queries file now lists each table by its catalogue name
  (or the name written in the SQL when it isn't catalogued), and the pack copes with any odd value
  instead of stopping. No rescan needed.
- **One odd item can't stop the pack any more.** Each part of the pack (and each table, file or
  workbook in the data files) is built on its own. If one can't be read, it is left out, the rest
  of the pack is still made, and 00-start-here lists what was left out and why. Other weak spots
  found in a review are fixed too: row counts stored as text, Power BI visuals with no title or
  type, odd page lists, and DAX that isn't plain text.
- **Word packs always open.** Control characters that sometimes sit in pasted SQL or DAX are
  removed from the Word (.docx) version, so Word no longer refuses the file.

## New in version 2.19

- **Search finds part of a name.** Typing *Stock* now finds StockonHand, OfficeStockonHand,
  vw_MaterialStockOnHand, STOCK_LEVELS and Overstock. Names are matched by their parts
  (CamelCase, snake_case and dotted names are split up), and a word of three letters or more
  also matches anywhere inside a name. Separators don't matter: *stock on hand*, *stock_on_hand*
  and *StockOnHand* find the same things.
- **Best matches first.** A name that is exactly what you typed comes first, then names that start
  with it, then names with it as a part, then names with it inside, then matches in descriptions,
  columns, notes and code.
- **Abbreviations.** A new **Abbreviations** page under Knowledge holds short forms that search
  understands both ways: with *SOH = Stock on hand*, searching *SOH* finds StockonHand and
  searching *stock on hand* finds SOH_Daily. It starts with common supply chain terms (SOH, ATP,
  MRP, MOQ, OTIF, DOS, FG, WIP, PO, STO…); change it to suit your business.
- **The same matching everywhere.** The sidebar search, the Search page, Columns only, and the
  Find lists (Tables and datasets, Python, Power BI, including model tables and queries) all
  match this way.
- **Search remembers.** Coming back to Search from another page keeps your words, view, grouping
  and filters; the Find lists keep their filter too.
- **No rescan needed.** The search index is rebuilt once, the first time 2.19 opens your catalogue.

## New in version 2.18

- **Relationship suggestions keep up with big databases.** The first step after a scan used to
  read every view and procedure again each time. Now what each one joins is saved with a
  fingerprint of its SQL, so a rescan only reads the ones that changed. The joins found are saved
  every couple of seconds, so pressing Stop keeps them, and a run stops reading after two minutes
  and reads the rest next time. The step shows which view it is reading ("reading the SQL of
  rpt.vSales (120 of 302)") and its own progress bar. Matching key names now only looks at
  key-like columns and skips pairs whose types can't join (a text code against a number). Turn
  the step off under **Settings → Scan extras** if you don't want it; what it found is kept. Checked
  with a made-up database of 415 tables, 302 views and 120 procedures (`tests/bigdb.py`).
- **Fewer false "unmatched tables".** SQL Server's own views (`sys.objects`,
  `INFORMATION_SCHEMA.COLUMNS`), table-valued functions (`FROM dbo.fnSales(@day)`) and the FROM
  inside `TRIM(' ' FROM name)` or `SUBSTRING(x FROM 2)` are no longer counted as tables. Rescan
  your SQL Server to drop the ones found before (system views drop out straight away). The
  **Unmatched tables** page and `pdcat scan` now say why each name didn't match: in a database or
  on a server you haven't scanned, built while the code runs, several tables with that name, or
  not found in what you've scanned.
- **A diagram on every table and view page.** Under **Diagram**, the table sits in the middle with
  the tables it joins to directly: foreign keys and confirmed joins solid, suggested ones dashed.
  Fields are off unless you switch them on, a click opens a table, and **Open the full database
  diagram** goes to **Database diagrams** with this database and its subject area picked.

## New in version 2.17

- **Versions of a report are grouped for you.** Reports in the same folder whose names start the
  same ("Sales report", "Sales report v2", "Sales report 2023-04 FINAL", "Sales report - copy",
  "Sales report March") are grouped as versions of one report after each scan, with no yes needed.
  The newest one that isn't named or filed as old is the current one. Excel workbooks are grouped
  the same way. Names have to share a meaningful start: two real words ("Weekly KPI pack" and
  "Weekly KPI pack Bob"), not just "report" or "dashboard". When the names share less, or go
  different ways after the start ("Sales report North", "Sales report South"), they are grouped
  only if the reports also hold mostly the same pages, measures and sources. Reports in different
  folders, or matched by content alone, are still only suggested, as before.
- **Only the current version shows.** Search, the Find › Power BI lists (reports, model tables,
  queries and measures), the Power BI map and Measures across reports show the current report with
  "+3 older" next to it. Each has a **Show older versions** switch that lists them all, marked
  "older version". The measures inside older versions are left out of search too.
- **Undo is one click.** On the group's page, make another report current (later scans keep your
  choice), take one out, or break the group up; what you take out isn't grouped again until you
  press **Allow again** on its page. Settings has a switch to turn the grouping off, which unfolds
  everything. Catalogues scanned before 2.17 are grouped the first time you open the app.

## New in version 2.16

- **Power BI model tables and queries open pages of their own.** On a report's page, pick a row
  in **Model tables** or **Queries** to open it (parameters have buttons under the queries).
  A table's page shows its columns, the measures that live in it, its relationships (each other
  table is a link), the query that loads it, the lineage back to the database tables and files,
  and the pages and visuals that show it. A query's page shows its M code and any SQL it sends,
  what it reads (catalogued tables and files open their own page), the queries it builds on and
  that build on it, and the table it loads. Excel workbook queries open the same way. Both take
  notes and follow-ups like any other item, and work on reports you already scanned: no rescan
  needed.
- **From a visual or a measure to its table.** Picking a visual on a report page shows buttons for
  the tables it uses, and a measure's page links to its home table.
- **Find menu: Tables and datasets, Python, Power BI.** One click opens a list of every table,
  view, data file and file series; every Python project, script and notebook; or every Power BI
  report, model table, query and measure. Each has a filter box, and a row opens its page.
- **Measures in search show their report.** "Where" now names the report a measure is in, not
  just the scan group, and a measure with no description shows its DAX instead.
## New in version 2.15

- **SQL with variables is read properly.** Much real SQL isn't one clean statement: it comes from
  Python with placeholders (`{}` where an f-string filled in a variable, `{schema}`, `?`, `%s`,
  `%(start)s`, `:start`), from dbt or Jinja templates (`{{ ref('sales') }}`, `{% if %}`), from
  SQLCMD scripts (`$(Db)`, `:setvar`, `GO` between batches) and from stored procedures with
  `DECLARE`/`SET`, `TRY`/`CATCH`, cursors, `PRINT`, `GOTO` and `MERGE`. sqlglot used to give up on
  these ("unsupported syntax, falling back to parsing as a Command"), so their tables went missing.
  Before sqlglot reads a query now, a copy is tidied: placeholders become plain names, template
  variables are filled in where they name a table (`{{ source('sap', 'ekpo') }}` → `sap.ekpo`),
  script-only lines are dropped, and each `GO` batch is read on its own, in the T-SQL dialect.
  SQL held in strings (`SET @sql = 'SELECT … FROM dbo.Sales'; EXEC (@sql)`) is read too. Temp
  tables, table variables, cursors and `USE db` are no longer mistaken for tables.
- **Nothing is lost when sqlglot still can't read a statement.** The statements it can't read
  (or reads only as a generic Command) are read by the simple pass, statement by statement, so
  their tables and lineage are still found. Each query page says how it was read ("Read by sqlglot,
  except 1 statement it couldn't read…") and what was tidied first; the SQL queries list has a
  "read" column and a "Not read exactly" filter.
- **Scans don't get stuck on one query.** Exact reading has a 5-second limit per query and a
  5-minute budget per library build; after three time-outs the rest of the build is read simply
  and read exactly on the next build. Very long SQL (over 200,000 characters) is read simply.
  sqlglot's warnings no longer fill the console; they are counted instead.
- **The scan log counts it.** "After each scan" shows how the SQL was read: "412 read exactly,
  9 partly, 3 simply (1 ran out of time)".

## New in version 2.14.1

- **Power BI scans no longer fail on a blank value.** pbixray hands back an empty cell (a measure
  with no expression, a query with no body, a missing description) as NaN rather than text, which
  ended a scan with `'float' object has no attribute 'strip'`. Blanks are now turned into empty
  text when the model is read, and the comparisons that run on rescans (report versions, measures
  across reports, data sources) cope with blanks already stored by earlier versions.
- **Power BI map filters:** only reports whose name or folder contains some words, only those that
  need a look, and at most N (the biggest first, 25 by default), so a big estate doesn't draw
  everything at once.
- **Big folders list faster.** A folder scan now asks the file system about each file once rather
  than three times, which matters on a network drive with 20,000 files, and the progress line for
  files that aren't opened says "Listing … (not opened …)" so it isn't mistaken for reading them.

## New in version 2.14

Folders with thousands of data files scan in minutes rather than hours, and a stopped scan
keeps what it had read.

- **Big folders are sampled by date.** A folder with more than 2,000 data files (Settings)
  opens only the newest 10 files of each dated series and the newest 2,000 stand-alone files;
  the rest are listed from the file system (name, size, modified) without being read. The
  folder card says how many files were opened, with a **Read every file** switch, and a
  sampled series says so on its page. A file read in an earlier scan keeps its rows and
  columns.
- **Daily files with a time in the name form one series.** `TOPIC_TOPIC_202304201901500.csv`
  is 20 April 2023 followed by a time, so a folder of such files becomes one daily series
  (pattern `TOPIC_TOPIC_{yyyy}{mm}{dd}{time}.csv`) instead of thousands of separate files.
  `sales_2023-04-20_190150.csv` works the same way.
- **Scans save as they go.** Power BI reports and Excel workbooks are saved a few at a time,
  then each dated series and every 200 stand-alone files, each in a short transaction. Pressing
  **Stop** (or a crash) loses only the batch being read; the scan log records the stop and the
  next scan carries on from what was saved. Files that have gone are marked missing when a scan
  completes, as before. Other windows can write to the catalogue throughout.
- **No re-scan of a folder scanned a moment ago.** Re-scan on a folder scanned in the last hour
  (Settings) asks you to press again; `pdcat scan` skips such sources unless run with
  `--force`. Unchanged files were already skipped; this saves the walk and the checks too.
- **Faster reading of each file.** The delimiter of a CSV is read off its first lines (the
  csv sniffer runs only when that isn't clear), most-common values are counted on the values
  themselves instead of a text copy of every column, and progress updates for files that
  aren't opened are drawn every half second rather than per file. On a folder of 3,000 small
  CSVs this roughly halves the time per file.
- **Measures across reports** (Map menu): the same DAX under different names in different
  reports, and the same measure name worked out differently, each with links to the measures.
  Earlier versions of a report are left out.
- **Smaller fixes:** the To review table has an **Open** button for the ticked row (clicking a
  cell only selects it); What's new no longer fails with a duplicate element key; Database
  diagrams can list the fields of chosen tables, not only all or none; package pages link to
  the package's documentation (or PyPI); the Copilot pack's test questions include a SQL
  script's reads and writes; f-strings in scripts follow attributes and calls
  (`f"{self.out}/{self.topic}_{stamp}.csv"`), so more `to_csv` targets get a file pattern
  instead of landing under Unknown files.

## New in version 2.13

**Data flow diagram** (🗺️ Map → Data flow, and on each project's page). It reads left to
right, like an ETL diagram: where the data starts, the code and reports it passes
through, and the tables and files that come out.

- **Where data comes from is now recognised in your code**, not only tables and files:
  - 🌐 **APIs**: `requests`, `httpx`, `aiohttp`, `urllib`, sessions and clients with a
    `base_url`, boto3 services.
  - 🕸️ **Web scraping**: pages fetched in a script that uses BeautifulSoup, lxml,
    requests-html and similar, Selenium and Playwright browsers, Scrapy spiders and
    `pd.read_html` on a web address. An address that looks like an API (`api.` host,
    `/api/`, `/v1/`, `.json`, OData) stays an API even in a scraping script.
  - 🛢️ **Database connections**: `pyodbc`, `pymssql`, SQLAlchemy `create_engine` (with
    `odbc_connect` too), PostgreSQL, MySQL, Oracle, SQLite, DuckDB, Snowflake, SAP HANA,
    Databricks, Teradata, JDBC and more. Connection strings are read for the server and
    database only.
  - ⬇️ **Files from the web** (`pd.read_csv("https://...")`, `urlretrieve`) and
    ☁️ **cloud storage** (`s3://`, `abfss://`, `gs://` paths, Azure Blob and S3 clients).
  - 🏭 **SAP** through `pyrfc`, with the RFC functions called (and the table for
    `RFC_READ_TABLE`).
  - In **Power BI reports and Excel workbooks**: Web.Contents, OData, SharePoint and the
    SQL Servers and ODBC sources in Power Query and workbook connections.
- **Each source is an item** (🔌 data source) you can open, describe and add notes to. Its
  page lists the addresses used and what uses it. The same API used by two projects is one
  box on the diagram.
- **Addresses never keep secrets.** Only the host and path are kept: query strings (where
  API keys usually are), user names and passwords are left out.
- **Start from one project** and the diagram also follows the data downstream (a report
  reading the table your script loads) and upstream (the script that writes the table
  your project reads). Tables and files nothing writes are grouped on the left by
  database; outputs end up on the right. Layout is automatic.
- **Where data comes from** table under the diagram, with a CSV download, plus Mermaid and
  Graphviz downloads of the diagram.
- **On every script's page** (notebooks and SQL files too) the same diagram, centred on that
  file: what it reads, what it writes, and what reads that afterwards.
- **Power BI map** (🗺️ Map → Power BI map): every report sized by its pages, visuals and
  measures, with the tables and files it loads, the scripts that write those, and the APIs
  and databases behind them. Older versions are folded into the current report, and a red
  dashed border marks a report that loads nothing catalogued, has no sources, or couldn't be
  read. The reports table beside it (biggest first) has a CSV download.
- When an address is built at run time (`requests.get(url)` with `url` from elsewhere), the
  source is still listed, named after the code that holds the address, so you can add a note
  saying where it points.
## New in version 2.12

Folder scans no longer look stuck, and big folders scan faster.

- **A progress box while scanning.** Adding or re-scanning a data folder (and scanning code)
  shows which file is being read, how many are left, how long it has taken, and any file
  that took longer than 20 seconds. The suggestions and review list worked out after a scan
  show step by step too; before, they ran with nothing on screen. A **Stop** button ends a
  scan, leaving the folder as it was before. When the scan finishes, the Data folders page
  says what was found and how long it took.
- **The terminal says what's happening.** The window the app was started from (and
  `pdcat scan`, including the weekly scheduled scan) prints an update every 30 seconds and
  names anything slow, so you can tell a long scan from a stuck one.
- **One pass over the folder.** The folder is walked once instead of four times, and the
  walk steps around folders that never hold your data: `.git`, `__pycache__`,
  `node_modules`, Python and conda environments, the recycle bin, and folders marked
  *ignore* in Folder roles. This matters most on network drives.
- **No more waiting on one big Power BI file.** With pbixray installed, a `.pbix` model is
  read in a separate process and given three minutes. If it takes longer it is stopped,
  the report page says so, and the scan carries on.
- **Big Excel workbooks read faster.** Only the start of a workbook's shared text is read to
  get the header rows, instead of all of it.
- **Adding a folder is quicker.** The app no longer re-examines the folder on every click
  while you fill in the form.
- **Far fewer "database is locked" errors.** A folder scan now reads all its files first and
  only then saves them, so the catalogue is locked for seconds rather than for the whole
  scan. Opening the catalogue no longer needs the lock at all, and anything that does have
  to wait for it waits up to a minute instead of failing after five seconds. This helps
  when the app and the weekly scan run at the same time, or when you have two windows open.
- **No more SyntaxWarnings from your scripts.** Older scripts often have things like `"\d"`
  in ordinary strings. Python printed a warning about each one while the catalogue read
  the code. They were about your scripts, not the catalogue, and are no longer printed.
- **Old versions of Power BI reports and workbooks.** Copies of one report (Sales.pbix,
  Sales_v2.pbix, "Sales - Copy.pbix", archive/Sales.pbix) are suggested as versions of one
  report, the same way script versions are. When the names don't say, two reports that share
  most of their pages, measures, tables and sources are matched by content instead. The
  newest file that isn't in an archive folder or named as old is suggested as the current
  one; only that one shows in search and diagrams once you accept. The suggestion says what
  it was matched by. Workbooks are matched by sheets and queries.
- **How a report changed over time.** A report's page gets an **Over time** section: its
  versions oldest first, each compared with the one before (pages, visuals, measures and
  their DAX, tables, sources added, removed or changed), and the changes each rescan saw in
  the file itself. Measure and page changes also go into the item's history, so they show
  under What's new › Recent changes. The group page compares any two versions.
- **A warning before a long scan.** The Add a folder form adds up the files first and warns
  when a folder holds thousands of files, gigabytes to read, or any file over 200 MB, and
  says what the scan does about it (one file at a time, changed files only on a rescan,
  large CSVs sampled, a large Power BI model given three minutes). The progress box shows
  a rough time left once a few files are in.
- **Scan log** (Add › Sources › Scan log): every scan with how long it took, how long was
  spent reading files, how much was read, the slow files and any errors; and the after-scan
  steps that took longest. A scan that took much longer than the one before is flagged at
  the top, so you can see the weekly scan getting slower before it becomes a problem.

## New in version 2.11

A tidy-up release after a code review. Nothing new to learn; the app looks and works the
same. What changed:

- **Two small fixes.** Reference documents now show their title (not `doc://…`) in search
  results and in the Home page's recently viewed list, and files under a folder marked
  *ignore* in Folder roles are now skipped when a project's data files are catalogued, the
  same as they already were for data folders.
- **Less hidden plumbing.** The post-scan finders, review checks and suggestion handlers
  are now plain imports in one list each (`postscan.py`, `review.py`, `suggestions.py`)
  instead of module names looked up by string, so a typo or a broken module shows up as
  an error instead of a feature quietly doing nothing. A review check that fails now warns
  instead of being swallowed.
- **Clearer names.** `backups.py` became `tablepairs.py` (main and backup tables; the
  catalogue backups live in `backup.py`), `drafts2.py` became `commentdrafts.py`, and the
  Copilot pack additions moved from `aipack21.py` into `aipack.py`.
- **Lint and dead code.** The code is checked with `ruff` (`ruff check src tests scripts`),
  unused imports and functions are gone, and the version number lives in one place
  (`pdcat.__version__`). The CLI closes the catalogue on every path.
- **Tests run on GitHub** for every push and pull request, on Linux and Windows.

The review notes, and what they suggest for other projects, are in
[docs/lessons-learned.md](docs/lessons-learned.md).

## New in version 2.1

Version 2.1 adds the 46 items below. Some things to know first:

- **The menu is tidier.** The app now opens on a **Home** page, the search box is always
  at the top of the sidebar, and pages that belonged together are now one page with a
  row of views: **To review** (with Data issues, Needs notes, Unmatched tables, Unlinked
  data and Duplicates), **Health** (Coverage and Issue hotspots), **What's new** (Recent
  notes and Recent changes), **Bulk edit** (Describe columns and Edit many items) and
  **Sources** (Add SQL, Data folders and SQL scanning). Company-wide keys moved to
  Knowledge, Packages to Map, and Sensitive data to Settings. The **📖 Menu guide** link
  in the sidebar explains every page.
- **Rescan after upgrading** (`pdcat scan`). Most of 2.1 is worked out from what a scan
  reads, so the SQL query library, report wireframes, report labels, renames, git
  history and the rest fill in on the first scan. Report page wireframes in particular
  need the report to be read again.
- **Switches.** Each step that runs after a scan can be turned off on the Settings page.
  Nothing already found is deleted when you turn one off.
- **Optional packages.** `sqlglot` (better SQL reading), `pypdf` (text of PDF
  documents) and `pywin32` (Access databases on Windows) are used when installed, and
  the app works without them: SQL is read with a simpler reader, PDFs are kept without
  their text, and Access files are listed but not opened. Word documents need nothing
  extra. Settings shows which are installed.
- **Nothing is sent anywhere.** The Copilot round trip and the questions for colleagues
  are files you carry yourself.

- **"Called by" on script pages**: every other script that uses a script, in one list.
  That means scripts that import it (and which of its functions they call), and scripts
  that run it with `subprocess`, `os.system`, `runpy`, `python -m`, `%run` or
  `!python`. Run links also show on the code map.
- **Link files found in another folder**: when a script names `sales.csv` but the only
  `sales.csv` catalogued is in a different folder, link it anyway and flag it as a
  possible wrong folder (before 2.1 the folders had to match). When the same file name
  turns up in several folders, it says so on the link and on the script and file pages
  (before, the newest was picked without a word).
- **Folder roles**: when you add a folder, the app suggests what it is. A *master
  folder* whose subfolders each look like their own project (their own `.git`, README,
  `environment.yml` or requirements, `src/`) becomes one project per subfolder. A
  *single project* keeps its subfolders together, each with a role suggested from its
  name and contents: code (`code/`, `src/`, `scripts/`, `notebooks/`, `sql/`), design (`design/`,
  `specs/`, since 2.3), raw data
  (`raw_data/`, `raw/`, `input/`: data that comes in from outside), data (`data/`,
  `processed/`), outputs (`output/`, `results/`), docs, archive (`archive/`, `old/`,
  `backup/`: kept but treated as old), or ignore. Roles apply at any depth, so
  `forecasting/data/raw_data/` is raw data inside the forecasting project. You confirm or change each one, the choices survive rescans, and new
  subfolders that appear later are suggested the same way.
- **Business projects**: add a whole folder (a project, a sub-folder like
  `forecasting/src`, or a data folder) in one go, with an option to keep adding new files
  that appear there. Start a new business project straight from a table or script page,
  with that item already in it.
- **Mark items as not relevant**: one click hides a table such as `dbo.sysdiagrams` from
  search, the review list, coverage, diagrams and exports; scans keep it hidden, and a
  list lets you bring it back. Tables SQL Server tools create for themselves (marked
  `microsoft_database_tools_support`, like `sysdiagrams`) are skipped by default.
- **ML model features** (was the feature dictionary): says up front that it lists the
  inputs to your machine learning models. Each row names the models that use the
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
  Every read or write call is recorded even when its path can't be worked out: the
  script page lists it as "Unknown file" with the line and the code (`df.to_csv(filepath)`),
  it goes on the To review list, and you can say which file it is, which then links it
  like any other.
- **Script versions**: scripts in the same folder whose names differ only by a version
  or date at the end (`forecast_v2.py`, `forecast_v3.py`, `forecast_20261001.py`,
  `forecast_old.py`, `forecast - Copy.py`) are grouped as versions of one script. The
  current one is the one other code imports or runs, or the one in the schedule list,
  or else the newest; when that isn't clear, you tick the main one. Only the current
  version shows on the code map, in search and in lineage. Older versions stay listed
  on its page with their dates and a "what changed" view, and you can split a pair that
  aren't really versions.
- **Main and backup tables**: link a table on SQL Server with its copy elsewhere (an
  Azure SQL backup with extra history, say) and mark which is the main one. Pairs are
  suggested when the same table name and columns turn up on two servers, for you to
  confirm. Both pages show the pair side by side: where each lives, the date range each
  covers (from the profiles), row counts and any columns only one has. On the code map
  and in lineage the pair shows as one table with a "main + backup" badge, so a script
  that reads both is clear about why.
- **Copilot round trip**: export a "gaps pack" of items with no description, with their
  code and columns, for your Copilot agent to draft descriptions; import its answers as
  suggestions you accept or edit. No AI service is connected to the catalogue. It's on
  the Copilot pack page under Round trip: the pack asks for a CSV of `id,column,answer`,
  which you paste or upload back. Naming rules the app couldn't read go in too, and
  Copilot's rewrite comes back as rules to confirm.
- **Descriptions from comments**: draft descriptions from SQL comments, docstrings,
  notebook markdown and comments next to column renames, as suggestions.
- **Column renames in lineage**: `rename({"Units": "qty"})` or `SELECT Units AS qty`
  links the two columns. Both names are shown, with the script and line where it was
  renamed, and the new column inherits the old one's description as a suggestion.
  Column lineage follows the chain through every rename.
- **Report labels**: Power BI display names and visual titles suggested as business
  names for the SQL columns behind them.
- **Git history**: who last changed a script, when, and the commit messages, used to
  suggest owners and show a change log.
- **Freshness and old items**: output files' modified dates show whether the scripts
  that write them still run. Outputs that keep refreshing earn a "refreshing" badge and
  drop out of your way; ones that stop go on the To review list asking for a refresh or
  a note on whether they're still needed. Scripts and projects nobody has touched or
  run in a long time are flagged the same way. You can mark projects, scripts and files
  as old, which hides them from the code map and diagrams (a switch shows them again)
  without deleting their notes.
- **Rules become checks**: a rule you write ("Units is never negative", "loaded daily
  by 7am") becomes a check run with each profile; failures go to review. Write them in
  the **Rule checks** box on a table or file page. It reads never negative / always
  positive, never blank, unique, between A and B, at least / at most N, one of A, B, C,
  at least N rows, and loaded daily / weekly / monthly / every N days (optionally "by
  7am", checked against the newest date in the data or a file's modified time). Rules
  already sitting in notes and column descriptions are offered as checks to add.
- **Questions for colleagues**: a share file holding only your open questions, which a
  colleague answers in their catalogue and merges back into yours (Share › Share with
  colleagues). Their reply is an answers file holding only the questions you asked.
- **Five quick questions**: a short daily list of the highest-value gaps, each answerable
  in under a minute, mostly by confirming a suggestion.
- **Learn from your queries**: joins and filters that come up again and again in pasted
  SQL and scripts become "how this table is usually used" notes and join hints.
- **Home page**: opens first. It shows your follow-ups, what changed since your last
  visit, your five quick questions, and anything failing or no longer refreshing.
- **Follow up**: a "Follow up" tick on any table, script, file or knowledge page pins it
  to the home page (with an optional note on where you were), so you can get back to
  what you were working on. Untick it when you're done.
- **Search box always in the sidebar**, so you can search from any page.
- **Search results as a table**: a sortable table with kind, name, where it lives,
  description, what it matched on, rows, last changed or scanned, notes, status and
  trust, and how many scripts use it. Filter by kind or source in the table, click a row
  to open it, and switch back to the card view if you prefer. File families show as
  one row ("SOH: daily, weekly, monthly") that opens to its members. Quick filter
  buttons above the results (Tables, Files, Code, Reports and measures, Metrics,
  Knowledge) replace hunting through the Kind list, and a metric found in search shows
  the Power BI measures and SQL that calculate it, and the reverse. A "Group by" choice
  regroups the results: by the script that creates them (so ten outputs of one script
  sit under that script, with anything nothing creates under "Not linked to a script"),
  by family, by folder or database, by business project, or not at all.
- **Remove or downplay links**: every link on an item's page (uses, created by, read by)
  gets a small menu: *Remove* for a link the scanner got wrong, or *Not important* for
  one that's right but noise (a script that only reads a lookup table, say). Unimportant
  links stay listed but greyed out, and are left out of grouping, diagrams, lineage and
  "used by" counts. Both survive rescans, show who set them, and can be undone from a
  "Removed and unimportant links" list.
- **SQL query library**: every SQL query found in scripts, notebooks, .sql files, Excel
  (Power Query and macros), Power BI and reference documents becomes a saved query,
  alongside the ones you paste in. Each gets a suggested name (from the variable or
  function holding it, a comment above it, or its file), which you can change, and
  records where it was found and the tables it uses. The same query found in several
  places is shown once, listing every place. A "SQL queries" page lists them all as a
  table you can sort, filter by table or source and pick from to see the query.
  Each table's page gets a "SQL using this table" section listing every query that
  references it, active ones first: in a scheduled script, a script whose outputs are
  still refreshing, the current version of a script, a view or procedure that exists
  now, or edited recently. Queries in old versions, old projects or items marked old
  sit below, greyed out. Each shows its name, where it is, how it uses the table
  (reads, joins on which columns, writes) and any SQL check findings.
- **Query diagrams with fields**: looking at a query (saved, pasted, a view or a
  procedure) shows a diagram of just the tables it uses, with their columns listed, the
  columns the query uses highlighted, and lines between the exact fields it joins on
  (from its ON clauses, plus the database's keys). Database diagrams get the same "show
  fields" view for any group of tables.
- **Access databases**: .accdb and .mdb files found in data folders are read for their
  saved queries (name and SQL, which go in the SQL query library), linked tables (what
  each points to, for example a SQL Server table, another Access file or a spreadsheet,
  with the connection's password removed, and linked to that item in the catalogue),
  and local tables with their columns and row counts. It opens the file read-only and
  never runs a query. On Windows it uses the Access Database Engine through DAO (from
  pywin32), so the engine's 32/64-bit version must match Python's; the setup page checks
  this and says what to install if it's missing.
- **SQL checks**: every saved query, view and procedure is checked (without running it)
  for common problems, each with a short reason and a suggested fix:
  - *Likely wrong results*: a join with no join condition, `NOT IN` on a column that can
    be empty, joining on a column that isn't unique (rows multiply; checked against
    profiles), comparing columns of different types, `BETWEEN` on date-times, dividing
    without guarding against zero, tables or columns that no longer exist or are marked
    deprecated.
  - *Slow*: `SELECT *`, functions wrapped around filtered columns (`YEAR(SaleDate) = 2025`),
    `LIKE '%...'`, `DISTINCT` covering duplicates, `UNION` where `UNION ALL` would do,
    cursors and loops in procedures; since 2.10 also subqueries and user functions per row, `OR` across
    columns, `ORDER BY` in views, joins on worked-out values, `COUNT(*) > 0`, long `IN` lists, hints, text
    columns compared with numbers, views on views, and big tables read with no filter. Review › Slow SQL lists
    them worst first.
  - *Risky*: `NOLOCK` on data that must be exact, hard-coded dates, missing schema names,
    and SQL built by pasting variables into strings in scripts (an injection risk).
  Findings show on the query's page and in a column on the SQL queries page, can be
  marked "fine here", and the worst ones go on To review.
- **Recently viewed** items in the sidebar.
- **Edit history** on notes and descriptions: who changed what and when, with undo.
- **Schedule scans from the app**: a Settings button that creates (or removes) the
  weekly Task Scheduler task for you, and shows when it last ran and whether it worked.
- **Naming rules audit**: paste a database's naming rules as text (from an email, say)
  on its page. The text is kept as written, and each rule the app recognises becomes a
  check you confirm or edit: prefixes (`vw_` for views, `usp_` for procedures), case
  (PascalCase, snake_case), endings (`...ID`, `...Date`), no spaces or reserved words,
  maximum length, which schema things belong in. Rules it can't turn into a check stay
  as text, and the Copilot round trip can help write them. The audit shows each rule
  with how many tables, views, procedures and columns pass, the ones that don't, and
  lets you mark agreed exceptions. New objects that break a rule go on To review, the
  results export to CSV, and the rules go in the Copilot pack so queries Copilot
  writes follow them.
  Rules can also be worked out from what's already there: for a database or a Python
  project, the app looks for the habits most names follow (say 92% of views start with
  `vw_`, columns are PascalCase, date columns end in `Date`, scripts are `snake_case.py`,
  output files are named `<thing>_<yyyymmdd>.csv`) and suggests each as a rule, with how
  many names follow it and examples of the ones that don't. You accept, edit or reject
  each suggestion; accepted ones are audited like pasted rules.
- **Tidier "Contains" lists**: a folder's contents show dated copies of the same data
  as one line per series (`sales_{yyyy}{mm}.csv · 48 files · Jan 2022 to Dec 2025`),
  opening to the files, and the rest grouped by subfolder and type, as a sortable table
  with a filter box instead of a long list of buttons. Scripts help find the series: a
  script that writes `f"out/sales_{run_date}.csv"` shows that every file matching
  `out/sales_*.csv` is one output, so they're grouped even when the part that changes
  isn't a recognisable date (a run number, a version, an odd date format), and the
  series links back to the script that makes it.
- **File families by grain**: files with the same base name and a grain suffix
  (`SOH_D`, `SOH_W`, `SOH_M`; also `_daily`, `_weekly`, `_monthly`, `_Q`, `_Y`) are
  grouped as one family, "SOH", while each stays its own item. The family page compares
  them side by side: the grain (from the suffix, checked against the spacing of the
  dates inside once profiled), date range, rows, columns that differ, and which scripts
  write and read each. The grouping is suggested for you to confirm, and you can add
  your own suffixes.
- **Starter knowledge packs**: optional packs of general knowledge you can load into the
  Knowledge section and Metric glossary, marked "general" so they're kept apart from
  what's specific to your data, and editable like anything else (loading again never
  overwrites your edits). First packs:
  - *Supply chain*: forecast accuracy (MAPE, WAPE, bias), safety stock, lead time,
    service level, fill rate, OTIF, inventory turns and days on hand, MOQ, ABC/XYZ,
    S&OP, demand vs supply planning terms.
  - *SAP*: the common tables and fields and what they mean (material master `MARA`/`MARC`,
    plants `T001W`, sales orders `VBAK`/`VBAP`, deliveries `LIKP`/`LIPS`, purchase orders
    `EKKO`/`EKPO`, stock movements `MATDOC`/`MSEG`; fields such as `MATNR`, `WERKS`,
    `LGORT`, `MEINS`), plus APO (planning books, key figures, DP and SNP), EWM
    (warehouse tasks, storage bins, handling units) and IBP (key figures, planning
    levels, attributes such as `PRDID` and `LOCID`).
  - *SQL Server good practice*: a general reference for writing and reviewing T-SQL:
    joins and NULLs, sargable filters and indexes, date handling, `NOLOCK` and isolation
    levels, temp tables vs table variables vs CTEs, window functions, set-based work
    instead of cursors, safe dynamic SQL, naming and formatting. Each SQL check links to
    the article that explains it, and the articles go in the Copilot pack.
  - *APO to IBP*: what changed in the move, as a side-by-side guide: APO DP becomes IBP
    for demand, SNP becomes IBP for supply (and response); planning books and data views
    become planning views (mostly in the Excel add-in); characteristics and CVCs become
    master data attributes and planning objects; location products become
    product-location master data; macros become key figure calculations and planning
    operators; and how forecasting, time buckets, lifecycle and realignment differ.
    Each pair says what to check when an old APO report, extract or metric is rebuilt
    on IBP data.
- **Link data to SAP**: mark what a table or column is in SAP terms ("PlantID is SAP
  `WERKS`", "this table is an extract of `MARC`"). Matches are suggested from column
  names (SAP field names, or common English names such as Material and Plant), for you
  to confirm. Linked items show the SAP meaning on their page, company-wide keys gain
  their SAP field, and the Copilot pack explains both so Copilot can answer in either.
  Data and scripts that still use APO (its tables, extracts or key figures) are flagged
  with the IBP equivalent, so you can see what needs moving and what already has.
- **Power BI measures as a table**: a report's measures (some have hundreds) show as a
  sortable, filterable table: name, home table, display folder, format, what it uses
  (columns and other measures), which pages and visuals show it, and whether it's in
  the glossary. Group by home table or display folder, filter to "uses another
  measure", and click a row to open it. Each measure is marked used or unused: used
  means a visual shows it, or a used measure builds on it (followed through every
  step). Unused ones are hidden by default, with a count and a switch to show them.
  Because other reports connected to a published dataset can use measures this file
  can't see, it says "not used in this file" rather than "unused".
- **Report page pictures**: a .pbix holds the layout of each page but no picture of
  the charts (Power BI draws them when the report opens). So each report page gets a
  wireframe drawn from its layout: every visual in its place, with its type, title and
  fields, and a click shows its measures. You can also paste or upload a screenshot of
  a page or visual to keep with it. Screenshots stay on your machine and are left out of
  share files and exports unless you tick them in, since they can show real numbers.
- **A tidier menu**: To review takes in Data issues, Needs notes, Unmatched tables,
  Unlinked data and Duplicates as filter tabs; Coverage and Issue hotspots become one
  Health page; Recent notes and Recent changes become one What's new page; Describe
  columns and Edit many items become one Bulk edit page; Add SQL, Data folders and SQL
  scanning become one Sources page; Company-wide keys moves to Knowledge, Packages to
  Map, and Sensitive data to Settings.
- **Suggested metrics**: the Metric glossary suggests metrics found in your code and
  reports: functions such as `wape()` or `forecast_accuracy()`, named calculations
  (`mape = ...`), computed SQL columns (`SUM(x) / SUM(y) AS fill_rate`), and Power BI
  measures not yet in the glossary. Each suggestion shows where it was found and the
  formula, and you tick the ones to add; the rest can be dismissed so they don't come
  back.

## Ideas for a later version

Kept here with no version planned:

- **Power BI template files (.pbit) as a deliverable**: a report template that shows the
  catalogue's own data.
- **Reading the Power BI service** (workspaces, published datasets, usage): the app reads
  .pbix, .pbit and .pbip files only, on purpose.
- **A no-install build or a .pbix add-on** for colleagues who don't use Python.

Built in 2.3: likely values for metrics.

## Upgrading from version 1

Install the new version the same way as before (`pip install -e .` or
`conda env update -f environment.yml --prune` then `pip install -e . --no-deps`) and run
`pdcat ui`. The first time it opens your catalogue it upgrades it in place and keeps an
untouched copy of the old file next to it (the path is shown in the app and on the
Settings page). Nothing is removed; every note is kept. Rescan to fill in the new
details (`pdcat scan`).

To upgrade from 2.0 to 2.1, install the same way and rescan; see
[New in version 2.1](#new-in-version-21).

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

To refresh the catalogue automatically every week, see
[Scan on a schedule](docs/commands.md#scan-on-a-schedule): a ready-made batch file and one
Task Scheduler command.

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
ruff check src tests scripts            # lint (same rules as the GitHub check)
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
