"""SQLite-database: schema, migraties en hulpfuncties."""

import json
import sqlite3
import time

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    kind              TEXT NOT NULL CHECK (kind IN ('image', 'video')),
    format            TEXT NOT NULL,
    ext               TEXT NOT NULL,
    size              INTEGER NOT NULL,
    sha256            TEXT NOT NULL,
    width             INTEGER,
    height            INTEGER,
    duration          REAL,
    codec             TEXT,
    status            TEXT NOT NULL CHECK (status IN ('ok', 'warning')),
    warning           TEXT,
    file_name         TEXT NOT NULL,
    playback_name     TEXT NOT NULL,
    playback_dir      TEXT NOT NULL CHECK (playback_dir IN ('files', 'renditions')),
    playback_sha256   TEXT NOT NULL,
    playback_size     INTEGER NOT NULL,
    thumb_name        TEXT,
    created_at        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS media_sha ON media (sha256);

CREATE TABLE IF NOT EXISTS playlists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    repeat_mode TEXT NOT NULL DEFAULT 'loop'
                CHECK (repeat_mode IN ('loop', 'once_hold_last', 'once_then_default')),
    mute        INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id INTEGER NOT NULL REFERENCES playlists (id) ON DELETE CASCADE,
    media_id    TEXT NOT NULL REFERENCES media (id) ON DELETE RESTRICT,
    position    INTEGER NOT NULL,
    duration    REAL
);
CREATE INDEX IF NOT EXISTS playlist_items_pl ON playlist_items (playlist_id, position);
CREATE INDEX IF NOT EXISTS playlist_items_media ON playlist_items (media_id);

CREATE TABLE IF NOT EXISTS players (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('local', 'remote')),
    token_hash   TEXT NOT NULL,
    playlist_id  INTEGER REFERENCES playlists (id) ON DELETE SET NULL,
    display_name TEXT NOT NULL DEFAULT 'HDMI-scherm',
    rotation     INTEGER NOT NULL DEFAULT 0 CHECK (rotation IN (0, 90, 180, 270)),
    created_at   REAL NOT NULL,
    last_seen    REAL
);
"""

DEFAULT_SETTINGS = {
    "system_name": "Digital Signage",
    "default_image_duration": 10,
    "default_playlist_id": None,
    "volume": 80,
}


def connect(path):
    conn = sqlite3.connect(path, timeout=15, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def init_db(path):
    conn = connect(path)
    try:
        # WAL beperkt schrijfacties en is robuust bij stroomuitval (geen corruptie).
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        if row is None:
            conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        # Toekomstige migraties: if int(row['value']) < 2: ...
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, json.dumps(value)))
    finally:
        conn.close()


class Tx:
    """Context manager voor een expliciete transactie (BEGIN IMMEDIATE)."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.execute("COMMIT")
        else:
            self.conn.execute("ROLLBACK")
        return False


def get_settings(conn):
    out = dict(DEFAULT_SETTINGS)
    for row in conn.execute("SELECT key, value FROM settings"):
        try:
            out[row["key"]] = json.loads(row["value"])
        except ValueError:
            pass
    return out


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


def now():
    return time.time()
