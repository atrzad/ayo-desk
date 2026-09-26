from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ayo_desk import migrations
from ayo_desk.core import Store


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "desk.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def version(self):
        with sqlite3.connect(self.path) as db:
            return db.execute("PRAGMA user_version").fetchone()[0]

    def test_new_database_reaches_latest_version(self):
        Store(self.path).close()
        self.assertEqual(self.version(), len(migrations.MIGRATIONS))
        Store(self.path).close()
        self.assertEqual(self.version(), len(migrations.MIGRATIONS))

    def test_database_from_0_1_2_keeps_its_data(self):
        with sqlite3.connect(self.path) as db:
            db.executescript(migrations.MIGRATIONS[0])
            db.execute("INSERT INTO events(day,time,title) VALUES('2026-09-20','10:00','Consulta')")
            db.execute("INSERT INTO music_library(id,folder) VALUES(1,'/musicas')")
            db.execute("INSERT INTO music_folder_tracks(path) VALUES('/musicas/a.mp3')")
        store = Store(self.path)
        try:
            self.assertEqual(store.events("2026-09-20")[0]["title"], "Consulta")
            self.assertEqual(store.music_folder(), "/musicas")
            self.assertEqual(store.tracks(), ["/musicas/a.mp3"])
            store.db.execute("SELECT path, plays FROM music_stats").fetchall()
        finally:
            store.close()
        self.assertEqual(self.version(), len(migrations.MIGRATIONS))

    def test_failed_migration_rolls_back_completely(self):
        Store(self.path).close()
        broken = (*migrations.MIGRATIONS, "CREATE TABLE half_done(x); SELECT * FROM missing_table;")
        with patch.object(migrations, "MIGRATIONS", broken):
            with self.assertRaises(sqlite3.OperationalError):
                Store(self.path)
        self.assertEqual(self.version(), len(migrations.MIGRATIONS))
        with sqlite3.connect(self.path) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("half_done", tables)

    def test_newer_database_is_not_downgraded(self):
        with sqlite3.connect(self.path) as db:
            db.execute(f"PRAGMA user_version = {len(migrations.MIGRATIONS) + 1}")
        with self.assertRaises(RuntimeError):
            Store(self.path)

    def test_settings_round_trip_json_values(self):
        store = Store(self.path)
        try:
            self.assertEqual(store.setting("music.volume", 70), 70)
            store.set_setting("music.volume", 45)
            store.set_setting("music.eq", {"preset": "Rock", "bands": [3, 2, 0]})
        finally:
            store.close()
        store = Store(self.path)
        try:
            self.assertEqual(store.setting("music.volume"), 45)
            self.assertEqual(store.setting("music.eq")["bands"], [3, 2, 0])
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
