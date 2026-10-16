"""SQLite-database: schema, migraties en hulpfuncties.

Schemaversies
  1  (1.0.0)  media (afbeelding/video), playlists, playlist_items, players, settings
  2  (1.1.0)  + HTML-media (kind 'html', html_entry/html_width/html_height,
               playback_dir 'html'/'builtin')
             + playlists.transition_enabled / transition_media_id / new_item_transition
             + playlist_items.scale_mode / transition_mode / transition_media_id
             + instelling scale_mode (standaard 'cover')

Een migratie wordt in één transactie uitgevoerd; vooraf maakt de server een back-up
(zie app.create_app). Bestaande gegevens blijven behouden; nieuwe kolommen krijgen
standaardwaarden waarmee oude playlists ongewijzigd blijven werken.
"""

import json
import sqlite3
import time

SCHEMA_VERSION = 2

MEDIA_TABLE_V2 = """
CREATE TABLE {name} (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    kind              TEXT NOT NULL CHECK (kind IN ('image', 'video', 'html')),
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
    playback_dir      TEXT NOT NULL CHECK (playback_dir IN ('files', 'renditions', 'html', 'builtin')),
    playback_sha256   TEXT NOT NULL,
    playback_size     INTEGER NOT NULL,
    thumb_name        TEXT,
    created_at        REAL NOT NULL,
    html_entry        TEXT,
    html_width        INTEGER,
    html_height       INTEGER
)
"""

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

""" + MEDIA_TABLE_V2.format(name="IF NOT EXISTS media") + """;
CREATE INDEX IF NOT EXISTS media_sha ON media (sha256);

CREATE TABLE IF NOT EXISTS playlists (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL UNIQUE,
    repeat_mode         TEXT NOT NULL DEFAULT 'loop'
                        CHECK (repeat_mode IN ('loop', 'once_hold_last', 'once_then_default')),
    mute                INTEGER NOT NULL DEFAULT 0,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    transition_enabled  INTEGER NOT NULL DEFAULT 0,
    transition_media_id TEXT REFERENCES media (id) ON DELETE SET NULL,
    new_item_transition TEXT NOT NULL DEFAULT 'default' CHECK (new_item_transition IN ('default', 'none'))
);

CREATE TABLE IF NOT EXISTS playlist_items (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id         INTEGER NOT NULL REFERENCES playlists (id) ON DELETE CASCADE,
    media_id            TEXT NOT NULL REFERENCES media (id) ON DELETE RESTRICT,
    position            INTEGER NOT NULL,
    duration            REAL,
    scale_mode          TEXT CHECK (scale_mode IS NULL OR scale_mode IN ('cover', 'contain', 'fill')),
    transition_mode     TEXT NOT NULL DEFAULT 'default' CHECK (transition_mode IN ('default', 'none', 'custom')),
    transition_media_id TEXT REFERENCES media (id) ON DELETE SET NULL
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
    "scale_mode": "cover",
}

SCALE_MODES = ("cover", "contain", "fill")


def connect(path):
    conn = sqlite3.connect(path, timeout=15, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def schema_version(path):
    """Versie van een bestaande database, of None als die nog niet bestaat."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)
    except sqlite3.OperationalError:
        return None
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        return int(row[0]) if row else None
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()


def _migrate_1_to_2(conn):
    cols_v1 = ("id, name, original_filename, kind, format, ext, size, sha256, width, height, duration, "
               "codec, status, warning, file_name, playback_name, playback_dir, playback_sha256, "
               "playback_size, thumb_name, created_at")
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(MEDIA_TABLE_V2.format(name="media_v2"))
        conn.execute(f"INSERT INTO media_v2 ({cols_v1}) SELECT {cols_v1} FROM media")
        conn.execute("DROP TABLE media")
        conn.execute("ALTER TABLE media_v2 RENAME TO media")
        conn.execute("CREATE INDEX IF NOT EXISTS media_sha ON media (sha256)")
        conn.execute("ALTER TABLE playlists ADD COLUMN transition_enabled INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE playlists ADD COLUMN transition_media_id TEXT "
                     "REFERENCES media (id) ON DELETE SET NULL")
        conn.execute("ALTER TABLE playlists ADD COLUMN new_item_transition TEXT NOT NULL DEFAULT 'default' "
                     "CHECK (new_item_transition IN ('default', 'none'))")
        conn.execute("ALTER TABLE playlist_items ADD COLUMN scale_mode TEXT "
                     "CHECK (scale_mode IS NULL OR scale_mode IN ('cover', 'contain', 'fill'))")
        conn.execute("ALTER TABLE playlist_items ADD COLUMN transition_mode TEXT NOT NULL DEFAULT 'default' "
                     "CHECK (transition_mode IN ('default', 'none', 'custom'))")
        conn.execute("ALTER TABLE playlist_items ADD COLUMN transition_media_id TEXT "
                     "REFERENCES media (id) ON DELETE SET NULL")
        problems = conn.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise sqlite3.IntegrityError(f"Verwijzingsfouten na migratie: {len(problems)}")
        conn.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
        # Afbeeldingen krijgen nieuwe, beeldvullende afspeelversies (zie app.regenerate_renditions).
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('renditions_version', '1')")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def init_db(path):
    conn = connect(path)
    try:
        # WAL beperkt schrijfacties en is robuust bij stroomuitval (geen corruptie).
        conn.execute("PRAGMA journal_mode = WAL")
        exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
        version = None
        if exists:
            row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            version = int(row["value"]) if row else None
        if version is None:
            conn.executescript(SCHEMA)
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                         (str(SCHEMA_VERSION),))
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('renditions_version', '2')")
        else:
            if version < 2:
                _migrate_1_to_2(conn)
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"Database heeft schemaversie {version}; deze software kent maximaal "
                                   f"{SCHEMA_VERSION}. Gebruik 'signage-admin rollback' niet over een migratie "
                                   "heen, of zet een oudere back-up terug.")
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, json.dumps(value)))
    finally:
        conn.close()


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(value)))


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
