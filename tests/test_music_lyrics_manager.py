from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib

from ayo_desk.core import Store
from ayo_desk.music.db import MusicDB
from ayo_desk.music.ui import lyrics_manager
from test_music_lyrics import FakeHttp, fixture


class FakeLibrary:
    def __init__(self, tracks):
        self.tracks = tracks

    def get(self, path):
        return self.tracks.get(path)


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "db.sqlite3")
        self.song = self.root / "Obsoleto.mp3"
        self.song.write_bytes(b"x")
        track = SimpleNamespace(title="Obsoleto", artist="Seu Pereira e Coletivo 401", display_artist="",
                                album="", duration=224.0)
        self.page = SimpleNamespace(store=self.store, music=MusicDB(self.store), closed=False,
                                    library=FakeLibrary({str(self.song): track}))
        self.changes = 0
        self.manager = lyrics_manager.LyricsManager(self.page, self._changed)
        no_voice = patch("ayo_desk.music.voice.binary", return_value=None)
        no_voice.start()
        self.addCleanup(no_voice.stop)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def _changed(self):
        self.changes += 1

    def wait(self, condition, seconds=5):
        context, until = GLib.MainContext.default(), time.monotonic() + seconds
        while not condition() and time.monotonic() < until:
            context.iteration(False)
            time.sleep(0.005)
        self.assertTrue(condition(), f"status: {self.manager.status!r}")

    def settle(self):
        self.wait(lambda: self.manager.status != "Procurando a letra…")

    def test_synced_file_next_to_the_song(self):
        self.song.with_name("Obsoleto.lrc").write_text("[00:01.00]um\n[00:02.00]dois", encoding="utf-8")
        self.manager._http = FakeHttp([])
        self.manager.load(str(self.song))
        self.settle()
        self.assertTrue(self.manager.lyrics["synced"])
        self.assertEqual(self.manager._http.calls, [], "Letra local sincronizada dispensa a internet")

    def test_lrclib_result_is_kept_and_reused(self):
        self.manager._http = FakeHttp([("/api/get?", fixture("lrclib_get_obsoleto"))])
        self.manager.load(str(self.song))
        self.settle()
        self.assertEqual(self.manager.lyrics["source"], "lrclib")
        self.assertEqual(self.page.music.lyrics_row(str(self.song))["synced"], 1)
        again = lyrics_manager.LyricsManager(self.page, self._changed)
        again._http = FakeHttp([])
        again.load(str(self.song))
        self.wait(lambda: again.lyrics is not None)
        self.assertEqual(again._http.calls, [], "Letra guardada não é buscada de novo")

    def test_plain_lyrics_explain_how_to_sync_them(self):
        (self.root / "Obsoleto_private.lrc").write_text("Hoje eu acordei assim\nMe sentindo obsoleto",
                                                        encoding="utf-8")
        self.manager._http = FakeHttp([("/api/get?", None), ("/api/search?", [])])
        self.manager.load(str(self.song))
        self.settle()
        self.assertFalse(self.manager.lyrics["synced"])
        self.assertIn("whisper-cpp", self.manager.status)

    def test_offline_and_not_found_messages(self):
        self.manager._http = FakeHttp([("/api/get?", None), ("/api/search?", [])])
        self.manager.load(str(self.song))
        self.settle()
        self.assertIsNone(self.manager.lyrics)
        self.assertEqual(self.manager.status, "Nenhuma letra encontrada.")
        self.assertEqual(self.page.music.lyrics_row(str(self.song))["source"], "nenhuma")
        self.page.store.set_setting("music.lyrics_online", True)
        other = lyrics_manager.LyricsManager(self.page, self._changed)
        other._http = FakeHttp([])
        other.load(str(self.song))
        self.wait(lambda: other.status == "Nenhuma letra encontrada.")
        self.assertEqual(other._http.calls, [], "Uma busca sem resultado não se repete logo")

    def test_delay_is_remembered_per_song(self):
        self.song.with_name("Obsoleto.lrc").write_text("[00:01.00]um\n[00:02.00]dois", encoding="utf-8")
        self.manager.load(str(self.song))
        self.settle()
        self.manager.shift(500)
        self.manager.shift(500)
        again = lyrics_manager.LyricsManager(self.page, self._changed)
        again.load(str(self.song))
        self.wait(lambda: again.lyrics is not None)
        self.assertEqual(again.lyrics["offset"], 1000)


if __name__ == "__main__":
    unittest.main()
