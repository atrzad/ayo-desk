"""Versioned SQLite schema. Each entry upgrades `PRAGMA user_version` by one.

Never edit a migration that already shipped: append a new one instead.
"""

MIGRATIONS = (
    # 1 — baseline: the schema created by versions up to 0.1.2 (idempotent for existing databases).
    """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY, day TEXT NOT NULL, time TEXT NOT NULL,
        title TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '');
    CREATE INDEX IF NOT EXISTS event_day ON events(day, time);
    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY, created TEXT NOT NULL,
        category TEXT NOT NULL, title TEXT NOT NULL, detail TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS tracks (
        id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL);
    CREATE TABLE IF NOT EXISTS music_library (
        id INTEGER PRIMARY KEY CHECK(id=1), folder TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS music_folder_tracks (path TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS music_exclusions (path TEXT PRIMARY KEY);
    """,
    # 2 — settings, track metadata and listening statistics.
    """
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE music_meta (
        path TEXT PRIMARY KEY,
        mtime_ns INTEGER NOT NULL DEFAULT 0,
        size INTEGER NOT NULL DEFAULT 0,
        title TEXT NOT NULL DEFAULT '',
        artist TEXT NOT NULL DEFAULT '',
        album TEXT NOT NULL DEFAULT '',
        album_artist TEXT NOT NULL DEFAULT '',
        genre TEXT NOT NULL DEFAULT '',
        year INTEGER,
        track_no INTEGER,
        track_total INTEGER,
        disc_no INTEGER,
        disc_total INTEGER,
        duration REAL NOT NULL DEFAULT 0,
        bitrate INTEGER NOT NULL DEFAULT 0,
        sample_rate INTEGER NOT NULL DEFAULT 0,
        channels INTEGER NOT NULL DEFAULT 0,
        bits_per_sample INTEGER,
        codec TEXT NOT NULL DEFAULT '',
        cover TEXT NOT NULL DEFAULT '',
        rg_track_gain REAL, rg_track_peak REAL, rg_album_gain REAL, rg_album_peak REAL,
        has_lyrics INTEGER NOT NULL DEFAULT 0,
        inferred TEXT NOT NULL DEFAULT '',
        search TEXT NOT NULL DEFAULT '',
        added_at TEXT NOT NULL);
    CREATE INDEX music_meta_album ON music_meta(album_artist, album, disc_no, track_no);
    CREATE INDEX music_meta_artist ON music_meta(artist);
    CREATE TABLE music_stats (
        path TEXT PRIMARY KEY,
        plays INTEGER NOT NULL DEFAULT 0,
        skips INTEGER NOT NULL DEFAULT 0,
        last_played TEXT,
        rating INTEGER NOT NULL DEFAULT 0 CHECK(rating BETWEEN 0 AND 5),
        favorite INTEGER NOT NULL DEFAULT 0,
        resume_at REAL NOT NULL DEFAULT 0);
    CREATE TABLE music_plays (
        id INTEGER PRIMARY KEY, path TEXT NOT NULL, played_at TEXT NOT NULL, seconds REAL NOT NULL);
    CREATE INDEX music_plays_time ON music_plays(played_at);
    """,
)


def migrate(db):
    """Apply pending migrations, each atomically; a failure leaves the previous version intact."""
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version > len(MIGRATIONS):
        raise RuntimeError("Este banco de dados foi criado por uma versão mais nova do Ayo Desk.")
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        try:
            db.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;")
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
    return len(MIGRATIONS)
