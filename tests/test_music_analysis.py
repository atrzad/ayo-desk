from pathlib import Path
import os
import tempfile
import threading
import unittest
from concurrent.futures import CancelledError

from audio_fixtures import make_audio
from ayo_desk.core import Store
from ayo_desk.music.analysis import analyze, from_bytes, leveling_factor, resample
from ayo_desk.music import tags
from ayo_desk.music.db import MusicDB


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def test_measures_loudness_and_waveform_of_a_tone(self):
        tone = make_audio(Path(self.temp.name) / "tom.flac", seconds=3, tone=True)
        result = analyze(tone, duration=3, bars=60)
        self.assertIsNotNone(result["gain"])
        self.assertLess(result["gain"], 0, "Um seno cheio é mais alto que a referência")
        self.assertGreater(result["peak"], 0.5)
        self.assertEqual(len(result["waveform"]), 60)
        self.assertGreater(max(from_bytes(result["waveform"])), 0.9)

    def test_silence_and_cancellation(self):
        silence = make_audio(Path(self.temp.name) / "mudo.wav", seconds=1)
        result = analyze(silence, duration=1, bars=20)
        self.assertEqual(len(result["waveform"]), 20)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(CancelledError):
            analyze(silence, cancel=cancel)

    def test_broken_file_raises(self):
        broken = Path(self.temp.name) / "quebrado.mp3"
        broken.write_bytes(b"nada")
        with self.assertRaises(ValueError):
            analyze(broken)

    def test_leveling_factor_limits_boosts(self):
        self.assertAlmostEqual(leveling_factor(-6.0), 10 ** (-6 / 20))
        self.assertEqual(leveling_factor(None), 1.0)
        self.assertEqual(leveling_factor(6.0, peak=0.9), 1 / 0.9, "Não passa do pico")
        self.assertEqual(leveling_factor(3.0, allow_boost=False), 1.0)
        self.assertAlmostEqual(leveling_factor(-3.0, preamp=3.0), 1.0)

    def test_resample(self):
        self.assertEqual(resample([1, 5, 2, 8], 2), [5, 8])
        self.assertEqual(len(resample([0.5] * 3, 10)), 10)
        self.assertEqual(resample([], 3), [0.0, 0.0, 0.0])


class AnalysisStorageTests(unittest.TestCase):
    def test_pending_fresh_and_gain_priority(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "d.sqlite3")
            music = MusicDB(store)
            try:
                def meta(path, **extra):
                    return dict(tags.empty(path), size=1, search="", cover="", mtime_ns=1, **extra)
                music.save_meta([meta("/a.mp3"), meta("/b.mp3", rg_track_gain=-4.0, rg_track_peak=0.8)])
                self.assertEqual(music.pending_analysis(), ["/a.mp3", "/b.mp3"])
                music.save_analysis("/a.mp3", 1, -9.0, 1.0, b"\x00\xff")
                music.save_analysis("/b.mp3", 1, -1.0, 0.5, b"")
                self.assertEqual(music.pending_analysis(), [])
                self.assertTrue(music.analysis("/a.mp3")["fresh"])
                self.assertEqual(music.gains(), {"/a.mp3": (-9.0, 1.0), "/b.mp3": (-4.0, 0.8)})
                music.save_meta([dict(meta("/a.mp3"), mtime_ns=2)])
                self.assertEqual(music.pending_analysis(), ["/a.mp3"], "Arquivo alterado é medido de novo")
                self.assertFalse(music.analysis("/a.mp3")["fresh"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
