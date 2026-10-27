-- A catalogue made by v1 of pdcat (made-up data), used to test the upgrade to v2.
BEGIN TRANSACTION;
CREATE TABLE assets (
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
INSERT INTO "assets" VALUES('sql://warehouse/salesdw/dbo.sales','table','dbo.Sales','warehouse',NULL,NULL,'{"database": "SalesDW", "schema": "dbo", "object": "Sales", "row_count": 1000}','active','2026-10-27T03:34:56+00:00','2026-10-27T03:34:56+00:00');
CREATE TABLE columns (
    asset_key   TEXT NOT NULL,
    name        TEXT NOT NULL,
    ordinal     INTEGER,
    data_type   TEXT,
    nullable    INTEGER,
    description TEXT,
    PRIMARY KEY (asset_key, name)
);
INSERT INTO "columns" VALUES('sql://warehouse/salesdw/dbo.sales','PlantID',1,'int',NULL,NULL);
INSERT INTO "columns" VALUES('sql://warehouse/salesdw/dbo.sales','Units',2,'decimal(18,2)',NULL,NULL);
CREATE TABLE folders (
    name      TEXT PRIMARY KEY,
    path      TEXT NOT NULL,
    recursive INTEGER NOT NULL DEFAULT 1,
    added_at  TEXT NOT NULL
);
CREATE TABLE libraries (
    name     TEXT PRIMARY KEY,
    path     TEXT NOT NULL,
    added_at TEXT NOT NULL
);
CREATE TABLE links (
    src_key  TEXT NOT NULL,
    relation TEXT NOT NULL,
    dst_ref  TEXT NOT NULL,
    dst_key  TEXT,
    source   TEXT NOT NULL,
    hint     TEXT,
    evidence TEXT,
    PRIMARY KEY (src_key, relation, dst_ref)
);
CREATE TABLE notes (
    id         INTEGER PRIMARY KEY,
    asset_key  TEXT NOT NULL,
    body       TEXT NOT NULL,
    tags       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    note_type  TEXT NOT NULL DEFAULT 'note',  -- note | issue | example
    status     TEXT NOT NULL DEFAULT 'open'   -- issues: open | resolved
);
INSERT INTO "notes" VALUES(1,'sql://warehouse/salesdw/dbo.sales','Loaded nightly at 2am.','refresh','2026-10-27T03:34:56+00:00','2026-10-27T03:34:56+00:00','note','open');
INSERT INTO "notes" VALUES(2,'sql://warehouse/salesdw/dbo.sales#Units','Units are net of returns.','','2026-10-27T03:34:56+00:00','2026-10-27T03:34:56+00:00','note','open');
INSERT INTO "notes" VALUES(3,'sql://warehouse/salesdw/dbo.sales','Plant 9999 is a test plant.','','2026-10-27T03:34:56+00:00','2026-10-27T03:34:56+00:00','issue','open');
CREATE TABLE profiles (
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
INSERT INTO "profiles" VALUES('sql://warehouse/salesdw/dbo.sales','Units',1,NULL,1000,3,40,'0','99',12.5,'[["1", 30]]','whole table','2026-10-27T03:34:56+00:00');
CREATE TABLE review_tasks (
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
CREATE TABLE scans (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    summary     TEXT
);
INSERT INTO "scans" VALUES(1,'warehouse','2026-10-27T03:34:56+00:00','2026-10-27T03:34:56+00:00','{"table": 1}');
CREATE INDEX ix_assets_source ON assets(source);
CREATE INDEX ix_links_dst ON links(dst_key);
CREATE INDEX ix_notes_asset ON notes(asset_key);
CREATE INDEX ix_review_asset ON review_tasks(asset_key);
COMMIT;
