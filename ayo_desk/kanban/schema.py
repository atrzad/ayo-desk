"""Versioned schema of kanban.sqlite3. Each entry upgrades `PRAGMA user_version` by one.

Ayo Kanban keeps its own file so it evolves independently of desk.sqlite3.
Never edit a migration that already shipped: append a new one instead.
"""
import os
from pathlib import Path
import sqlite3

MIGRATIONS = (
    # 1 — boards, ordered columns, cards, their checklists and the app's preferences.
    """
    CREATE TABLE kanban_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE kanban_boards (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, position INTEGER NOT NULL, created TEXT NOT NULL);
    CREATE TABLE kanban_columns (
        id INTEGER PRIMARY KEY, board INTEGER NOT NULL REFERENCES kanban_boards(id) ON DELETE CASCADE,
        name TEXT NOT NULL, position INTEGER NOT NULL,
        wip_limit INTEGER NOT NULL DEFAULT 0 CHECK(wip_limit >= 0),
        done INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX kanban_columns_board ON kanban_columns(board, position);
    CREATE TABLE kanban_cards (
        id INTEGER PRIMARY KEY, column_id INTEGER NOT NULL REFERENCES kanban_columns(id) ON DELETE CASCADE,
        position INTEGER NOT NULL, title TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
        priority INTEGER NOT NULL DEFAULT 0 CHECK(priority BETWEEN 0 AND 3),
        due TEXT, tags TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0,
        created TEXT NOT NULL, updated TEXT NOT NULL, done_at TEXT);
    CREATE INDEX kanban_cards_column ON kanban_cards(column_id, archived, position);
    CREATE TABLE kanban_checklist (
        id INTEGER PRIMARY KEY, card INTEGER NOT NULL REFERENCES kanban_cards(id) ON DELETE CASCADE,
        position INTEGER NOT NULL, text TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX kanban_checklist_card ON kanban_checklist(card, position);
    """,
)


def default_path():
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "ayo-desk"
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return base / "kanban.sqlite3"


def connect(path=None):
    path = default_path() if path is None else path
    db = sqlite3.connect(path)
    if str(path) != ":memory:":
        os.chmod(path, 0o600)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys = ON")
        migrate(db)
    except Exception:
        db.close()
        raise
    return db


def migrate(db):
    """Apply pending migrations, each atomically; a failure leaves the previous version intact."""
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version > len(MIGRATIONS):
        raise RuntimeError("Estes quadros foram criados por uma versão mais nova do Ayo Kanban.")
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        try:
            db.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;")
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
    return len(MIGRATIONS)
