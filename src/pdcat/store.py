"""SQLite storage for the catalogue: scanned assets, the links between them, and your notes.

Scanned data and notes live in separate tables. Notes are keyed by asset key,
so re-scanning never touches them, and a note survives even if its table
disappears for a while.
"""
from __future__ import annotations

import json
import re
import fnmatch
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
"""

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
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.fts = self._create_search_index()

    def close(self) -> None:
        self.conn.close()

    def _migrate(self) -> None:
        """Bring catalogues made by older versions up to date."""
        note_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(notes)")}
        with self.conn:
            if "note_type" not in note_cols:
                self.conn.execute("ALTER TABLE notes ADD COLUMN note_type TEXT NOT NULL DEFAULT 'note'")
            if "status" not in note_cols:
                self.conn.execute("ALTER TABLE notes ADD COLUMN status TEXT NOT NULL DEFAULT 'open'")

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
    def scan(self, source: str) -> Iterator["Catalogue"]:
        """Wrap one source's scan in a transaction.

        Everything the source found last time is marked 'missing' up front and
        flipped back to 'active' as the scan sees it again, so dropped tables or
        deleted scripts show up as missing instead of silently vanishing. If the
        scan fails, nothing changes.
        """
        started = now()
        with self.conn:
            self.conn.execute("UPDATE assets SET status = 'missing' WHERE source = ?", (source,))
            self.conn.execute("DELETE FROM links WHERE source = ?", (source,))
            self.conn.execute(
                "DELETE FROM columns WHERE asset_key IN (SELECT key FROM assets WHERE source = ?)", (source,)
            )
            yield self
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
                 dst_key: str | None = None, hint: str | None = None, evidence: str | None = None) -> None:
        """Record that src uses dst. dst_ref is "table:<name>" for a table to be matched later,
        "file:<path>" for a file, or an exact asset key."""
        self.conn.execute(
            "INSERT OR IGNORE INTO links (src_key, relation, dst_ref, dst_key, source, hint, evidence) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (src_key, relation, dst_ref, dst_key, source, hint, evidence),
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
            updates.append((cands[0]["key"] if len(cands) == 1 else None, link["rowid"]))
        updates += self._resolve_file_refs()
        with self.conn:
            self.conn.executemany("UPDATE links SET dst_key = ? WHERE rowid = ?", updates)

    def _resolve_file_refs(self) -> list[tuple[str | None, int]]:
        """Match "file:<path>" references from code to catalogued CSV/Parquet files.

        Relative paths match files in the same project first, then in any folder
        whose path ends the same way. f-string parts ("{}") act as wildcards, and
        when several files match (e.g. dated extracts) the newest one wins.
        """
        files = []
        for row in self.conn.execute(
                "SELECT key, source, attrs FROM assets WHERE kind = 'data_file' AND status = 'active'"):
            a = json.loads(row["attrs"])
            files.append({"key": row["key"], "source": row["source"], "path": _norm_path(a.get("path", "")),
                          "mtime": a.get("mtime") or 0})
        if not files:
            return [(None, r["rowid"]) for r in self.conn.execute("SELECT rowid FROM links WHERE dst_ref LIKE 'file:%'")]

        updates = []
        for link in self.conn.execute("SELECT rowid, dst_ref, source FROM links WHERE dst_ref LIKE 'file:%'"):
            ref = _norm_path(link["dst_ref"][len("file:"):])
            while ref.startswith("./"):
                ref = ref[2:]
            pattern = fnmatch.translate(ref.replace("{}", "*")).replace(".*", "[^/]*") if "{}" in ref else None
            absolute = bool(re.match(r"^([a-z]:/|/)", ref))

            def hit(f: dict) -> bool:
                if absolute:
                    target = f["path"]
                else:  # compare the same number of trailing path segments
                    target = "/".join(f["path"].split("/")[-(ref.count("/") + 1):])
                return re.fullmatch(pattern, target) is not None if pattern else target == ref

            matches = [f for f in files if hit(f)]
            same_project = [f for f in matches if f["source"] == link["source"]]
            matches = same_project or matches
            best = max(matches, key=lambda f: f["mtime"])["key"] if matches else None
            updates.append((best, link["rowid"]))
        return updates

    # ---- search ---------------------------------------------------------

    def rebuild_search(self) -> None:
        """Rebuild the full-text index. A personal catalogue is small enough to do this in full."""
        notes_by_key: dict[str, list[str]] = defaultdict(list)
        for n in self.conn.execute("SELECT asset_key, body, tags FROM notes"):
            base = n["asset_key"].split("#", 1)[0]
            notes_by_key[base].append(f"{n['body']} {n['tags']}")
        cols_by_key: dict[str, list[str]] = defaultdict(list)
        for c in self.conn.execute("SELECT asset_key, name, description FROM columns"):
            cols_by_key[c["asset_key"]].append(f"{c['name']} {c['description'] or ''}")

        rows = []
        seen = set()
        for a in self.conn.execute("SELECT key, kind, source, name, description, attrs FROM assets"):
            attrs = json.loads(a["attrs"])
            extra = " ".join(str(attrs.get(k, "")) for k in ("functions", "imports", "classes", "sql", "tags"))
            body = " ".join([a["description"] or "", extra, " ".join(cols_by_key[a["key"]]),
                             " ".join(notes_by_key[a["key"]])])
            rows.append((a["key"], a["kind"], a["source"], _searchable(a["name"]), _searchable(body)))
            seen.add(a["key"])
        for key, texts in notes_by_key.items():
            if key not in seen:  # notes on files or unmatched tables
                rows.append((key, "note", "", _searchable(key), _searchable(" ".join(texts))))
        with self.conn:
            self.conn.execute("DELETE FROM search_idx")
            self.conn.executemany("INSERT INTO search_idx (key, kind, source, name, body) VALUES (?, ?, ?, ?, ?)", rows)

    def search(self, text: str, *, kind: str | None = None, source: str | None = None,
               limit: int = 100) -> list[sqlite3.Row]:
        tokens = re.findall(r"\w+", text or "")
        if not tokens:
            return self.list_assets(kind=kind, source=source, limit=limit)
        filters, params = [], []
        if self.fts:
            filters.append("search_idx MATCH ?")
            params.append(" ".join(f'"{t}"*' for t in tokens))
            order = "bm25(search_idx, 0, 0, 0, 10.0, 1.0)"
        else:
            for t in tokens:
                filters.append("(s.name LIKE ? OR s.body LIKE ?)")
                params += [f"%{t}%", f"%{t}%"]
            order = "s.name"
        if kind:
            filters.append("s.kind = ?")
            params.append(kind)
        if source:
            filters.append("s.source = ?")
            params.append(source)
        sql = f"""
            SELECT s.key, s.kind, s.source, COALESCE(a.name, s.key) AS name, a.description, a.status,
                   (SELECT COUNT(*) FROM notes n WHERE n.asset_key = s.key OR n.asset_key LIKE s.key || '#%') AS note_count
            FROM search_idx s LEFT JOIN assets a ON a.key = s.key
            WHERE {' AND '.join(filters)}
            ORDER BY {order} LIMIT ?
        """
        return self.conn.execute(sql, (*params, limit)).fetchall()

    # ---- reading --------------------------------------------------------

    def list_assets(self, *, kind: str | None = None, source: str | None = None,
                    limit: int = 100) -> list[sqlite3.Row]:
        filters, params = ["1 = 1"], []
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
            "SELECT key, kind, name, description, status FROM assets WHERE parent = ? ORDER BY name", (key,)
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
            "SELECT kind, COUNT(*) FROM assets WHERE status = 'active' GROUP BY kind").fetchall())
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
        with self.conn:
            self.conn.execute(
                "INSERT INTO folders (name, path, recursive, added_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET path = excluded.path, recursive = excluded.recursive",
                (name, path, int(recursive), now()))

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

    def add_library(self, name: str, path: str) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO libraries (name, path, added_at) VALUES (?, ?, ?) "
                              "ON CONFLICT(name) DO UPDATE SET path = excluded.path", (name, path, now()))

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
                "INSERT INTO notes (asset_key, body, tags, created_at, updated_at, note_type) VALUES (?, ?, ?, ?, ?, ?)",
                (asset_key, body.rstrip(), ", ".join(split_tags(tags)), ts, ts, note_type),
            )
        self.rebuild_search()
        return cur.lastrowid

    def update_note(self, note_id: int, body: str, tags: str = "", note_type: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE notes SET body = ?, tags = ?, note_type = COALESCE(?, note_type), updated_at = ? WHERE id = ?",
                (body.rstrip(), ", ".join(split_tags(tags)), note_type, now(), note_id),
            )
        self.rebuild_search()

    def set_issue_status(self, note_id: int, status: str) -> None:
        if status not in ("open", "resolved"):
            raise ValueError("status must be 'open' or 'resolved'")
        with self.conn:
            self.conn.execute("UPDATE notes SET status = ?, updated_at = ? WHERE id = ?", (status, now(), note_id))

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
        with self.conn:
            self.conn.execute("DELETE FROM profiles WHERE asset_key = ?", (asset_key,))
            self.conn.executemany(
                """INSERT INTO profiles (asset_key, column_name, ordinal, data_type, row_count, null_count,
                       distinct_count, min_value, max_value, mean_value, top_values, method, profiled_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(asset_key, c["name"], c.get("ordinal", i + 1), c.get("data_type"), c.get("rows"), c.get("nulls"),
                  c.get("distinct"), _text(c.get("min")), _text(c.get("max")), c.get("mean"),
                  json.dumps(c.get("top") or [], default=str), method, ts)
                 for i, c in enumerate(columns)],
            )

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

    def delete_note(self, note_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self.rebuild_search()


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


def _searchable(text: str) -> str:
    """Make snake_case and dotted names searchable by their parts: 'dbo.sales_daily' also matches 'daily'."""
    return f"{text} {re.sub(r'[_.:/#-]+', ' ', text)}"
