import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlencode

from audio_fixtures import can_encode, make_audio
from ayo_desk.music import lyrics, tags, voice

FIXTURES = Path(__file__).parent / "fixtures" / "lyrics"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


class FakeHttp:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, **_kwargs):
        full = url + ("?" + urlencode(params) if params else "")
        self.calls.append(full)
        for pattern, value in self.routes:
            if pattern in full:
                return value
        return None


class ParseTests(unittest.TestCase):
    def test_lrc_with_metadata_offsets_repeats_and_word_stamps(self):
        text = ("[ar:Seu Pereira]\n[offset:+250]\n[00:26.72] Hoje eu acordei assim\n"
                "[00:29.81][01:10.5]Me <00:30.10>sentindo obsoleto\n\n[01:05]Feito um CD\n")
        parsed = lyrics.parse(text, "teste")
        self.assertTrue(parsed["synced"])
        self.assertEqual(parsed["offset"], 250)
        self.assertEqual(parsed["lines"][0], (26720, "Hoje eu acordei assim"))
        self.assertEqual(parsed["lines"][1], (29810, "Me sentindo obsoleto"))
        self.assertEqual([ms for ms, _t in parsed["lines"]], [26720, 29810, 65000, 70500])

    def test_plain_text_even_with_lrc_extension(self):
        parsed = lyrics.parse("\nTodos esses que aí estão\nEles passarão, eu passarinho\n♪\n\n")
        self.assertFalse(parsed["synced"])
        self.assertEqual([t for _ms, t in parsed["lines"]],
                         ["Todos esses que aí estão", "Eles passarão, eu passarinho", "♪"])

    def test_current_line_follows_position_and_offset(self):
        parsed = lyrics.parse("[00:10.00]um\n[00:20.00]dois\n[00:30.00]três")
        self.assertEqual(lyrics.current_index(parsed, 5), -1)
        self.assertEqual(lyrics.current_index(parsed, 20.5), 1)
        parsed["offset"] = 1000  # lyrics one second late → shown one second earlier
        self.assertEqual(lyrics.current_index(parsed, 19.2), 1)
        self.assertEqual(lyrics.current_index(lyrics.parse("sem tempo"), 50), -1)

    def test_round_trip_to_lrc(self):
        parsed = lyrics.parse("[01:02.34]linha")
        again = lyrics.parse(lyrics.to_lrc(parsed, "Título", "Artista"))
        self.assertEqual(again["lines"], [(62340, "linha")])


class LocalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_private_sidecar_from_downloaders_is_found(self):
        song = self.root / "Canção(MP3_160K).mp3"
        song.write_bytes(b"x")
        (self.root / "Canção(MP3_160K)_private.lrc").write_text("linha um\nlinha dois", encoding="utf-8")
        found = lyrics.local(song)
        self.assertEqual(found["source"], "arquivo:Canção(MP3_160K)_private.lrc")
        self.assertFalse(found["synced"])

    def test_synced_sidecar_wins_over_plain(self):
        song = self.root / "faixa.mp3"
        song.write_bytes(b"x")
        (self.root / "faixa_private.lrc").write_text("só texto", encoding="utf-8")
        (self.root / "faixa.lrc").write_text("[00:01.00]com tempo", encoding="utf-8")
        self.assertTrue(lyrics.local(song)["synced"])
        self.assertIsNone(lyrics.local(self.root / "nada.mp3"))

    @unittest.skipUnless(tags.available() and can_encode(".mp3"), "Precisa de mutagen e lamemp3enc")
    def test_embedded_uslt_and_sylt(self):
        from mutagen.id3 import ID3, SYLT, USLT
        song = make_audio(self.root / "embutida.mp3")
        id3 = ID3(song)
        id3.add(USLT(encoding=3, lang="por", desc="", text="[00:02.00]primeira\n[00:04.00]segunda"))
        id3.save()
        self.assertTrue(lyrics.local(song)["synced"], "USLT com tempos conta como sincronizada")
        id3.delall("USLT")
        id3.add(SYLT(encoding=3, lang="por", format=2, type=1, text=[("oi", 1000), ("tchau", 3000)]))
        id3.save()
        self.assertEqual(lyrics.local(song)["lines"], [(1000, "oi"), (3000, "tchau")])


