from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from audio_fixtures import can_encode, make_audio, png_bytes
from ayo_desk.core import Store
from ayo_desk.music import covers, tags
from ayo_desk.music.db import MusicDB
from ayo_desk.music.library import read_changes, scan_music_folder


class InferenceTests(unittest.TestCase):
    def guess(self, relative, root="/musica"):
        return tags.infer_from_path(Path(root) / relative, root)

    def test_names_from_the_real_library_layout(self):
        self.assertEqual(self.guess("Album - Gêmeos/01 - Esperando Você.mp3"),
                         {"track_no": 1, "title": "Esperando Você", "album": "Gêmeos"})
        self.assertEqual(self.guess("Awakening：Sleeping/2 - いらない (feat. 蛯名啓太).mp3")["album"], "Awakening:Sleeping")
        self.assertEqual(self.guess("X/02 - Onde Que Nós Taria？.mp3")["title"], "Onde Que Nós Taria?")
        self.assertEqual(self.guess("Salad Days/Blue Boy [GXuIWm12S24].mp3")["title"], "Blue Boy")
        self.assertEqual(self.guess("Mundi Opus/You are back in the game _ Skyfall Soundtrack(MP3_320K).mp3")["title"],
                         "You are back in the game _ Skyfall Soundtrack")

    def test_numbers_that_are_part_of_the_title(self):
        guess = self.guess("Primo de 2° Grau/3 DA MADRUGA [0_FK7U51H3c].mp3")
        self.assertEqual(guess["title"], "3 DA MADRUGA")
        self.assertNotIn("track_no", guess)
        self.assertEqual(self.guess("Locals 2/2 - 27 Reasons.mp3")["title"], "27 Reasons")
        self.assertEqual(self.guess("A/1979.mp3")["title"], "1979")

    def test_title_with_dash_is_not_split_into_artist(self):
        guess = self.guess("Another (Demo) One/01 - A Heart Like Hers - Demo.mp3")
        self.assertEqual(guess["title"], "A Heart Like Hers - Demo")
        self.assertNotIn("artist", guess)

    def test_artist_album_disc_and_year_folders(self):
        guess = self.guess("Mac DeMarco/Salad Days (2014)/CD 2/1-03 Brother.flac")
        self.assertEqual(guess, {"disc_no": 1, "track_no": 3, "title": "Brother", "album": "Salad Days",
                                 "year": 2014, "artist": "Mac DeMarco"})
        self.assertEqual(self.guess("Taylor/1989/01 - Welcome.mp3")["album"], "1989")
        self.assertNotIn("album", self.guess("solta.mp3"), "Arquivo na raiz não tem álbum")

    def test_fold_ignores_case_accents_and_fullwidth(self):
        self.assertEqual(tags.fold("São JOÃO"), "sao joao")
        self.assertEqual(tags.fold("𝐌𝐔𝐍𝐃𝐈 𝐎𝐏𝐔𝐒"), "mundi opus")
        self.assertIn(tags.fold("coracao"), tags.fold("Eu Ainda Tenho Coração"))


@unittest.skipUnless(tags.available(), "Instale python-mutagen")
class TagReadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "Música"
        self.cache = patch.dict(os.environ, {"XDG_CACHE_HOME": str(Path(self.temp.name) / "cache")})
        self.cache.start()

    def tearDown(self):
        self.cache.stop()
        self.temp.cleanup()

    def tagged(self, relative, **fields):
        import mutagen
        path = make_audio(self.root / relative)
        audio = mutagen.File(path, easy=True)
        if audio.tags is None:
            audio.add_tags()
        for key, value in fields.items():
            audio[key] = value
        audio.save()
        return str(path)

    def test_tags_win_and_names_fill_the_gaps(self):
        for suffix in (".mp3", ".flac", ".ogg"):
            if not can_encode(suffix):
                continue
            with self.subTest(suffix):
                path = self.tagged(f"Album - Pasta/07 - Nome do arquivo{suffix}", artist="Terno Rei",
                                   title="﻿Título da tag", tracknumber="3/12", date="2019-05-01")
                meta, _ = tags.read(path, root=self.root)
                self.assertEqual(meta["title"], "Título da tag")
                self.assertEqual(meta["artist"], "Terno Rei")
                self.assertEqual((meta["track_no"], meta["track_total"]), (3, 12))
                self.assertEqual(meta["year"], 2019)
                self.assertEqual(meta["album"], "Pasta")
                self.assertEqual(meta["inferred"], "album")
                self.assertAlmostEqual(meta["duration"], 1.0, delta=0.15)
                self.assertGreater(meta["sample_rate"], 0)

    def test_embedded_cover_and_replaygain_in_mp3(self):
        if not can_encode(".mp3"):
            self.skipTest("lamemp3enc indisponível")
        from mutagen.id3 import APIC, ID3, TXXX, USLT
        path = make_audio(self.root / "capa.mp3")
        id3 = ID3(path)
        image = png_bytes()
        id3.add(APIC(encoding=3, mime="image/png", type=3, desc="Capa", data=image))
        id3.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text=["-6.48 dB"]))
        id3.add(USLT(encoding=3, lang="por", desc="", text="Uma linha"))
        id3.save()
        meta, cover = tags.read(path, with_cover=True)
        self.assertEqual(cover, image)
        self.assertEqual(meta["rg_track_gain"], -6.48)
        self.assertEqual(meta["has_lyrics"], 1)
        self.assertEqual(meta["codec"], "MP3")

    def test_broken_file_falls_back_to_the_name(self):
        path = self.root / "Álbum/01 - Quebrada.mp3"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"not audio at all")
        meta, cover = tags.read(path, root=self.root, with_cover=True)
        self.assertEqual((meta["title"], meta["track_no"], meta["album"]), ("Quebrada", 1, "Álbum"))
        self.assertIsNone(cover)


class CoverCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"XDG_CACHE_HOME": self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_store_thumbnail_dedupe_and_prune(self):
        first, second = png_bytes((255, 0, 0), 600), png_bytes((0, 0, 255))
        key = covers.store(first)
        self.assertEqual(covers.store(first), key)
        self.assertEqual(covers.path(key, thumbnail=False).read_bytes(), first)
        from gi.repository import GdkPixbuf
        thumb = GdkPixbuf.Pixbuf.new_from_file(str(covers.path(key)))
        self.assertEqual(thumb.get_width(), covers.THUMB_SIZE)
        other = covers.store(second)
        self.assertEqual(covers.store(b"garbage"), "")
        covers.prune({other})
        self.assertIsNone(covers.path(key))
        self.assertIsNotNone(covers.path(other))

    def test_folder_cover_is_case_insensitive(self):
        folder = Path(self.temp.name) / "album"
        folder.mkdir()
        (folder / "Folder.JPG").write_bytes(b"x")
        self.assertEqual(covers.folder_cover(folder).name, "Folder.JPG")


class LibraryDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "Música"
        self.store = Store(Path(self.temp.name) / "desk.sqlite3")
        self.music = MusicDB(self.store)
        self.env = patch.dict(os.environ, {"XDG_CACHE_HOME": str(Path(self.temp.name) / "cache")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.store.close()
        self.temp.cleanup()

    def sync(self):
        root, paths = scan_music_folder(self.root)
        self.store.sync_music_folder(root, paths)
        changed = read_changes(paths, self.music.signatures(), root=root)
        self.music.save_meta(changed)
        self.music.prune_meta(self.store.tracks())
        return changed

    def test_incremental_scan_reads_only_changed_files(self):
        first = make_audio(self.root / "Álbum/01 - Primeira.wav")
        second = make_audio(self.root / "Álbum/02 - Segunda.wav")
        (self.root / "Álbum/cover.png").write_bytes(png_bytes())
        self.assertEqual(len(self.sync()), 2)
        self.assertEqual(self.sync(), [])
        make_audio(second, seconds=2)
        os.utime(second, ns=(1, 1))
        self.assertEqual([m["path"] for m in self.sync()], [str(second)])
        library = {t["path"]: t for t in self.music.library()}
        self.assertEqual(library[str(first)]["title"], "Primeira")
        self.assertEqual(library[str(first)]["album"], "Álbum")
        self.assertTrue(library[str(first)]["cover"], "Capa da pasta usada quando não há capa embutida")
        added = library[str(first)]["added_at"]
        self.sync()
        self.assertEqual({t["path"]: t for t in self.music.library()}[str(first)]["added_at"], added)

    def test_removed_files_leave_metadata_but_keep_statistics(self):
        path = make_audio(self.root / "faixa.wav")
        self.sync()
        self.music.record_play(str(path), 120)
        self.music.set_rating(str(path), 5)
        path.unlink()
        self.sync()
        self.assertEqual(self.music.signatures(), {})
        make_audio(path)
        self.sync()
        track = self.music.library()[0]
        self.assertEqual((track["plays"], track["rating"]), (1, 5))

    def test_unscanned_manual_track_is_listed_from_its_name(self):
        manual = make_audio(Path(self.temp.name) / "Solta/03 - Avulsa.wav")
        self.store.add_tracks([manual])
        track = self.music.library()[0]
        self.assertEqual((track["title"], track["track_no"], track.get("pending")), ("Avulsa", 3, True))

    def test_statistics_validate_rating_and_resume(self):
        with self.assertRaises(ValueError):
            self.music.set_rating("/x.mp3", 6)
        self.music.set_favorite("/x.mp3", True)
        self.music.record_skip("/x.mp3")
        self.music.set_resume_position("/x.mp3", 1234.5)
        self.assertEqual(self.music.resume_position("/x.mp3"), 1234.5)
        row = self.store.db.execute("SELECT favorite, skips FROM music_stats WHERE path='/x.mp3'").fetchone()
        self.assertEqual(tuple(row), (1, 1))


if __name__ == "__main__":
    unittest.main()
