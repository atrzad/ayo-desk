import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from audio_fixtures import can_encode, make_audio, png_bytes
from ayo_desk.music import covers, tags
from ayo_desk.music.identify import clean, matching, songrec, writer
from ayo_desk.music.identify.deezer import Deezer
from ayo_desk.music.identify.job import IdentifyJob, Identifier, changes
from ayo_desk.music.identify.musicbrainz import MusicBrainz
from ayo_desk.music.net import Offline
from gi.repository import GLib

FIXTURES = Path(__file__).parent / "fixtures" / "identify"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


class FakeHttp:
    """Routes URL substrings to canned responses; unknown URLs behave like a 404."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, cache=True, binary=False, **_kwargs):
        full = url + ("?" + urlencode(params) if params else "")
        self.calls.append(full)
        for pattern, value in self.routes:
            if pattern in full:
                return value(full) if callable(value) else value
        return None


SEARCH = fixture("deezer_search_seu_pereira_otario")
TRACK = fixture("deezer_track_otario")
ALBUM = fixture("deezer_album_otario")
COVER = png_bytes((10, 120, 200), 64)


def deezer_routes():
    return [("/search?", SEARCH), (f"/track/isrc:{TRACK['isrc']}", TRACK), ("/track/isrc:", {"error": {"code": 800}}),
            (f"/track/{TRACK['id']}", TRACK), (f"/album/{ALBUM['id']}", ALBUM), ("cdn-images.dzcdn.net", COVER)]


def shazam_json(title="Otário", artist="Seu Pereira e Coletivo 401", isrc=TRACK["isrc"]):
    return json.dumps({"matches": [{"id": "1"}], "track": {
        "key": "123", "title": title, "subtitle": artist, "isrc": isrc, "genres": {"primary": "Rock"},
        "images": {"coverart": "https://is1-ssl.mzstatic.com/image/thumb/a.jpg/400x400cc.jpg"},
        "sections": [{"type": "SONG", "metadata": [{"title": "Album", "text": "Eu Não Sou Boa Influência pra Você"},
                                                   {"title": "Released", "text": "2017"}]}]}})


class CleanTests(unittest.TestCase):
    def test_youtube_download_names_and_tags(self):
        self.assertEqual(clean.artist_list("﻿Tyler |  The Creator"), ["Tyler, The Creator"])
        self.assertEqual(clean.artist_list("Madvillain - Topic"), ["Madvillain"])
        self.assertEqual(clean.clean("Operation_ Greenbacks"), "Operation: Greenbacks")
        self.assertEqual(clean.clean("See You On Monday (You_re Lost)"), "See You On Monday (You're Lost)")
        self.assertEqual(clean.core_title("Hero v.s. Villain (Epilogue) [feat. E. Mason] (Official Audio)"),
                         "Hero v.s. Villain")
        self.assertEqual(clean.featured("Doomsday (feat. Pebbles The Invisible Girl)"), ["Pebbles The Invisible Girl"])
        self.assertEqual(clean.versions_of("Otário (Ao Vivo)"), {"live"})
        self.assertEqual(clean.versions_of("A Heart Like Hers - Demo"), {"demo"})
        self.assertEqual(clean.display_title("Dear God [Official Music Video]"), "Dear God")

    def test_hints_drop_artist_named_albums_and_artist_prefixes(self):
        hint = clean.hints({"path": "/m/Deftones – Rx Queen (Stephen Carpenter Play-Through)(MP3_160K).mp3",
                            "title": "Deftones – Rx Queen (Stephen Carpenter Play-Through)", "artist": "Deftones",
                            "album": "Deftones", "duration": 260})
        self.assertEqual((hint["core"], hint["album"]), ("Rx Queen", ""))
        self.assertEqual(hint["queries"][0], "Deftones Rx Queen")
        title_only = clean.hints({"path": "/m/OTÁRIO _ Seu Pereira e Coletivo 401(MP3_160K).mp3", "title": ""})
        self.assertEqual(title_only["queries"], ["OTÁRIO Seu Pereira e Coletivo 401"])


class MatchingTests(unittest.TestCase):
    def test_studio_version_wins_over_live_by_title_and_duration(self):
        hint = clean.hints({"path": "/m/x.mp3", "title": "Otário", "artist": "Seu Pereira", "duration": 229})
        ranked = matching.best(hint, Deezer(FakeHttp(deezer_routes())).search("Seu Pereira Otário"))
        self.assertEqual(ranked[0][2]["album"], "Eu Não Sou Boa Influência pra Você")
        self.assertGreaterEqual(ranked[0][0], matching.AUTO)
        live = next(item for item in ranked if "Ao Vivo" in item[2]["title"])
        self.assertLess(live[0], matching.AUTO, "A versão ao vivo nunca é gravada sozinha")
        self.assertLess(live[0], ranked[0][0])
        self.assertIn("é versão live", live[1])

    def test_unknown_artist_never_reaches_automatic_on_text_alone(self):
        hint = {"title": "Otário", "artists": [], "duration": 229, "versions": set()}
        value, reasons = matching.score(hint, {"title": "Otário", "artists": ["Seu Pereira e Coletivo 401"],
                                               "duration": 229})
        self.assertLess(value, matching.AUTO)
        self.assertIn("artista desconhecido no arquivo", reasons)

    def test_wrong_artist_and_length_are_rejected(self):
        hint = {"title": "Otário", "artists": ["Cássia Eller"], "duration": 229, "versions": set()}
        value, _ = matching.score(hint, {"title": "Otário", "artists": ["Seu Pereira e Coletivo 401"],
                                         "duration": 340})
        self.assertLess(value, matching.REVIEW)


class ProviderTests(unittest.TestCase):
    def test_deezer_complete_merges_track_and_album(self):
        deezer = Deezer(FakeHttp(deezer_routes()))
        full = deezer.complete(deezer.search("x")[0])
        self.assertEqual((full["track_no"], full["track_total"], full["year"]), (2, 10, 2017))
        self.assertEqual((full["isrc"], full["genre"]), ("BREOQ1700002", "Rock"))
        self.assertEqual(full["album_artist"], "Seu Pereira e Coletivo 401")
        self.assertTrue(full["cover_url"].startswith("https://cdn-images.dzcdn.net"))
        self.assertIsNone(deezer.by_isrc("XX0000000000"))
        self.assertEqual(Deezer(FakeHttp([("/search?", fixture("deezer_search_nothing"))])).search("zz"), [])

    def test_musicbrainz_search_and_cover(self):
        http = FakeHttp([("/recording?", fixture("musicbrainz_search_doom")),
                         ("coverartarchive.org/release/", {"images": [{"front": True, "image": "https://ia/full.jpg",
                                                                      "thumbnails": {"1200": "https://ia/1200.jpg"}}]})])
        brainz = MusicBrainz(http)
        found = brainz.search("Hero v.s. Villain", ["MF DOOM"], 175)
        self.assertEqual(found[0]["artists"][0], "MF DOOM")
        self.assertAlmostEqual(found[0]["duration"], 175)
        self.assertIn('recording:"Hero v.s. Villain"', http.calls[0].replace("%22", '"').replace("+", " ")
                      .replace("%3A", ":").replace("%28", "(").replace("%29", ")"))
        self.assertEqual(brainz.complete(found[0])["cover_url"], "https://ia/1200.jpg")

    def test_songrec_output(self):
        heard = songrec.parse(shazam_json())
        self.assertEqual((heard["title"], heard["artists"], heard["year"]), ("Otário", ["Seu Pereira e Coletivo 401"], 2017))
        self.assertTrue(heard["cover_url"].endswith("/1000x1000cc.jpg"))
        self.assertIsNone(songrec.parse('{"matches": [], "tagid": "x"}'))
        self.assertIsNone(songrec.parse("not json"))

        class Done:
            returncode, stdout = 0, shazam_json()
        with patch.object(songrec, "available", return_value=True):
            self.assertEqual(songrec.recognize("/m/a.mp3", run=lambda *a, **k: Done())["isrc"], TRACK["isrc"])


class IdentifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict("os.environ", {"XDG_CACHE_HOME": self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def info(self, **fields):
        base = {"path": "/m/Otário(MP3_160K).mp3", "title": "Otário", "artist": "Seu Pereira e Coletivo 401",
                "album": "Seu Pereira e Coletivo 401", "duration": 229.4, "cover": "video", "genre": "Music"}
        base.update(fields)
        return base

    def test_confident_match_fills_everything_and_replaces_the_video_cover(self):
        identifier = Identifier(FakeHttp(deezer_routes()), use_songrec=False, use_musicbrainz=False)
        result = identifier.identify(self.info())
        self.assertEqual(result["status"], "auto")
        change = result["changes"]
        self.assertEqual(change["album"], "Eu Não Sou Boa Influência pra Você")
        self.assertEqual((change["track_no"], change["track_total"], change["date"]), (2, 10, "2017-10-01"))
        self.assertEqual((change["isrc"], change["genre"]), ("BREOQ1700002", "Rock"))
        self.assertNotIn("title", change, "O título já estava certo")
        self.assertEqual(covers.path(change["cover"], thumbnail=False).read_bytes(), COVER)

    def test_title_only_file_is_confirmed_by_sound(self):
        identifier = Identifier(FakeHttp(deezer_routes()), use_musicbrainz=False)
        identifier.last_recognition = -100
        with patch.object(songrec, "available", return_value=True), \
                patch.object(songrec, "recognize", return_value=songrec.parse(shazam_json())) as heard:
            result = identifier.identify(self.info(title="", artist="", album="", path="/m/faixa 07.mp3"))
        heard.assert_called_once()
        self.assertEqual(result["status"], "auto")
        self.assertIn("reconhecida pelo som", result["reasons"])
        self.assertEqual(result["changes"]["artist"], "Seu Pereira e Coletivo 401")

    def test_ambiguous_name_is_read_as_title_then_artist(self):
        identifier = Identifier(FakeHttp(deezer_routes()), use_songrec=False, use_musicbrainz=False)
        result = identifier.identify(self.info(title="", artist="", album="",
                                               path="/m/OTÁRIO _ Seu Pereira e Coletivo 401(MP3_160K).mp3"))
        self.assertEqual(result["status"], "auto")
        self.assertEqual(result["changes"]["artist"], "Seu Pereira e Coletivo 401")
        hint = clean.hints({"path": "/m/05 - MENSAGEM FAVORITA - DJ ARANA (ÁLBUM - ROCK PESADO 2)(MP3_160K).mp3",
                            "title": ""})
        self.assertEqual(hint["album"], "ROCK PESADO 2")
        self.assertIn(("MENSAGEM FAVORITA", ["DJ ARANA"]), [(r["title"], r["artists"]) for r in hint["readings"]])

    def test_nothing_found_and_already_correct(self):
        empty = Identifier(FakeHttp([("/search?", fixture("deezer_search_nothing"))]), use_songrec=False,
                           use_musicbrainz=False)
        self.assertEqual(empty.identify(self.info())["status"], "missing")
        correct = Identifier(FakeHttp(deezer_routes()), use_songrec=False, use_musicbrainz=False,
                             replace_covers=False)
        done = self.info(album="Eu Não Sou Boa Influência pra Você", album_artist="Seu Pereira e Coletivo 401",
                         year=2017, track_no=2, track_total=10, disc_no=1, genre="Rock", isrc="BREOQ1700002")
        self.assertEqual(correct.identify(done)["status"], "unchanged")

    def test_changes_keep_real_genres_and_never_erase(self):
        diff = changes({"genre": "Samba", "album": "Algo", "date": "2017"},
                       {"genre": "Pop", "album": "", "date": "2017-10-01", "title": "Novo"})
        self.assertEqual(diff, {"title": "Novo"})


class AlbumTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict("os.environ", {"XDG_CACHE_HOME": self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_album_tracks_are_matched_by_title_and_length(self):
        album_hit = {"data": [{"id": ALBUM["id"], "title": ALBUM["title"], "nb_tracks": 10, "record_type": "album",
                               "cover_xl": ALBUM["cover_xl"], "artist": {"name": "Seu Pereira e Coletivo 401"}}]}
        http = FakeHttp([("/search/album?", album_hit), *deezer_routes()])
        identifier = Identifier(http, use_songrec=False, use_musicbrainz=False)
        infos = [{"path": "/m/a/02.mp3", "title": "otario", "artist": "", "album": "Eu nao sou boa influencia",
                  "duration": 229}, {"path": "/m/a/xx.mp3", "title": "Faixa bônus que não existe", "duration": 50}]
        album, results = identifier.identify_album(infos, "Eu Não Sou Boa Influência pra Você", "Seu Pereira")
        self.assertEqual(album["album"], "Eu Não Sou Boa Influência pra Você")
        self.assertEqual(results[0]["status"], "auto")
        self.assertEqual(results[0]["changes"]["track_no"], 2)
        self.assertEqual(results[1]["status"], "missing")
        nothing = Identifier(FakeHttp([("/search/album?", {"data": []})]), use_songrec=False, use_musicbrainz=False)
        self.assertEqual(nothing.identify_album(infos, "Álbum que não existe", "Ninguém"), (None, []))


class JobTests(unittest.TestCase):
    def test_job_reports_results_retries_offline_and_can_stop(self):
        calls = {"n": 0}

        class Flaky:
            def identify(self, info):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise Offline("sem rede")
                if info["path"] == "/bad":
                    raise ValueError("arquivo estranho")
                return {"path": info["path"], "status": "auto"}
        results, done = [], []
        job = IdentifyJob(Flaky(), [{"path": "/a"}, {"path": "/bad"}], results.append,
                          finished=done.append, retry_delay=0.01)
        job.start()
        deadline = time.monotonic() + 5
        while not done and time.monotonic() < deadline:
            GLib.MainContext.default().iteration(False)
            time.sleep(0.005)
        self.assertEqual([r["status"] for r in results], ["auto", "error"])
        self.assertEqual(done, [True])


@unittest.skipUnless(writer.available(), "Instale python-mutagen")
class WriterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict("os.environ", {"XDG_CACHE_HOME": self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_apply_then_restore_round_trip(self):
        new_cover = covers.store(png_bytes((250, 10, 10), 32))
        old_image = png_bytes((10, 250, 10), 16)
        for suffix in (".mp3", ".flac", ".ogg"):
            if not can_encode(suffix):
                continue
            with self.subTest(suffix):
                import mutagen
                path = make_audio(Path(self.temp.name) / f"faixa{suffix}")
                audio = mutagen.File(path, easy=True)
                if audio.tags is None:
                    audio.add_tags()
                audio["title"], audio["artist"] = "Título velho", "Madvillain - Topic"
                audio.save()
                if suffix == ".mp3":
                    from mutagen.id3 import APIC, ID3
                    id3 = ID3(path)
                    id3.add(APIC(encoding=3, mime="image/png", type=3, desc="", data=old_image))
                    id3.save()
                before = tags.read(path, with_cover=True)
                backup = writer.apply(str(path), {"title": "Curls", "artist": "Madvillain", "album": "Madvillainy",
                                                  "date": "2004-03-23", "track_no": 5, "track_total": 22,
                                                  "isrc": "USSM10400001", "cover": new_cover})
                meta, cover = tags.read(path, with_cover=True)
                self.assertEqual((meta["title"], meta["artist"], meta["album"]), ("Curls", "Madvillain", "Madvillainy"))
                self.assertEqual((meta["track_no"], meta["track_total"], meta["year"]), (5, 22, 2004))
                self.assertEqual(writer._read_isrc(path), "USSM10400001")
                self.assertEqual(cover, covers.path(new_cover, thumbnail=False).read_bytes())
                writer.restore(str(path), backup)
                meta, cover = tags.read(path, with_cover=True)
                self.assertEqual((meta["title"], meta["artist"]), ("Título velho", "Madvillain - Topic"))
                self.assertIsNone(meta["track_no"] if "track_no" not in (meta["inferred"] or "") else None)
                self.assertEqual(writer._read_isrc(path), "")
                self.assertEqual(cover, before[1], "A capa antiga (ou nenhuma) volta")


if __name__ == "__main__":
    unittest.main()
