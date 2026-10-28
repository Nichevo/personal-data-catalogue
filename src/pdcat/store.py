"""SQLite storage for the catalogue: scanned assets, the links between them, and your notes.

Scanned data and notes live in separate tables. Notes are keyed by asset key,
so re-scanning never touches them, and a note survives even if its table
disappears for a while.
"""
from __future__ import annotations

import json
import re
import fnmatch
import getpass
import sqlite3
import uuid
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    key         TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    source      TEXT NOT NULL,
    parent      TEXT,
    description TEXT,
    attrs       TEXT NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'active',
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_assets_source ON assets(source);

CREATE TABLE IF NOT EXISTS columns (
    asset_key   TEXT NOT NULL,
    name        TEXT NOT NULL,
    ordinal     INTEGER,
    data_type   TEXT,
    nullable    INTEGER,
    description TEXT,
    PRIMARY KEY (asset_key, name)
);

CREATE TABLE IF NOT EXISTS links (
    src_key  TEXT NOT NULL,
    relation TEXT NOT NULL,
    dst_ref  TEXT NOT NULL,
    dst_key  TEXT,
    source   TEXT NOT NULL,
    hint     TEXT,
    evidence TEXT,
    PRIMARY KEY (src_key, relation, dst_ref)
);
CREATE INDEX IF NOT EXISTS ix_links_dst ON links(dst_key);

CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY,
    asset_key  TEXT NOT NULL,
    body       TEXT NOT NULL,
    tags       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    note_type  TEXT NOT NULL DEFAULT 'note',  -- note | issue | example
    status     TEXT NOT NULL DEFAULT 'open'   -- issues: open | resolved
);
CREATE INDEX IF NOT EXISTS ix_notes_asset ON notes(asset_key);

-- Value profiles: one row per column, from a SQL table or a data file.
CREATE TABLE IF NOT EXISTS profiles (
    asset_key   TEXT NOT NULL,
    column_name TEXT NOT NULL,
    ordinal     INTEGER,
    data_type   TEXT,
    row_count   INTEGER,
    null_count  INTEGER,
    distinct_count INTEGER,
    min_value   TEXT,
    max_value   TEXT,
    mean_value  REAL,
    top_values  TEXT NOT NULL DEFAULT '[]',  -- JSON [[value, count], ...]
    method      TEXT,                        -- how it was measured, e.g. "full table" or "first 200000 rows"
    profiled_at TEXT NOT NULL,
    PRIMARY KEY (asset_key, column_name)
);

