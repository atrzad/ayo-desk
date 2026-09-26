from pathlib import Path
import tempfile
import unittest

from ayo_desk.core import Store
from ayo_desk.music import m3u
from ayo_desk.music.db import MusicDB


class PlaylistDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "desk.sqlite3"
        self.store = Store(self.path)
        self.music = MusicDB(self.store)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_create_rename_delete_and_unique_names(self):
        first = self.music.create_playlist("  Para   correr ", ["/m/a.mp3"])
        second = self.music.create_playlist("para correr")
        self.assertEqual([p["name"] for p in self.music.playlists()], ["Para correr", "para correr (2)"])
        self.assertEqual(self.music.rename_playlist(second, "Estudar"), "Estudar")
        with self.assertRaises(ValueError):
            self.music.create_playlist("   ")
        self.music.delete_playlist(first)
        self.assertEqual([p["name"] for p in self.music.playlists()], ["Estudar"])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM playlist_items").fetchone()[0], 0)
        with self.assertRaises(ValueError):
            self.music.rename_playlist(first, "Sumiu")

    def test_add_skips_duplicates_insert_move_and_remove(self):
        playlist = self.music.create_playlist("Mix", ["/a", "/b", "/c"])
        self.assertEqual(self.music.add_to_playlist(playlist, ["/b", "/d", "/d"]), 1)
        self.assertEqual(self.music.playlist_paths(playlist), ["/a", "/b", "/c", "/d"])
        self.music.add_to_playlist(playlist, ["/x"], at=1)
        self.assertEqual(self.music.playlist_paths(playlist), ["/a", "/x", "/b", "/c", "/d"])
        self.music.move_in_playlist(playlist, [0], 3)
        self.assertEqual(self.music.playlist_paths(playlist), ["/x", "/b", "/a", "/c", "/d"])
        self.music.move_in_playlist(playlist, [3, 4], 0)
        self.assertEqual(self.music.playlist_paths(playlist), ["/c", "/d", "/x", "/b", "/a"])
        self.music.remove_from_playlist(playlist, [0, 4])
        self.assertEqual(self.music.playlist_paths(playlist), ["/d", "/x", "/b"])
        self.assertEqual(self.music.playlists()[0]["count"], 3)

    def test_playlists_survive_restart_and_forgotten_files(self):
        playlist = self.music.create_playlist("Guardada", ["/a", "/b", "/a"])
        self.store.close()
        self.store = Store(self.path)
        self.music = MusicDB(self.store)
        self.assertEqual(self.music.playlist_paths(playlist), ["/a", "/b", "/a"])
        self.music.forget_in_playlists("/a")
        self.assertEqual(self.music.playlist_paths(playlist), ["/b"])


class M3UTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_round_trip_with_relative_paths_and_accents(self):
        songs = self.root / "Música" / "Álbum"
        songs.mkdir(parents=True)
        tracks = [{"path": str(songs / "01 - Canção.mp3"), "duration": 241.4, "artist": "Terno Rei", "title": "Canção"},
                  {"path": "https://radio.example/stream", "title": "Rádio"}]
        target = self.root / "Música" / "lista.m3u8"
        m3u.write(target, tracks)
        text = target.read_text(encoding="utf-8")
        self.assertIn("#EXTINF:241,Terno Rei - Canção", text)
        self.assertIn("Álbum/01 - Canção.mp3", text)
        self.assertNotIn(str(self.root), text)
        self.assertEqual(m3u.read(target), [t["path"] for t in tracks])

    def test_foreign_playlists_windows_latin1_uris_and_pls(self):
        folder = self.root / "Musicas"
        folder.mkdir()
        (folder / "faixa.mp3").write_bytes(b"x")
        latin = self.root / "velha.m3u"
        latin.write_bytes("#EXTM3U\r\n#EXTINF:10,Coração\r\nMusicas\\faixa.mp3\r\n\r\n/abs/ç.flac\r\n".encode("cp1252"))
        self.assertEqual(m3u.read(latin), [str(folder / "faixa.mp3"), "/abs/ç.flac"])
        uri = self.root / "uri.m3u8"
        uri.write_text("file:///home/ayo/M%C3%BAsica/a%20b.mp3\n", encoding="utf-8")
        self.assertEqual(m3u.read(uri), ["/home/ayo/Música/a b.mp3"])
        pls = self.root / "radio.pls"
        pls.write_text("[playlist]\nNumberOfEntries=2\nFile2=Musicas/faixa.mp3\nFile1=http://r.example/a\nTitle1=R\n")
        self.assertEqual(m3u.read(pls), ["http://r.example/a", str(folder / "faixa.mp3")])


if __name__ == "__main__":
    unittest.main()
