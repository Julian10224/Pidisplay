-- Schema van versie 1.0.0 (voor migratietests)
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