-- Things to look at, generated from the catalogue (see review.py). Your status survives regeneration.
CREATE TABLE IF NOT EXISTS review_tasks (
    id           TEXT PRIMARY KEY,           -- "<rule>:<asset key>[:<version>]"
    rule         TEXT NOT NULL,
    asset_key    TEXT NOT NULL,
    title        TEXT NOT NULL,
    detail       TEXT,
    priority     REAL NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'open',  -- open | done | snoozed | dismissed | resolved (no longer applies)
    snooze_until TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_review_asset ON review_tasks(asset_key);

-- Home-built libraries added from the app (config libraries live in config.toml).
CREATE TABLE IF NOT EXISTS libraries (
    name     TEXT PRIMARY KEY,
    path     TEXT NOT NULL,
    added_at TEXT NOT NULL
);

-- Folders of CSV/Parquet files added from the app (config folders live in config.toml).
CREATE TABLE IF NOT EXISTS folders (
    name      TEXT PRIMARY KEY,
    path      TEXT NOT NULL,
    recursive INTEGER NOT NULL DEFAULT 1,
    added_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scans (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    summary     TEXT
);

-- App settings (backups, SQL pacing, Copilot pack…) and the catalogue's revision counter.
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- What you know about an item, kept apart from scanned data so rescans never touch it.
-- asset_key is any key: a table, file, script, report, business project ("bp://…") or package ("pkg://…").
CREATE TABLE IF NOT EXISTS item_info (
    asset_key        TEXT PRIMARY KEY,
    purpose          TEXT,
    grain            TEXT,
    refresh          TEXT,          -- daily | weekly | monthly | ad hoc
    lifecycle        TEXT,          -- in use | experimental | deprecated | retired
    replaced_by      TEXT,
    trust            TEXT,          -- trusted | caution | do not use
    owner            TEXT,
    sensitive        INTEGER NOT NULL DEFAULT 0,
    freshness_column TEXT,
    copy_of          TEXT,
    updated_at       TEXT,
    author           TEXT,
    import_batch     INTEGER
);

-- Your description of a column, and whether its values are sensitive.
CREATE TABLE IF NOT EXISTS column_info (
    asset_key    TEXT NOT NULL,
    column_name  TEXT NOT NULL,
    description  TEXT,
    sensitive    INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT,
    author       TEXT,
    import_batch INTEGER,
    PRIMARY KEY (asset_key, column_name)
);

-- Company-wide keys such as PlantID, and the columns that carry them.
CREATE TABLE IF NOT EXISTS keys (
    name          TEXT PRIMARY KEY,
    state         TEXT NOT NULL DEFAULT 'active',   -- active | suggested | rejected
    description   TEXT,
    format        TEXT,
    master_key    TEXT,
    master_column TEXT,
    aliases       TEXT NOT NULL DEFAULT '[]',
    evidence      TEXT,
    created_at    TEXT,
    updated_at    TEXT,
    author        TEXT,
    import_batch  INTEGER
);
CREATE TABLE IF NOT EXISTS key_columns (
    key_name     TEXT NOT NULL,
    asset_key    TEXT NOT NULL,
    column_name  TEXT NOT NULL,
    state        TEXT NOT NULL DEFAULT 'suggested', -- confirmed | suggested | rejected
    how          TEXT,
    updated_at   TEXT,
    import_batch INTEGER,
    PRIMARY KEY (asset_key, column_name)
);

-- Business projects ("Demand forecasting") that group tables, scripts, files and reports.
CREATE TABLE IF NOT EXISTS bprojects (
    uid          TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    purpose      TEXT,
    status       TEXT,
    owner        TEXT,
    notes        TEXT,
    created_at   TEXT,
    updated_at   TEXT,
    author       TEXT,
    import_batch INTEGER
);
CREATE TABLE IF NOT EXISTS bproject_members (
    project_uid TEXT NOT NULL,
    asset_key   TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'confirmed',  -- confirmed | suggested | rejected
    PRIMARY KEY (project_uid, asset_key)
);

-- Standalone knowledge: articles and business rules that needn't attach to anything.
CREATE TABLE IF NOT EXISTS knowledge (
    uid          TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    body         TEXT NOT NULL DEFAULT '',
    section      TEXT,             -- Business rules | How-to | History | …
    tags         TEXT NOT NULL DEFAULT '',
    triggers     TEXT NOT NULL DEFAULT '',
    pinned       TEXT NOT NULL DEFAULT '[]',
    excluded     TEXT NOT NULL DEFAULT '[]',
    author       TEXT,
    created_at   TEXT,
    updated_at   TEXT,
    confirmed_at TEXT,
    import_batch INTEGER
);

-- Real analyst questions with the tables and query that answer them.
CREATE TABLE IF NOT EXISTS questions (
    uid          TEXT PRIMARY KEY,
    question     TEXT NOT NULL,
    answer       TEXT,
    sql          TEXT,
    tables       TEXT NOT NULL DEFAULT '[]',
    caveats      TEXT,
    tags         TEXT NOT NULL DEFAULT '',
    author       TEXT,
    created_at   TEXT,
    updated_at   TEXT,
    import_batch INTEGER
);

-- Business metric definitions ("forecast accuracy").
CREATE TABLE IF NOT EXISTS metrics (
    uid          TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    definition   TEXT,
    formula      TEXT,
    sources      TEXT NOT NULL DEFAULT '[]',
    owner        TEXT,
    caveats      TEXT,
    tags         TEXT NOT NULL DEFAULT '',
    from_measure TEXT,
    author       TEXT,
    created_at   TEXT,
    updated_at   TEXT,
    import_batch INTEGER
);

-- Machine learning feature dictionary.
CREATE TABLE IF NOT EXISTS features (
    uid                 TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    project             TEXT,
    description         TEXT,
    entity              TEXT,
    grain               TEXT,
    sources             TEXT NOT NULL DEFAULT '[]',   -- ["<asset key>#<column>", …]
    built_by            TEXT,                         -- "<script key>::<function>"
    expression          TEXT,
    window              TEXT,
    known_at_prediction TEXT,                         -- yes | no | unknown
    leakage_reason      TEXT,
    missing             TEXT,
    encoding            TEXT,
    data_type           TEXT,
    models              TEXT NOT NULL DEFAULT '[]',
    importance          TEXT NOT NULL DEFAULT '{}',
    status              TEXT,
    owner               TEXT,
    state               TEXT NOT NULL DEFAULT 'confirmed',  -- confirmed | suggested | rejected
    baseline            TEXT,
    author              TEXT,
    created_at          TEXT,
    updated_at          TEXT,
    import_batch        INTEGER
);

-- What changed between scans: columns added, removed or retyped, row counts, new and dropped items.
CREATE TABLE IF NOT EXISTS schema_history (
    id        INTEGER PRIMARY KEY,
    asset_key TEXT NOT NULL,
    at        TEXT NOT NULL,
    change    TEXT NOT NULL,
    detail    TEXT
);
CREATE INDEX IF NOT EXISTS ix_history_asset ON schema_history(asset_key);

-- Every query sent to a SQL Server, so you can show a DBA exactly what ran.
CREATE TABLE IF NOT EXISTS query_log (
    id       INTEGER PRIMARY KEY,
    at       TEXT NOT NULL,
    source   TEXT,
    database TEXT,
    purpose  TEXT,
    query    TEXT NOT NULL,
    rows     INTEGER,
    ms       INTEGER,
    status   TEXT,
    error    TEXT
);

-- Tables waiting to be profiled, run at a controlled pace once you approve them.
CREATE TABLE IF NOT EXISTS profile_queue (
    asset_key    TEXT PRIMARY KEY,
    state        TEXT NOT NULL DEFAULT 'queued',  -- queued | approved | done | failed | skipped
    full_table   INTEGER NOT NULL DEFAULT 0,
    requested_at TEXT,
    finished_at  TEXT,
    error        TEXT
);

-- Table-to-table relationships for ERDs: declared keys, joins found in code, matching key names.
CREATE TABLE IF NOT EXISTS relationships (
    src_key     TEXT NOT NULL,
    src_columns TEXT NOT NULL,
    dst_key     TEXT NOT NULL,
    dst_columns TEXT NOT NULL,
    how         TEXT NOT NULL,     -- fk | join | name | key
    state       TEXT NOT NULL,     -- declared | suggested | confirmed | rejected
    evidence    TEXT,
    PRIMARY KEY (src_key, src_columns, dst_key, dst_columns)
);

-- Subject areas: groups of related tables for ERDs, worked out automatically and editable by hand.
CREATE TABLE IF NOT EXISTS subject_areas (
    uid          TEXT PRIMARY KEY,
    database_key TEXT NOT NULL,
    name         TEXT NOT NULL,
    manual       INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT
);
CREATE TABLE IF NOT EXISTS area_members (
    area_uid  TEXT NOT NULL,
    asset_key TEXT NOT NULL,
    shared    INTEGER NOT NULL DEFAULT 0,
    manual    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (area_uid, asset_key)
);

-- Links you add by hand ("created by this script"). They survive rescans.
CREATE TABLE IF NOT EXISTS manual_links (
    src_key      TEXT NOT NULL,
    relation     TEXT NOT NULL,
    dst_key      TEXT NOT NULL,
    removed      INTEGER NOT NULL DEFAULT 0,  -- 1 = hide a link the scanner found
    created_at   TEXT,
    author       TEXT,
    import_batch INTEGER,
    PRIMARY KEY (src_key, relation, dst_key)
);

-- Notes and scans merged in from colleagues, so a whole import can be undone.
CREATE TABLE IF NOT EXISTS import_batches (
    id          INTEGER PRIMARY KEY,
    file        TEXT,
    author      TEXT,
    imported_at TEXT NOT NULL,
    summary     TEXT,
    undone      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS source_map (
    author      TEXT NOT NULL,
    their_source TEXT NOT NULL,
    my_source   TEXT NOT NULL,
    PRIMARY KEY (author, their_source)
);

-- Version 2.1 --------------------------------------------------------------------------

-- Things the catalogue suggests and you accept, edit or reject (script versions, file families,
-- SAP meanings, metrics, naming rules...). A rejected suggestion never comes back.
CREATE TABLE IF NOT EXISTS suggestions (
    id          TEXT PRIMARY KEY,           -- "<kind>:<subject>:<detail>", stable across scans
    kind        TEXT NOT NULL,
    subject     TEXT NOT NULL,              -- the item it is about
    title       TEXT NOT NULL,
    detail      TEXT,
    payload     TEXT NOT NULL DEFAULT '{}', -- what accepting it does
    score       REAL NOT NULL DEFAULT 0,
    state       TEXT NOT NULL DEFAULT 'open',  -- open | accepted | rejected | gone (no longer found)
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    decided_by  TEXT
);
CREATE INDEX IF NOT EXISTS ix_suggestions_kind ON suggestions(kind, state);
CREATE INDEX IF NOT EXISTS ix_suggestions_subject ON suggestions(subject);

-- Items that belong together: file families by grain (SOH_D, SOH_W, SOH_M), script versions,
-- a main table and its backup. Each item stays its own item; the group adds a page and a row.
CREATE TABLE IF NOT EXISTS item_groups (
    uid         TEXT PRIMARY KEY,
    type        TEXT NOT NULL,              -- family | versions | backup
    name        TEXT NOT NULL,
    main_key    TEXT,                       -- the current version, the main table
    attrs       TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT,
    author      TEXT,
    import_batch INTEGER
);
CREATE TABLE IF NOT EXISTS group_members (
    group_uid   TEXT NOT NULL,
    asset_key   TEXT NOT NULL,
    label       TEXT,                       -- "daily", "v3", "backup"...
    PRIMARY KEY (group_uid, asset_key)
);
CREATE INDEX IF NOT EXISTS ix_group_members_asset ON group_members(asset_key);

-- How much a link matters: "unimportant" links stay listed but are left out of diagrams and counts.
CREATE TABLE IF NOT EXISTS link_marks (
    src_key     TEXT NOT NULL,
    relation    TEXT NOT NULL,
    dst_key     TEXT NOT NULL,
    mark        TEXT NOT NULL,              -- unimportant
    created_at  TEXT,
    author      TEXT,
    PRIMARY KEY (src_key, relation, dst_key)
);

-- Every change you make (notes, descriptions, item details, links, decisions), with who and when,
-- so it can be shown and undone.
CREATE TABLE IF NOT EXISTS edits (
    id          INTEGER PRIMARY KEY,
    at          TEXT NOT NULL,
    author      TEXT,
    item        TEXT NOT NULL,              -- the asset key (or bp://, kb://...) it is about
    what        TEXT NOT NULL,              -- "note", "description", "item_info.owner", "link", ...
    old_value   TEXT,
    new_value   TEXT,
    target      TEXT,                       -- JSON: how to put the old value back
    undone      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_edits_item ON edits(item);

-- SQL queries found anywhere (scripts, notebooks, .sql files, Excel, Power BI, Access, documents)
-- or pasted in. The same query found in several places is one row with several places.
CREATE TABLE IF NOT EXISTS sql_queries (
    uid         TEXT PRIMARY KEY,           -- hash of the normalised SQL
    name        TEXT NOT NULL,
    named_by    TEXT NOT NULL DEFAULT 'auto',  -- auto | you
    sql         TEXT NOT NULL,
    tables      TEXT NOT NULL DEFAULT '[]',
    analysis    TEXT NOT NULL DEFAULT '{}',
    first_seen  TEXT,
    last_seen   TEXT,
    status      TEXT NOT NULL DEFAULT 'active'  -- active | missing
);
CREATE TABLE IF NOT EXISTS sql_query_places (
    query_uid   TEXT NOT NULL,
    item        TEXT NOT NULL,              -- the script, workbook, report, Access file... it is in
    location    TEXT NOT NULL DEFAULT '',   -- line, function, query name
    name_hint   TEXT,
    source      TEXT,
    PRIMARY KEY (query_uid, item, location)
);
CREATE INDEX IF NOT EXISTS ix_query_places_item ON sql_query_places(item);

-- Findings from checking SQL, and the ones you marked "fine here".
CREATE TABLE IF NOT EXISTS sql_findings_ok (
    query_uid   TEXT NOT NULL,
    rule        TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    author      TEXT,
    at          TEXT,
    PRIMARY KEY (query_uid, rule, detail)
);

-- Column-level lineage: renames and copies between columns (Units -> qty), from code and SQL.
CREATE TABLE IF NOT EXISTS column_links (
    src_item    TEXT NOT NULL,
    src_column  TEXT NOT NULL,
    dst_item    TEXT NOT NULL,
    dst_column  TEXT NOT NULL,
    how         TEXT NOT NULL,              -- rename | alias | copy
    evidence    TEXT,                       -- script and line
    source      TEXT,
    PRIMARY KEY (src_item, src_column, dst_item, dst_column)
);

-- Naming rules for a database or project, pasted as text or worked out from existing names.
CREATE TABLE IF NOT EXISTS naming_rules (
    uid         TEXT PRIMARY KEY,
    scope       TEXT NOT NULL,              -- the database or project key
    applies_to  TEXT NOT NULL,              -- table | view | procedure | column | script | file
    check_type  TEXT NOT NULL,              -- prefix | suffix | case | pattern | max_length | no_spaces | ...
    value       TEXT,
    text        TEXT,                       -- the rule as written
    state       TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT,
    author      TEXT
);
CREATE TABLE IF NOT EXISTS naming_exceptions (
    rule_uid    TEXT NOT NULL,
    asset_key   TEXT NOT NULL,
    column_name TEXT NOT NULL DEFAULT '',
    reason      TEXT,
    PRIMARY KEY (rule_uid, asset_key, column_name)
);

-- Scheduled jobs imported from a CSV (a Task Scheduler export, a job list), and the scripts they run.
CREATE TABLE IF NOT EXISTS schedules (
    uid         TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    command     TEXT,
    timing      TEXT,
    raw         TEXT NOT NULL DEFAULT '{}',  -- the whole CSV row
    import_file TEXT,
    imported_at TEXT
);
CREATE TABLE IF NOT EXISTS schedule_links (
    schedule_uid TEXT NOT NULL,
    asset_key    TEXT NOT NULL,
    state        TEXT NOT NULL DEFAULT 'confirmed',  -- confirmed | suggested | rejected
    how          TEXT,
    PRIMARY KEY (schedule_uid, asset_key)
);

-- Installed conda environments read from conda-meta, and which project runs in which.
CREATE TABLE IF NOT EXISTS conda_envs (
    name        TEXT PRIMARY KEY,
    path        TEXT NOT NULL,
    python      TEXT,
    packages    TEXT NOT NULL DEFAULT '[]',   -- [{"name", "version", "channel", "build"}]
    scanned_at  TEXT
);
CREATE TABLE IF NOT EXISTS project_envs (
    project_key TEXT PRIMARY KEY,
    env_name    TEXT NOT NULL,
    how         TEXT
);

-- Commits that touched each script, read from the project's git history.
CREATE TABLE IF NOT EXISTS git_history (
    asset_key   TEXT NOT NULL,
    commit_id   TEXT NOT NULL,
    at          TEXT NOT NULL,
    author      TEXT,
    message     TEXT,
    PRIMARY KEY (asset_key, commit_id)
);
CREATE INDEX IF NOT EXISTS ix_git_asset ON git_history(asset_key);

-- Things you follow up on, pinned to the home page; and what you looked at recently.
CREATE TABLE IF NOT EXISTS follow_ups (
    item        TEXT PRIMARY KEY,
    note        TEXT,
    created_at  TEXT
);
CREATE TABLE IF NOT EXISTS recent_views (
    item        TEXT PRIMARY KEY,
    at          TEXT NOT NULL
);

-- Documents: reference documents on business projects (every version kept), and screenshots.
CREATE TABLE IF NOT EXISTS documents (
    uid         TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,              -- the business project or item it belongs to
    title       TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'reference',  -- reference | screenshot
    created_at  TEXT,
    author      TEXT
);
CREATE TABLE IF NOT EXISTS document_versions (
    doc_uid     TEXT NOT NULL,
    version     INTEGER NOT NULL,
    file_name   TEXT,
    mime        TEXT,
    content     BLOB,
    text        TEXT,                       -- extracted text, for search and highlighting
    added_at    TEXT,
    author      TEXT,
    PRIMARY KEY (doc_uid, version)
);
"""

SCHEMA_VERSION = 3  # 3 = version 2.1

# Columns added to v1 tables. Added in place by the upgrade; nothing is dropped.
ADDED_COLUMNS = {
    "notes": {"uid": "TEXT", "author": "TEXT", "confirmed_at": "TEXT", "archived": "INTEGER NOT NULL DEFAULT 0",
              "import_batch": "INTEGER"},
    "links": {"how": "TEXT", "match_note": "TEXT"},
    "profiles": {"histogram": "TEXT", "percentiles": "TEXT", "hints": "TEXT"},
    "folders": {"canonical": "TEXT", "roles": "TEXT"},
    "libraries": {"role": "TEXT NOT NULL DEFAULT 'library'", "from_folder": "TEXT"},
    "item_info": {"relevance": "TEXT", "sap_name": "TEXT"},
    "column_info": {"sap_name": "TEXT"},
}


class UpgradeError(RuntimeError):
    """The catalogue couldn't be upgraded. The message says where the untouched backup is."""

TABLE_KINDS = ("table", "view")
MANUAL_SOURCE = "manual"  # SQL you paste in by hand; never touched by scans
NOTE_TYPES = {"note": "Note", "issue": "Data issue", "example": "Example values"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def split_tags(tags: str) -> list[str]:
    return [t.strip().lower() for t in re.split(r"[,;]", tags or "") if t.strip()]


class Catalogue:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        memory = str(path) == ":memory:"
        existed = not memory and self.path.exists() and self.path.stat().st_size > 0
        if not memory:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.upgrade_backup: Path | None = None
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        has_data = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'assets'").fetchone() is not None
        if existed and has_data and version < SCHEMA_VERSION:
            # Upgrading a catalogue made by an older version: keep an untouched copy first.
            self.upgrade_backup = self._safety_copy(version)
        try:
            self.conn.executescript(SCHEMA)
            self._migrate()
        except Exception as e:
            self.conn.close()
            if self.upgrade_backup:
                raise UpgradeError(
                    f"Couldn't upgrade the catalogue at {self.path} ({type(e).__name__}: {e}). Nothing was changed "
                    f"in your notes: an untouched copy is at {self.upgrade_backup}. Copy it back over the "
                    "catalogue file to keep using the old version.") from e
            raise
        self.fts = self._create_search_index()

    def close(self) -> None:
        self.conn.close()

    def _safety_copy(self, version: int) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.path.with_name(f"{self.path.stem}.before-v{SCHEMA_VERSION}-{stamp}{self.path.suffix or '.db'}")
        dest = sqlite3.connect(str(target))
        try:
            self.conn.backup(dest)
        finally:
            dest.close()
        return target

    def _migrate(self) -> None:
        """Bring catalogues made by older versions up to date. Only adds: no table, column or note is removed."""
        with self.conn:
            for table, cols in ADDED_COLUMNS.items():
                have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
                for name, decl in cols.items():
                    if name not in have:
                        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            note_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(notes)")}
            if "note_type" not in note_cols:
                self.conn.execute("ALTER TABLE notes ADD COLUMN note_type TEXT NOT NULL DEFAULT 'note'")
            if "status" not in note_cols:
                self.conn.execute("ALTER TABLE notes ADD COLUMN status TEXT NOT NULL DEFAULT 'open'")
            # Every note gets an id that is the same on every machine, so notes can be shared and merged.
            self.conn.execute("UPDATE notes SET uid = lower(hex(randomblob(16))) WHERE uid IS NULL")
            self.conn.execute("UPDATE notes SET author = ? WHERE author IS NULL", (self.author(),))
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_notes_uid ON notes(uid)")
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # ---- settings -------------------------------------------------------

    def setting(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any, *, quiet: bool = False) -> None:
        """Save a setting. quiet=True for bookkeeping that shouldn't count as a change to the catalogue."""
        with self.conn:
            self.conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                              "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, json.dumps(value)))
        if key != "revision" and not quiet:
            self.changed()

    def author(self) -> str:
        """Who notes are written by on this machine: the name in Settings, or your Windows user name."""
        try:
            row = self.conn.execute("SELECT value FROM settings WHERE key = 'author'").fetchone()
        except sqlite3.OperationalError:
            row = None
        if row and json.loads(row["value"]):
            return json.loads(row["value"])
        try:
            return getpass.getuser()
        except Exception:
            return "me"

    def revision(self) -> int:
        """A counter that goes up whenever the catalogue changes, so the app knows when cached pages are stale."""
        return int(self.setting("revision", 0))

    def changed(self) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO settings (key, value) VALUES ('revision', '1') "
                              "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1")

    def _create_search_index(self) -> bool:
        try:
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS search_idx USING fts5("
                "key UNINDEXED, kind UNINDEXED, source UNINDEXED, name, body, tokenize='unicode61')"
            )
            return True
        except sqlite3.OperationalError:
            # SQLite without FTS5: fall back to a plain table searched with LIKE.
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS search_idx (key TEXT, kind TEXT, source TEXT, name TEXT, body TEXT)"
            )
            return False

    # ---- scanning -------------------------------------------------------

    @contextmanager
    def scan(self, source: str, prefix: str | None = None) -> Iterator["Catalogue"]:
        """Wrap one source's scan in a transaction.

        Everything the source found last time is marked 'missing' up front and
        flipped back to 'active' as the scan sees it again, so dropped tables or
        deleted scripts show up as missing instead of silently vanishing. If the
        scan fails, nothing changes.
        """
        started = now()
        # A prefix limits the scan to one database ("sql://src/db") and what's inside it.
        exact, inside = (prefix.lower(), prefix.lower() + "/%") if prefix else ("%", "%")
        scope = "(key LIKE ? OR key LIKE ?)"
        from .history import snapshot
        before = snapshot(self, source, exact, inside)
        with self.conn:
            self.conn.execute(f"UPDATE assets SET status = 'missing' WHERE source = ? AND {scope}",
                              (source, exact, inside))
            self.conn.execute("DELETE FROM links WHERE source = ? AND (src_key LIKE ? OR src_key LIKE ?)",
                              (source, exact, inside))
            self.conn.execute(
                f"DELETE FROM columns WHERE asset_key IN (SELECT key FROM assets WHERE source = ? AND {scope})",
                (source, exact, inside))
            yield self
            from .history import record_changes
            record_changes(self, before, snapshot(self, source, exact, inside), first_scan=not before)
            counts = dict(self.conn.execute(
                "SELECT kind, COUNT(*) FROM assets WHERE source = ? AND status = 'active' GROUP BY kind",
                (source,),
            ).fetchall())
            self.conn.execute(
                "INSERT INTO scans (source, started_at, finished_at, summary) VALUES (?, ?, ?, ?)",
                (source, started, now(), json.dumps(counts)),
            )
        self.resolve_refs()
        from .codemap import resolve_imports
        resolve_imports(self)
        from .links import apply_manual_links
        apply_manual_links(self)
        self.rebuild_search()

    def upsert_asset(self, key: str, kind: str, name: str, source: str, *, parent: str | None = None,
                     description: str | None = None, attrs: dict[str, Any] | None = None) -> None:
        ts = now()
        attrs = dict(attrs or {})
        old = self.conn.execute("SELECT attrs FROM assets WHERE key = ?", (key,)).fetchone()
        if old:
            # Remember when the object itself changed (SQL modify_date, file modified time),
            # so the review list can flag it.
            prev = json.loads(old["attrs"])
            if attrs.get("modified") and prev.get("modified") and str(attrs["modified"]) != str(prev["modified"]):
                attrs["changed_at"] = ts
                attrs["previous_modified"] = prev["modified"]
            else:
                for k in ("changed_at", "previous_modified"):
                    if k in prev and k not in attrs:
                        attrs[k] = prev[k]
            if prev.get("added_in_rescan"):
                attrs["added_in_rescan"] = True
        elif self.conn.execute("SELECT 1 FROM scans WHERE source = ? LIMIT 1", (source,)).fetchone():
            attrs["added_in_rescan"] = True  # appeared after this source's first scan
        self.conn.execute(
            """
            INSERT INTO assets (key, kind, name, source, parent, description, attrs, status, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                kind = excluded.kind, name = excluded.name, source = excluded.source,
                parent = excluded.parent, description = excluded.description,
                attrs = excluded.attrs, status = 'active', last_seen = excluded.last_seen
            """,
            (key, kind, name, source, parent, description, json.dumps(attrs, default=str), ts, ts),
        )

    def set_columns(self, asset_key: str, columns: list[dict[str, Any]]) -> None:
        self.conn.execute("DELETE FROM columns WHERE asset_key = ?", (asset_key,))
        self.conn.executemany(
            "INSERT OR REPLACE INTO columns (asset_key, name, ordinal, data_type, nullable, description) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(asset_key, c["name"], c.get("ordinal"), c.get("data_type"), c.get("nullable"), c.get("description"))
             for c in columns],
        )

    def add_link(self, src_key: str, relation: str, dst_ref: str, source: str, *,
                 dst_key: str | None = None, hint: str | None = None, evidence: str | None = None,
                 how: str | None = None) -> None:
        """Record that src uses dst. dst_ref is "table:<name>" for a table to be matched later,
        "file:<path>" for a file, or an exact asset key. `how` says how the link was found
        ("pandas read_csv", "path from config file"…), so you can judge how sure it is."""
        self.conn.execute(
            "INSERT OR IGNORE INTO links (src_key, relation, dst_ref, dst_key, source, hint, evidence, how) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (src_key, relation, dst_ref, dst_key, source, hint, evidence, how),
        )

    def resolve_refs(self) -> None:
        """Match "table:<name>" references from code to the tables and views you have scanned.

        "sales", "dbo.sales", "db.dbo.sales" and "server.db.dbo.sales" are all
        accepted. When several tables match, the link's hint ("<source>/<db>")
        and then the dbo schema break the tie; if it is still ambiguous the
        reference stays unresolved rather than guessing.
        """
        by_object: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in self.conn.execute(
            "SELECT key, source, attrs FROM assets WHERE kind IN ('table', 'view', 'procedure') AND status = 'active'"
        ):
            a = json.loads(row["attrs"])
            by_object[a.get("object", "").lower()].append({
                "key": row["key"], "source": row["source"].lower(),
                "database": a.get("database", "").lower(), "schema": a.get("schema", "").lower(),
                "server": a.get("server", "").lower(),
            })

        updates = []
        for link in self.conn.execute("SELECT rowid, dst_ref, hint FROM links WHERE dst_ref LIKE 'table:%'"):
            parts = link["dst_ref"][len("table:"):].split(".")
            if "{}" in parts[-1]:  # a name built at run time, e.g. f"sales_{region}"
                pattern = re.compile(re.escape(parts[-1]).replace(r"\{\}", ".+") + "$")
                cands = [c for obj, cs in by_object.items() if pattern.match(obj) for c in cs]
            else:
                cands = by_object.get(parts[-1], [])
            if len(parts) >= 2:
                cands = [c for c in cands if c["schema"] == parts[-2]]
            if len(parts) >= 3:
                cands = [c for c in cands if c["database"] == parts[-3]]
            if len(parts) >= 4:
                cands = [c for c in cands if parts[-4] in (c["server"], c["source"])]
            if len(cands) > 1 and link["hint"]:
                hinted = [c for c in cands if _matches_hint(c, link["hint"])]
                cands = hinted or cands
            if len(cands) > 1 and len(parts) == 1:
                cands = [c for c in cands if c["schema"] == "dbo"] or cands
            updates.append((cands[0]["key"] if len(cands) == 1 else None,
                            f"{len(cands)} tables have this name" if len(cands) > 1 else None, link["rowid"]))
        updates += self._resolve_file_refs()
        with self.conn:
            self.conn.executemany("UPDATE links SET dst_key = ?, match_note = ? WHERE rowid = ?", updates)

    def _resolve_file_refs(self) -> list[tuple[str | None, str | None, int]]:
        """Match "file:<path>" references from code to catalogued CSV/Parquet files.

        Relative paths match files in the same project first, then in any folder
        whose path ends the same way. f-string parts ("{}") act as wildcards, and
        when several files match (e.g. dated extracts) the newest one wins. When
        nothing matches that way but a file with the same name is catalogued in
        another folder, it is linked anyway and flagged as a possible wrong folder.
        """
        files = []
        for row in self.conn.execute(
                "SELECT key, source, attrs FROM assets WHERE kind IN ('data_file', 'series_file', 'workbook', "
                "'report', 'access_db') AND status = 'active'"):
            a = json.loads(row["attrs"])
            # A file in a dated series links to the series, not to one month's copy.
            files.append({"key": a.get("in_series") or row["key"], "source": row["source"],
                          "path": _norm_path(a.get("path", "")), "mtime": a.get("mtime") or 0})
        if not files:
            return [(None, None, r["rowid"]) for r in self.conn.execute(
                "SELECT rowid FROM links WHERE dst_ref LIKE 'file:%'")]

        # An exact reference can only match files with the same file name, so look those up directly
        # rather than comparing every reference with every file (slow with thousands of each).
        by_name: dict[str, list[dict]] = {}
        for f in files:
            by_name.setdefault(f["path"].rsplit("/", 1)[-1], []).append(f)
        updates = []
        for link in self.conn.execute("SELECT rowid, dst_ref, source FROM links WHERE dst_ref LIKE 'file:%'"):
            ref = _norm_path(link["dst_ref"][len("file:"):])
            while ref.startswith("./"):
                ref = ref[2:]
            pattern = re.compile(fnmatch.translate(ref.replace("{}", "*")).replace(".*", "[^/]*")) \
                if "{}" in ref else None
            absolute = bool(re.match(r"^([a-z]:/|/)", ref))
            depth = ref.count("/") + 1

            def hit(f: dict, ref: str = ref, pattern=pattern, absolute: bool = absolute, depth: int = depth) -> bool:
                if absolute:
                    target = f["path"]
                else:  # compare the same number of trailing path segments
                    target = "/".join(f["path"].split("/")[-depth:])
                return pattern.fullmatch(target) is not None if pattern else target == ref

            leaf = ref.rsplit("/", 1)[-1]
            candidates = files if pattern else by_name.get(leaf, [])
            matches = [f for f in candidates if hit(f)]
            note = None
            if not matches:
                # Same file name in some other folder: link it, but say so.
                if pattern:
                    leaf_rx = re.compile(fnmatch.translate(leaf.replace("{}", "*")))
                    matches = [f for f in files if leaf_rx.fullmatch(f["path"].rsplit("/", 1)[-1])]
                else:
                    matches = list(by_name.get(leaf, []))
                if matches:
                    best = max(matches, key=lambda f: (f["source"] == link["source"], f["mtime"]))
                    note = f"found in another folder: {best['path']}"
            same_project = [f for f in matches if f["source"] == link["source"]]
            matches = same_project or matches
            best = max(matches, key=lambda f: f["mtime"])["key"] if matches else None
            distinct = {f["key"] for f in matches}
            if len(distinct) > 1:
                more = f"{len(distinct)} files match; linked the newest"
                note = f"{note}; {more}" if note else more
            updates.append((best, note, link["rowid"]))
        return updates

    # ---- search ---------------------------------------------------------

    def _search_rows(self, key: str | None = None) -> list[tuple]:
        """Index rows for scanned items and notes: all of them, or just the one item `key` (and its columns' notes)."""
        def where(col: str) -> tuple[str, tuple]:
            if key is None:
                return "", ()
            return f" AND ({col} = ? OR substr({col}, 1, ?) = ?)", (key, len(key) + 1, key + "#")
        notes_by_key: dict[str, list[str]] = defaultdict(list)
        w, p = where("asset_key")
        for n in self.conn.execute("SELECT asset_key, body, tags FROM notes WHERE archived = 0" + w, p):
            base = n["asset_key"].split("#", 1)[0]
            notes_by_key[base].append(f"{n['body']} {n['tags']}")
        cols_by_key: dict[str, list[str]] = defaultdict(list)
        w, p = where("c.asset_key")
        for c in self.conn.execute(
                "SELECT c.asset_key, c.name, c.description, ci.description AS mine FROM columns c "
                "LEFT JOIN column_info ci ON ci.asset_key = c.asset_key AND ci.column_name = c.name WHERE 1 = 1" + w, p):
            cols_by_key[c["asset_key"]].append(f"{c['name']} {c['description'] or ''} {c['mine'] or ''}")
        w, p = where("asset_key")
        info_by_key = {r["asset_key"]: " ".join(str(r[k] or "") for k in ("purpose", "grain", "owner", "refresh"))
                       for r in self.conn.execute("SELECT * FROM item_info WHERE 1 = 1" + w, p)}

        rows = []
        seen = set()
        w, p = ("", ()) if key is None else (" AND key = ?", (key,))
        for a in self.conn.execute("SELECT key, kind, source, name, description, attrs FROM assets "
                                   "WHERE kind != 'series_file'" + w, p):
            attrs = json.loads(a["attrs"])
            extra = " ".join(str(attrs.get(k, "")) for k in ("functions", "imports", "classes", "sql", "tags",
                                                               "measures", "pattern"))
            body = " ".join([a["description"] or "", info_by_key.get(a["key"], ""), extra,
                             " ".join(cols_by_key[a["key"]]), " ".join(notes_by_key[a["key"]])])
            rows.append((a["key"], a["kind"], a["source"], _searchable(a["name"]), _searchable(body)))
            seen.add(a["key"])
        for base, texts in notes_by_key.items():
            if base not in seen:  # notes on files or unmatched tables
                rows.append((base, "note", "", _searchable(base), _searchable(" ".join(texts))))
        return rows

    def reindex_search(self, asset_key: str) -> None:
        """Refresh one item's entry in the full-text index (after a note or description changes), which is
        much quicker than rebuilding the whole index on a big catalogue."""
        key = asset_key.split("#", 1)[0]
        rows = self._search_rows(key)
        with self.conn:
            # Knowledge, questions, metrics and the like have their own rows, which this leaves alone.
            self.conn.execute("DELETE FROM search_idx WHERE key = ? AND kind NOT IN ('knowledge', 'question', "
                              "'metric', 'feature', 'key', 'bproject')", (key,))
            self.conn.executemany("INSERT INTO search_idx (key, kind, source, name, body) VALUES (?, ?, ?, ?, ?)", rows)
        self.changed()

    def rebuild_search(self) -> None:
        """Rebuild the full-text index."""
        rows = self._search_rows()
        # Knowledge that isn't a scanned item: articles, questions, metrics, features, keys, business projects.
        for sql, prefix, kind in (
                ("SELECT uid AS id, title AS name, body || ' ' || tags || ' ' || triggers || ' ' || COALESCE(section, '') "
                 "AS body FROM knowledge", "kb://", "knowledge"),
                ("SELECT uid AS id, question AS name, COALESCE(answer, '') || ' ' || COALESCE(sql, '') || ' ' || tags "
                 "|| ' ' || COALESCE(caveats, '') AS body FROM questions", "q://", "question"),
                ("SELECT uid AS id, name, COALESCE(definition, '') || ' ' || COALESCE(formula, '') || ' ' || tags "
                 "|| ' ' || COALESCE(caveats, '') AS body FROM metrics", "metric://", "metric"),
                ("SELECT uid AS id, name, COALESCE(description, '') || ' ' || COALESCE(project, '') || ' ' || "
                 "COALESCE(entity, '') || ' ' || COALESCE(expression, '') AS body FROM features WHERE state != 'rejected'",
                 "feature://", "feature"),
                ("SELECT name AS id, name, COALESCE(description, '') || ' ' || aliases AS body FROM keys "
                 "WHERE state = 'active'", "key://", "key"),
                ("SELECT uid AS id, name, COALESCE(purpose, '') || ' ' || COALESCE(notes, '') AS body FROM bprojects",
                 "bp://", "bproject")):
            for r in self.conn.execute(sql):
                rows.append((f"{prefix}{r['id']}", kind, "", _searchable(r["name"]), _searchable(r["body"] or "")))
        with self.conn:
            self.conn.execute("DELETE FROM search_idx")
            self.conn.executemany("INSERT INTO search_idx (key, kind, source, name, body) VALUES (?, ?, ?, ?, ?)", rows)
        self.changed()

    def search(self, text: str, *, kind: str | None = None, source: str | None = None,
               limit: int = 100, include_hidden: bool = False, kinds: tuple[str, ...] | None = None) -> list[sqlite3.Row]:
        """Ranked matches. Each row has a "snippet" showing where the words matched (with **bold** hits).
        Deprecated and retired items sink to the bottom. Items marked not relevant and older versions of
        scripts are left out unless `include_hidden`. `kinds` limits the results to several kinds."""
        tokens = re.findall(r"\w+", text or "")
        if not tokens:
            return self.list_assets(kind=kind, source=source, limit=limit, include_hidden=include_hidden, kinds=kinds)
        filters, params = [], []
        if not include_hidden:
            filters.append(f"s.key NOT IN ({HIDDEN_KEYS})")
        if kinds:
            filters.append(f"s.kind IN ({', '.join('?' * len(kinds))})")
            params += list(kinds)
        if self.fts:
            filters.append("search_idx MATCH ?")
            params.append(" ".join(f'"{t}"*' for t in tokens))
            order = "bm25(search_idx, 0, 0, 0, 10.0, 1.0)"
            snippet = "snippet(search_idx, 4, '**', '**', '…', 14)"
        else:
            for t in tokens:
                filters.append("(s.name LIKE ? OR s.body LIKE ?)")
                params += [f"%{t}%", f"%{t}%"]
            order = "s.name"
            snippet = "''"
        if kind:
            filters.append("s.kind = ?")
            params.append(kind)
        if source:
            filters.append("s.source = ?")
            params.append(source)
        sql = f"""
            SELECT s.key, s.kind, s.source, COALESCE(a.name, {_KNOWLEDGE_NAME}, s.key) AS name, a.description, a.status,
                   {snippet} AS snippet, i.lifecycle, i.trust, i.sensitive, i.purpose,
                   (SELECT COUNT(*) FROM notes n WHERE n.asset_key = s.key OR n.asset_key LIKE s.key || '#%') AS note_count
            FROM search_idx s LEFT JOIN assets a ON a.key = s.key LEFT JOIN item_info i ON i.asset_key = s.key
            WHERE {' AND '.join(filters)}
            ORDER BY CASE WHEN i.lifecycle IN ('deprecated', 'retired') OR i.trust = 'do not use' THEN 1 ELSE 0 END,
                     {order} LIMIT ?
        """
        return self.conn.execute(sql, (*params, limit)).fetchall()

    def matching_columns(self, key: str, text: str) -> list[dict[str, Any]]:
        """Columns of one item whose name or description matches the search words, with their profile."""
        tokens = [t.lower() for t in re.findall(r"\w+", text or "")]
        if not tokens:
            return []
        out = []
        prof = {p["column_name"].lower(): dict(p) for p in self.profile(key)}
        mine = {r["column_name"].lower(): r for r in self.conn.execute(
            "SELECT column_name, description, sensitive FROM column_info WHERE asset_key = ?", (key,))}
        cols = [dict(c) for c in self.columns(key)] or \
            [{"name": p["column_name"], "data_type": p["data_type"], "description": None} for p in prof.values()]
        for c in cols:
            low = c["name"].lower()
            m = mine.get(low)
            desc = (m["description"] if m and m["description"] else None) or c.get("description") or ""
            hay = f"{_searchable(c['name'])} {desc}".lower()
            if all(any(w.startswith(t) for w in re.findall(r"\w+", hay)) for t in tokens):
                out.append({**c, "description": desc, "profile": prof.get(low),
                            "sensitive": bool(m and m["sensitive"])})
        return out

    def search_columns(self, text: str, limit: int = 300) -> list[dict[str, Any]]:
        """Every column, in any table or file, whose name or description matches the words."""
        tokens = [t.lower() for t in re.findall(r"\w+", text or "")]
        if not tokens:
            return []
        clauses = " AND ".join(["(lower(c.name) LIKE ? OR lower(COALESCE(ci.description, c.description, '')) LIKE ?)"]
                               * len(tokens))
        params = [p for t in tokens for p in (f"%{t}%", f"%{t}%")]
        rows = self.conn.execute(
            f"""SELECT c.asset_key, a.name AS item, a.kind, a.source, c.name AS column_name, c.data_type,
                       COALESCE(ci.description, c.description) AS description, COALESCE(ci.sensitive, 0) AS sensitive,
                       p.row_count, p.null_count, p.distinct_count, p.min_value, p.max_value, p.top_values
                FROM columns c JOIN assets a ON a.key = c.asset_key AND a.status = 'active'
                LEFT JOIN column_info ci ON ci.asset_key = c.asset_key AND ci.column_name = c.name
                LEFT JOIN profiles p ON p.asset_key = c.asset_key AND lower(p.column_name) = lower(c.name)
                WHERE {clauses} ORDER BY lower(c.name), a.name LIMIT ?""", (*params, limit)).fetchall()
        return [dict(r) for r in rows]

    # ---- reading --------------------------------------------------------

    def list_assets(self, *, kind: str | None = None, source: str | None = None,
                    limit: int = 100, include_hidden: bool = False,
                    kinds: tuple[str, ...] | None = None) -> list[sqlite3.Row]:
        filters, params = ["kind != 'series_file'"], []
        if not include_hidden:
            filters.append(f"key NOT IN ({HIDDEN_KEYS})")
        if kinds:
            filters.append(f"kind IN ({', '.join('?' * len(kinds))})")
            params += list(kinds)
        if kind:
            filters.append("kind = ?")
            params.append(kind)
        if source:
            filters.append("source = ?")
            params.append(source)
        return self.conn.execute(
            f"""SELECT key, kind, source, name, description, status,
                       (SELECT COUNT(*) FROM notes n WHERE n.asset_key = a.key OR n.asset_key LIKE a.key || '#%') AS note_count
                FROM assets a WHERE {' AND '.join(filters)} ORDER BY kind, name LIMIT ?""",
            (*params, limit),
        ).fetchall()

    def get_asset(self, key: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM assets WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        asset = dict(row)
        asset["attrs"] = json.loads(asset["attrs"])
        return asset

    def columns(self, key: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM columns WHERE asset_key = ? ORDER BY ordinal, name", (key,)).fetchall()

    def links_from(self, key: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT l.*, a.name AS dst_name, a.kind AS dst_kind FROM links l
               LEFT JOIN assets a ON a.key = l.dst_key WHERE l.src_key = ? ORDER BY l.relation, l.dst_ref""",
            (key,),
        ).fetchall()

    def links_from_children(self, parent_key: str) -> list[sqlite3.Row]:
        """Everything the scripts inside a project use, for the project-level view."""
        return self.conn.execute(
            """SELECT l.*, a.name AS dst_name, a.kind AS dst_kind FROM links l
               JOIN assets s ON s.key = l.src_key AND s.parent = ?
               LEFT JOIN assets a ON a.key = l.dst_key ORDER BY l.relation, l.dst_ref""",
            (parent_key,),
        ).fetchall()

    def links_to(self, key: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT l.*, a.name AS src_name, a.kind AS src_kind FROM links l
               LEFT JOIN assets a ON a.key = l.src_key WHERE l.dst_key = ? OR l.dst_ref = ?
               ORDER BY l.relation, a.name""",
            (key, key),
        ).fetchall()

    def children(self, key: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT key, kind, name, description, status, attrs FROM assets WHERE parent = ? ORDER BY name", (key,)
        ).fetchall()

    def needs_documentation(self, limit: int = 50) -> list[sqlite3.Row]:
        """Tables and views with no description and no notes, most used by your code first."""
        return self.conn.execute(
            """SELECT a.key, a.kind, a.source, a.name, a.description, a.status, 0 AS note_count,
                      (SELECT COUNT(DISTINCT l.src_key) FROM links l WHERE l.dst_key = a.key) AS used_by
               FROM assets a
               WHERE a.kind IN ('table', 'view') AND a.status = 'active'
                 AND COALESCE(a.description, '') = ''
                 AND NOT EXISTS (SELECT 1 FROM notes n WHERE n.asset_key = a.key OR n.asset_key LIKE a.key || '#%')
               ORDER BY used_by DESC, a.name LIMIT ?""",
            (limit,),
        ).fetchall()

    def unresolved_refs(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT dst_ref, COUNT(DISTINCT src_key) AS used_by, MIN(evidence) AS example
               FROM links WHERE dst_key IS NULL AND dst_ref LIKE 'table:%'
               GROUP BY dst_ref ORDER BY used_by DESC, dst_ref"""
        ).fetchall()

    def stats(self) -> dict[str, Any]:
        kinds = dict(self.conn.execute(
            "SELECT kind, COUNT(*) FROM assets WHERE status = 'active' AND kind != 'series_file' GROUP BY kind").fetchall())
        notes = self.conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        last = self.conn.execute(
            "SELECT source, MAX(finished_at) AS at FROM scans GROUP BY source ORDER BY source").fetchall()
        return {"kinds": kinds, "notes": notes, "last_scans": {r["source"]: r["at"] for r in last}}

    def sources(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT DISTINCT source FROM assets ORDER BY source")]

    def kinds(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT DISTINCT kind FROM assets ORDER BY kind")]

    # ---- pasted SQL -----------------------------------------------------

    def save_snippet(self, title: str, sql: str, *, description: str = "", tags: str = "",
                     database: str | None = None, key: str | None = None) -> str:
        """Save a piece of SQL you pasted in, and link it to the tables it reads and writes.

        `database` ("<source>/<db>") says where bare table names live. Pass `key`
        to update an existing snippet.
        """
        from .sqlrefs import extract_refs

        key = key or f"{MANUAL_SOURCE}://sql/{uuid.uuid4().hex[:12]}"
        attrs = {"sql": sql.strip(), "database": database or "", "tags": ", ".join(split_tags(tags))}
        with self.conn:
            self.upsert_asset(key, "sql_snippet", title.strip() or "Untitled SQL", MANUAL_SOURCE,
                              description=description.strip() or None, attrs=attrs)
            self.conn.execute("DELETE FROM links WHERE src_key = ?", (key,))
            for ref in extract_refs(sql):
                self.add_link(key, ref.relation, f"table:{ref.name}", MANUAL_SOURCE,
                              hint=database or None, evidence="pasted SQL")
        self.resolve_refs()
        self.rebuild_search()
        return key

    def delete_snippet(self, key: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM assets WHERE key = ? AND source = ?", (key, MANUAL_SOURCE))
            self.conn.execute("DELETE FROM links WHERE src_key = ?", (key,))
            self.conn.execute("DELETE FROM notes WHERE asset_key = ?", (key,))
        self.rebuild_search()

    # ---- folders added from the app ---------------------------------------

    def add_folder(self, name: str, path: str, recursive: bool = True) -> None:
        from .duplicates import canonical, drive_aliases
        with self.conn:
            self.conn.execute(
                "INSERT INTO folders (name, path, recursive, added_at, canonical) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET path = excluded.path, recursive = excluded.recursive, "
                "canonical = excluded.canonical",
                (name, path, int(recursive), now(), canonical(path, drive_aliases(self))))
        self.changed()

    def folder_overlaps(self, path: str) -> list[str]:
        """Folder sources already covering this path (the same folder, a parent or a child),
        so the same files aren't catalogued twice."""
        from .duplicates import _inside, canonical, drive_aliases
        aliases = drive_aliases(self)
        new = canonical(path, aliases)
        out = []
        for r in self.folders():
            old = canonical(r["path"], aliases)
            if _inside(new, old) or _inside(old, new):
                out.append(r["name"])
        return out

    def folders(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM folders ORDER BY name").fetchall()

    def remove_folder(self, name: str) -> None:
        """Forget a folder and everything scanned from it (notes are kept)."""
        with self.conn:
            self.conn.execute("DELETE FROM folders WHERE name = ?", (name,))
            self.conn.execute("DELETE FROM profiles WHERE asset_key IN (SELECT key FROM assets WHERE source = ?)", (name,))
            self.conn.execute("DELETE FROM columns WHERE asset_key IN (SELECT key FROM assets WHERE source = ?)", (name,))
            self.conn.execute("DELETE FROM links WHERE source = ?", (name,))
            self.conn.execute("DELETE FROM assets WHERE source = ?", (name,))
        self.resolve_refs()
        from .codemap import resolve_imports
        resolve_imports(self)
        self.rebuild_search()

    def add_library(self, name: str, path: str, role: str = "library", from_folder: str | None = None) -> None:
        """A Python folder added from the app: a home-built library, or (role "project") the code in a data
        folder you added."""
        with self.conn:
            self.conn.execute("INSERT INTO libraries (name, path, added_at, role, from_folder) VALUES (?, ?, ?, ?, ?) "
                              "ON CONFLICT(name) DO UPDATE SET path = excluded.path, role = excluded.role, "
                              "from_folder = excluded.from_folder", (name, path, now(), role, from_folder))

    def libraries(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM libraries ORDER BY name").fetchall()

    def remove_library(self, name: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM libraries WHERE name = ?", (name,))
        self.remove_folder(name)  # same clean-up: forget everything scanned from it

    def databases(self) -> list[str]:
        """Scanned databases as "<source>/<db>", for choosing where pasted SQL runs."""
        return [r[0] for r in self.conn.execute(
            "SELECT name FROM assets WHERE kind = 'database' AND status = 'active' ORDER BY name")]

    # ---- notes ----------------------------------------------------------

    def notes(self, key: str, *, include_columns: bool = True) -> list[sqlite3.Row]:
        if include_columns:
            return self.conn.execute(
                "SELECT * FROM notes WHERE asset_key = ? OR asset_key LIKE ? || '#%' ORDER BY created_at DESC",
                (key, key),
            ).fetchall()
        return self.conn.execute("SELECT * FROM notes WHERE asset_key = ? ORDER BY created_at DESC", (key,)).fetchall()

    def recent_notes(self, limit: int = 30) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT n.*, a.name AS asset_name, a.kind AS asset_kind FROM notes n
               LEFT JOIN assets a ON a.key = CASE WHEN instr(n.asset_key, '#') > 0
                    THEN substr(n.asset_key, 1, instr(n.asset_key, '#') - 1) ELSE n.asset_key END
               ORDER BY n.updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()

    def add_note(self, asset_key: str, body: str, tags: str = "", note_type: str = "note") -> int:
        if note_type not in NOTE_TYPES:
            raise ValueError(f"note_type must be one of {', '.join(NOTE_TYPES)}")
        ts = now()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO notes (asset_key, body, tags, created_at, updated_at, note_type, uid, author) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (asset_key, body.rstrip(), ", ".join(split_tags(tags)), ts, ts, note_type, uuid.uuid4().hex,
                 self.author()),
            )
            from .edits import record
            record(self, asset_key, "note added", None, body.rstrip(), {"type": "note_add", "id": cur.lastrowid})
        self.reindex_search(asset_key)
        return cur.lastrowid

    def update_note(self, note_id: int, body: str, tags: str = "", note_type: str | None = None) -> None:
        before = self.conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        with self.conn:
            if before:
                from .edits import record
                record(self, before["asset_key"], "note changed", before["body"], body.rstrip(),
                       {"type": "note_update", "id": note_id, "body": before["body"], "tags": before["tags"],
                        "note_type": before["note_type"]})
            self.conn.execute(
                "UPDATE notes SET body = ?, tags = ?, note_type = COALESCE(?, note_type), updated_at = ? WHERE id = ?",
                (body.rstrip(), ", ".join(split_tags(tags)), note_type, now(), note_id),
            )
        self._reindex_note(note_id)

    def set_issue_status(self, note_id: int, status: str) -> None:
        if status not in ("open", "resolved"):
            raise ValueError("status must be 'open' or 'resolved'")
        with self.conn:
            self.conn.execute("UPDATE notes SET status = ?, updated_at = ? WHERE id = ?", (status, now(), note_id))
        self.changed()

    def open_issues(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT n.*, a.name AS asset_name, a.kind AS asset_kind FROM notes n
               LEFT JOIN assets a ON a.key = CASE WHEN instr(n.asset_key, '#') > 0
                    THEN substr(n.asset_key, 1, instr(n.asset_key, '#') - 1) ELSE n.asset_key END
               WHERE n.note_type = 'issue' AND n.status = 'open' ORDER BY n.created_at DESC"""
        ).fetchall()

    # ---- profiles -------------------------------------------------------

    def save_profile(self, asset_key: str, columns: list[dict[str, Any]], method: str) -> None:
        """Replace the stored value profile of a table or file (one dict per column)."""
        ts = now()
        hidden = {r[0].lower() for r in self.conn.execute(
            "SELECT column_name FROM column_info WHERE asset_key = ? AND sensitive = 1", (asset_key,))}
        whole = self.conn.execute("SELECT 1 FROM item_info WHERE asset_key = ? AND sensitive = 1",
                                  (asset_key,)).fetchone() is not None
        # Sensitive columns: keep the counts, never the values.
        columns = [c if not (whole or c["name"].lower() in hidden) else
                   {**c, "min": None, "max": None, "mean": None, "top": [], "histogram": None, "percentiles": None}
                   for c in columns]
        with self.conn:
            self.conn.execute("DELETE FROM profiles WHERE asset_key = ?", (asset_key,))
            self.conn.executemany(
                """INSERT INTO profiles (asset_key, column_name, ordinal, data_type, row_count, null_count,
                       distinct_count, min_value, max_value, mean_value, top_values, method, profiled_at,
                       histogram, percentiles, hints)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(asset_key, c["name"], c.get("ordinal", i + 1), c.get("data_type"), c.get("rows"), c.get("nulls"),
                  c.get("distinct"), _text(c.get("min")), _text(c.get("max")), c.get("mean"),
                  json.dumps(c.get("top") or [], default=str), method, ts,
                  json.dumps(c["histogram"], default=str) if c.get("histogram") else None,
                  json.dumps(c["percentiles"], default=str) if c.get("percentiles") else None,
                  json.dumps(c["hints"], default=str) if c.get("hints") else None)
                 for i, c in enumerate(columns)],
            )
        self.changed()

    def profile(self, key: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM profiles WHERE asset_key = ? ORDER BY ordinal, column_name", (key,)).fetchall()

    def profiled_keys(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT DISTINCT asset_key FROM profiles")}

    def upstream(self, key: str, depth: int = 3) -> list[str]:
        """Tables and files this item comes from: whatever wrote it, and what those writers read, a few steps back."""
        found: list[str] = []
        frontier = [key]
        for _ in range(depth):
            writers = [r[0] for f in frontier for r in self.conn.execute(
                "SELECT src_key FROM links WHERE dst_key = ? AND relation = 'writes'", (f,))]
            sources = [r[0] for w in writers for r in self.conn.execute(
                "SELECT dst_key FROM links WHERE src_key = ? AND relation = 'reads' AND dst_key IS NOT NULL", (w,))]
            frontier = [s for s in dict.fromkeys(sources) if s not in found and s != key]
            found += frontier
            if not frontier:
                break
        return found

    def confirm_note(self, note_id: int) -> None:
        """You checked the note and it still holds."""
        with self.conn:
            self.conn.execute("UPDATE notes SET confirmed_at = ? WHERE id = ?", (now(), note_id))
        self.changed()

    def archive_note(self, note_id: int, archived: bool = True) -> None:
        """Move a note out of the way without deleting it."""
        with self.conn:
            self.conn.execute("UPDATE notes SET archived = ? WHERE id = ?", (int(archived), note_id))
        self._reindex_note(note_id)

    def delete_note(self, note_id: int) -> None:
        row = self.conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        with self.conn:
            if row:
                from .edits import record
                record(self, row["asset_key"], "note deleted", row["body"], None,
                       {"type": "note_delete", "row": dict(row)})
            self.conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        if row:
            self.reindex_search(row["asset_key"])

    def _reindex_note(self, note_id: int) -> None:
        row = self.conn.execute("SELECT asset_key FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row:
            self.reindex_search(row[0])


def _matches_hint(candidate: dict[str, str], hint: str) -> bool:
    hint = hint.lower()
    if "/" in hint:
        src, db = hint.split("/", 1)
        return candidate["source"] == src and candidate["database"] == db
    return candidate["database"] == hint


def _text(v: Any) -> str | None:
    """Store min/max values as readable text whatever their type (numbers, dates, numpy scalars)."""
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        text = v.isoformat()
        return text[:10] if text.endswith("T00:00:00") else text
    if hasattr(v, "item"):  # numpy scalar
        v = v.item()
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() and abs(v) < 1e15 else f"{v:.10g}"
    return str(v)


def _norm_path(p: str) -> str:
    """Compare paths the Windows way: forward slashes, case-insensitive."""
    return p.replace("\\", "/").lower()


# Items left out of search and lists: marked not relevant, or an older version of a script.
HIDDEN_KEYS = """SELECT asset_key FROM item_info WHERE relevance = 'not relevant'
    UNION SELECT m.asset_key FROM group_members m JOIN item_groups g ON g.uid = m.group_uid
    WHERE g.type = 'versions' AND g.main_key IS NOT NULL AND g.main_key != m.asset_key"""

# Display names of search hits that aren't scanned items (knowledge, questions, business projects...).
_KNOWLEDGE_NAME = """CASE s.kind
    WHEN 'knowledge' THEN (SELECT title FROM knowledge WHERE 'kb://' || uid = s.key)
    WHEN 'question' THEN (SELECT question FROM questions WHERE 'q://' || uid = s.key)
    WHEN 'metric' THEN (SELECT name FROM metrics WHERE 'metric://' || uid = s.key)
    WHEN 'feature' THEN (SELECT name FROM features WHERE 'feature://' || uid = s.key)
    WHEN 'key' THEN (SELECT name FROM keys WHERE 'key://' || name = s.key)
    WHEN 'bproject' THEN (SELECT name FROM bprojects WHERE 'bp://' || uid = s.key)
    END"""


def _searchable(text: str) -> str:
    """Make snake_case and dotted names searchable by their parts: 'dbo.sales_daily' also matches 'daily'."""
    return f"{text} {re.sub(r'[_.:/#-]+', ' ', text)}"
