"""Persistent local data and the calculator's C ABI; no graphical dependencies."""
import ctypes
import datetime as dt
import os
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parent.parent


class Calculator:
    def __init__(self, path=None):
        self.lib = ctypes.CDLL(str(path or ROOT / "build/libayo.so"))
        self.lib.ayo_calculate.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_double),
                                         ctypes.c_char_p, ctypes.c_size_t]
        self.lib.ayo_calculate.restype = ctypes.c_int

    def evaluate(self, text):
        if "\0" in text:
            raise ValueError("Caractere inválido")
        text = text.replace("×", "*").replace("÷", "/").replace("−", "-").replace("π", "pi").replace(",", ".")
        output, error = ctypes.c_double(), ctypes.create_string_buffer(256)
        code = self.lib.ayo_calculate(text.encode("utf-8"), ctypes.byref(output), error, len(error))
        if code:
            raise ValueError(error.value.decode("utf-8"))
        return output.value


class Store:
    def __init__(self, path=None):
        if path is None:
            base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "ayo-desk"
            base.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = base / "desk.sqlite3"
        self.db = sqlite3.connect(path)
        if str(path) != ":memory:":
            os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
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
        """)

    def save_event(self, day, time, title, notes="", event_id=None):
        day = dt.date.fromisoformat(day).isoformat()
        if time:
            time = dt.datetime.strptime(time, "%H:%M").strftime("%H:%M")
        title = title.strip()
        if not title:
            raise ValueError("Informe o título do compromisso.")
        with self.db:
            if event_id is None:
                return self.db.execute("INSERT INTO events(day,time,title,notes) VALUES(?,?,?,?)",
                                       (day, time, title, notes)).lastrowid
            self.db.execute("UPDATE events SET day=?,time=?,title=?,notes=? WHERE id=?",
                            (day, time, title, notes, event_id))
        return event_id

    def events(self, day):
        return self.db.execute("SELECT * FROM events WHERE day=? ORDER BY time,id", (day,)).fetchall()

    def event_days(self, year, month):
        prefix = f"{year:04d}-{month:02d}-%"
        return [int(r[0][-2:]) for r in self.db.execute("SELECT DISTINCT day FROM events WHERE day LIKE ?", (prefix,))]

    def delete_event(self, event_id):
        with self.db:
            self.db.execute("DELETE FROM events WHERE id=?", (event_id,))

    def log(self, category, title, detail=""):
        with self.db:
            self.db.execute("INSERT INTO history(created,category,title,detail) VALUES(?,?,?,?)",
                            (dt.datetime.now().astimezone().isoformat(timespec="seconds"), category, title, detail))
            self.db.execute("DELETE FROM history WHERE category=? AND id NOT IN "
                            "(SELECT id FROM history WHERE category=? ORDER BY id DESC LIMIT 200)", (category, category))

    def history(self, category):
        return self.db.execute("SELECT * FROM history WHERE category=? ORDER BY id DESC LIMIT 100", (category,)).fetchall()

    def clear_history(self, category):
        with self.db:
            self.db.execute("DELETE FROM history WHERE category=?", (category,))

    def add_tracks(self, paths):
        paths = [(str(Path(p).resolve()),) for p in paths]
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO tracks(path) VALUES(?)", paths)
            self.db.executemany("DELETE FROM music_exclusions WHERE path=?", paths)

    def tracks(self):
        manual = [r[0] for r in self.db.execute("SELECT path FROM tracks ORDER BY id")]
        folder = [r[0] for r in self.db.execute(
            "SELECT path FROM music_folder_tracks WHERE path NOT IN "
            "(SELECT path FROM music_exclusions) ORDER BY path COLLATE NOCASE")]
        return list(dict.fromkeys([*manual, *folder]))

    def music_folder(self):
        record = self.db.execute("SELECT folder FROM music_library WHERE id=1").fetchone()
        return record[0] if record else None

    def sync_music_folder(self, folder, paths):
        """Commit only complete scans; retain manually added files across folder changes."""
        folder = str(Path(folder).resolve())
        with self.db:
            if self.music_folder() != folder:
                self.db.execute("DELETE FROM music_exclusions")
            self.db.execute("INSERT INTO music_library(id,folder) VALUES(1,?) "
                            "ON CONFLICT(id) DO UPDATE SET folder=excluded.folder", (folder,))
            self.db.execute("DELETE FROM music_folder_tracks")
            self.db.executemany("INSERT OR IGNORE INTO music_folder_tracks(path) VALUES(?)",
                                ((str(p),) for p in paths))

    def remove_track(self, path):
        with self.db:
            self.db.execute("DELETE FROM tracks WHERE path=?", (path,))
            self.db.execute("INSERT OR IGNORE INTO music_exclusions(path) "
                            "SELECT path FROM music_folder_tracks WHERE path=?", (path,))

    def close(self):
        self.db.close()
