from concurrent.futures import CancelledError
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from ayo_desk.core import Store
from ayo_desk.music.library import scan_music_folder


class MusicLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.folder = self.root / "Minha música"
        self.folder.mkdir()
        self.database = self.root / "desk.sqlite3"
        self.store = Store(self.database)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def file(self, relative):
        path = self.folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"sample")
        return str(path)

    def test_recursive_scan_filters_files_and_avoids_symlink_loops(self):
        first = self.file("Banda/Álbum/01 - canção.MP3")
        second = self.file("Outra faixa.flac")
        self.file("Banda/capa.jpg")
        self.file("anotações.txt")
        self.file(".arquivo-oculto.mp3")
        self.file(".cache/preview.wav")
        (self.folder / "Banda/voltar").symlink_to(self.folder, target_is_directory=True)
        (self.folder / "atalho.mp3").symlink_to(first)
        (self.folder / "ausente.mp3").symlink_to(self.folder / "não existe")
        root, tracks = scan_music_folder(self.folder)
        self.assertEqual(root, str(self.folder))
        self.assertEqual(set(tracks), {first, second})
        self.assertEqual(len(tracks), 2)

    def test_folder_and_tracks_survive_restart_and_rescan_updates_inventory(self):
        old = self.file("antiga.mp3")
        self.store.sync_music_folder(*scan_music_folder(self.folder))
        self.store.close()
        self.store = Store(self.database)
        self.assertEqual(self.store.music_folder(), str(self.folder))
        self.assertEqual(self.store.tracks(), [old])
        Path(old).unlink()
        new = self.file("nova.ogg")
        self.store.sync_music_folder(*scan_music_folder(self.folder))
        self.assertEqual(self.store.tracks(), [new])

    def test_changing_folder_preserves_manual_files_and_does_not_duplicate(self):
        manual = self.file("manual.wav")
        previous = self.file("anterior.mp3")
        self.store.add_tracks([manual])
        self.store.sync_music_folder(*scan_music_folder(self.folder))
        self.assertEqual(len(self.store.tracks()), 2)
        replacement = self.root / "Outra pasta"
        replacement.mkdir()
        current = replacement / "atual.flac"
        current.write_bytes(b"sample")
        self.store.sync_music_folder(*scan_music_folder(replacement))
        self.assertEqual(self.store.tracks(), [manual, str(current)])
        self.assertTrue(Path(previous).is_file())
        self.assertEqual(self.store.music_folder(), str(replacement))

    def test_removed_folder_tracks_stay_hidden_until_explicitly_added(self):
        path = self.file("não repetir.mp3")
        self.store.sync_music_folder(*scan_music_folder(self.folder))
        self.store.remove_track(path)
        self.store.close()
        self.store = Store(self.database)
        self.store.sync_music_folder(*scan_music_folder(self.folder))
        self.assertEqual(self.store.tracks(), [])
        self.assertTrue(Path(path).is_file())
        self.store.add_tracks([path])
        self.assertEqual(self.store.tracks(), [path])

    def test_unavailable_folder_does_not_replace_cached_library(self):
        path = self.file("guardada.mp3")
        self.store.sync_music_folder(*scan_music_folder(self.folder))
        with self.assertRaises(ValueError):
            result = scan_music_folder(self.root / "desconectada")
            self.store.sync_music_folder(*result)
        self.assertEqual(self.store.music_folder(), str(self.folder))
        self.assertEqual(self.store.tracks(), [path])

    def test_empty_folder_and_cancelled_scan(self):
        self.assertEqual(scan_music_folder(self.folder), (str(self.folder), []))
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(CancelledError):
            scan_music_folder(self.folder, cancel)

    def test_existing_playlist_migrates_without_losing_tracks(self):
        legacy = self.root / "old.sqlite3"
        path = self.file("existente.mp3")
        with sqlite3.connect(legacy) as db:
            db.execute("CREATE TABLE tracks(id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL)")
            db.execute("INSERT INTO tracks(path) VALUES(?)", (path,))
        upgraded = Store(legacy)
        try:
            self.assertEqual(upgraded.tracks(), [path])
            self.assertIsNone(upgraded.music_folder())
            upgraded.sync_music_folder(*scan_music_folder(self.folder))
            self.assertEqual(upgraded.tracks(), [path])
        finally:
            upgraded.close()


if __name__ == "__main__":
    unittest.main()