class LrcLibTests(unittest.TestCase):
    def test_exact_match_with_synced_lyrics(self):
        http = FakeHttp([("/api/get?", fixture("lrclib_get_obsoleto"))])
        found = lyrics.LrcLib(http).find("Seu Pereira e Coletivo 401", "Obsoleto", duration=224)
        self.assertTrue(found["synced"].startswith("[00:26.72]"))
        self.assertIn("duration=224", http.calls[0])

    def test_search_fallback_and_duration_filter(self):
        http = FakeHttp([("/api/get?", None), ("/api/search?", fixture("lrclib_search_batalha"))])
        found = lyrics.LrcLib(http).find("Seu Pereira", "Batalha Diária", duration=117)
        self.assertEqual(found["synced"], "")
        self.assertTrue(found["plain"])
        self.assertIsNone(lyrics.LrcLib(http).find("Seu Pereira", "Batalha Diária", duration=300))


class AlignTests(unittest.TestCase):
    def test_plain_lyrics_get_times_from_heard_words(self):
        plain = lyrics.parse("Hoje eu acordei assim\nMe sentindo obsoleto\n\nFeito um CD na estante")
        heard = [("hoje", 26700, 27000), ("eu", 27000, 27200), ("acordei", 27200, 28000), ("assim", 28000, 29000),
                 ("uh", 29100, 29400), ("me", 29800, 30000), ("sentido", 30000, 30500), ("obsoleto", 30500, 31500),
                 ("feito", 33000, 33300), ("um", 33300, 33400), ("cd", 33400, 33900)]
        synced, share = lyrics.align(plain, heard)
        self.assertEqual([ms for ms, _t in synced["lines"]], [26700, 29800, 33000])
        self.assertGreater(share, 0.7)
        self.assertEqual(synced["source"], "voz")

    def test_lines_without_matches_are_placed_between_neighbours(self):
        plain = lyrics.parse("primeira linha\nlinha cantada errado\nterceira linha")
        heard = [("primeira", 1000, 1200), ("linha", 1200, 1500), ("xyz", 4000, 4200),
                 ("terceira", 9000, 9200), ("linha", 9200, 9500)]
        synced, _share = lyrics.align(plain, heard)
        first, middle, last = [ms for ms, _t in synced["lines"]]
        self.assertTrue(first < middle < last)
        self.assertIsNone(lyrics.align(plain, [])[0])


class VoiceTests(unittest.TestCase):
    def test_tokens_are_joined_into_words_with_times(self):
        data = {"transcription": [{"tokens": [
            {"text": "[_BEG_]", "offsets": {"from": 0, "to": 0}},
            {"text": " Ho", "offsets": {"from": 26700, "to": 26800}},
            {"text": "je", "offsets": {"from": 26800, "to": 27000}},
            {"text": " eu", "offsets": {"from": 27000, "to": 27200}},
            {"text": ",", "offsets": {"from": 27200, "to": 27210}},
            {"text": " [_TT_50]", "offsets": {"from": 27300, "to": 27300}}]}]}
        self.assertEqual(voice.words_from_json(data), [("Hoje", 26700, 27000), ("eu,", 27000, 27210)])

    def test_language_guess(self):
        self.assertEqual(voice.guess_language("eu não sei o que você quer de mim meu amor, tudo que tem pra "
                                              "gente é isso"), "pt")
        self.assertEqual(voice.guess_language("I don't know what you want from me, my love, it's all "
                                              "that we have and the night"), "en")
        self.assertEqual(voice.guess_language("oi"), "auto")


if __name__ == "__main__":
    unittest.main()
