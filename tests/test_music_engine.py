from pathlib import Path
import tempfile
import time
import unittest

from audio_fixtures import make_audio
from ayo_desk.music.engine import Gst, Player
from gi.repository import GLib

Gst.init(None)
HAS_WAV = bool(Gst.ElementFactory.find("wavparse") and Gst.ElementFactory.find("audioconvert"))


def pump(until, seconds=5):
    deadline = time.monotonic() + seconds
    while not until() and time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
        time.sleep(.005)
    return until()


def silent_sink():
    sink = Gst.ElementFactory.make("fakesink")
    sink.set_property("sync", True)
    return sink


@unittest.skipUnless(HAS_WAV, "Instale gst-plugins-base e gst-plugins-good para testar reprodução")
class PlayerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.errors, self.ended, self.started = [], [], []

    def tearDown(self):
        self.temp.cleanup()

    def player(self):
        return Player(lambda: None, self.errors.append, lambda: self.ended.append(True),
                      started=self.started.append, sink=silent_sink())

    def test_real_decode_pause_seek_and_end_without_speaker_output(self):
        track = make_audio(Path(self.temp.name) / "faixa com espaços.wav", seconds=2)
        player = self.player()
        try:
            player.load(track)
            self.assertTrue(pump(lambda: player.position()[1] > 0))
            self.assertFalse(self.errors)
            self.assertAlmostEqual(player.position()[1], 2, places=1)
            player.toggle()
            self.assertFalse(player.playing)
            self.assertTrue(player.seek(1.5))
            player.toggle()
            self.assertTrue(player.playing)
            finished = pump(lambda: self.ended or self.errors, 8)  # generous: CI machines can be slow
            _r, state, pending = player.playbin.get_state(0)
            self.assertTrue(finished, f"state={state.value_nick} pending={pending.value_nick} "
                                      f"pos={player.position()} playing={player.playing}")
            self.assertFalse(self.errors)
        finally:
            player.close()

    def test_gapless_switch_reports_the_new_track_without_eos(self):
        first = make_audio(Path(self.temp.name) / "01.wav", seconds=0.4)
        second = make_audio(Path(self.temp.name) / "02.wav", seconds=0.4)
        player = self.player()
        try:
            player.load(first)
            player.set_next(second)
            self.assertTrue(pump(lambda: self.started or self.ended or self.errors))
            self.assertEqual(self.started, [str(second)])
            self.assertEqual(player.path, str(second))
            self.assertFalse(self.ended, "Não pode haver fim de fila entre faixas gapless")
            player.set_next(None)
            self.assertTrue(pump(lambda: self.ended or self.errors))
            self.assertFalse(self.errors)
        finally:
            player.close()

    def test_load_paused_at_a_position_for_session_restore(self):
        track = make_audio(Path(self.temp.name) / "retomar.wav", seconds=3)
        player = self.player()
        try:
            player.load(track, play=False, start=2.0)
            self.assertFalse(player.playing)
            self.assertTrue(pump(lambda: player.position()[0] >= 1.9))
            self.assertAlmostEqual(player.position()[0], 2.0, delta=0.15)
        finally:
            player.close()

    def test_speed_keeps_running_and_survives_seek(self):
        track = make_audio(Path(self.temp.name) / "rapida.wav", seconds=3)
        player = self.player()
        try:
            player.set_rate(1.5)
            player.load(track)
            self.assertTrue(pump(lambda: player.position()[0] > 0.3))
            start, began = player.position()[0], time.monotonic()
            self.assertTrue(pump(lambda: player.position()[0] - start >= 0.6, 3))
            elapsed = time.monotonic() - began
            self.assertLess(elapsed, 0.6 / 1.2, "1,5× deve andar mais rápido que o relógio")
            self.assertTrue(player.seek(0.5))
            self.assertEqual(player.rate, 1.5)
            player.set_rate(9)
            self.assertEqual(player.rate, 2.0)
        finally:
            player.close()

    def test_speed_change_near_a_gapless_switch_never_hangs(self):
        # Regression: seeking to apply the rate while playbin switched tracks deadlocked the app.
        first = make_audio(Path(self.temp.name) / "curta.wav", seconds=0.3)
        second = make_audio(Path(self.temp.name) / "seguinte.wav", seconds=0.3)
        for delay in (0.0, 0.05, 0.15, 0.25):
            player = self.player()
            try:
                player.load(first)
                player.set_next(second)
                pump(lambda: False, delay)
                player.set_rate(1.25)
                self.assertTrue(pump(lambda: self.ended or self.started, 3))
            finally:
                player.close()
                self.ended.clear()
                self.started.clear()

    def test_fade_out_then_pause(self):
        track = make_audio(Path(self.temp.name) / "fade.wav", seconds=3)
        player = self.player()
        try:
            player.load(track)
            player.set_playing(False, fade=0.2)
            self.assertFalse(player.playing)
            self.assertTrue(pump(lambda: player._fade == 1.0 and not player._fade_source, 2))
            _ok, state, _pending = player.playbin.get_state(Gst.SECOND)
            self.assertEqual(state, Gst.State.PAUSED)
        finally:
            player.close()

    def test_crossfade_switches_decks_without_eos(self):
        first = make_audio(Path(self.temp.name) / "a.wav", seconds=3)
        second = make_audio(Path(self.temp.name) / "b.wav", seconds=3)
        player = self.player()
        try:
            player.load(first)
            self.assertTrue(pump(lambda: player.position()[0] > 0.2))
            old = player.deck
            player.crossfade_to(second, 0.4, gain=0.5)
            self.assertTrue(player.crossfading)
            self.assertEqual(player.path, str(second))
            self.assertIsNot(player.deck, old)
            self.assertAlmostEqual(player.deck.gain.get_property("volume"), 0.5)
            self.assertTrue(pump(lambda: not player.crossfading, 3))
            _ok, state, _p = old.playbin.get_state(Gst.SECOND)
            self.assertEqual(state, Gst.State.NULL, "O deck antigo para depois do crossfade")
            self.assertEqual(player.deck.level, 1.0)
            self.assertTrue(player.position()[0] > 0.2)
            self.assertFalse(self.ended or self.errors)
        finally:
            player.close()

    def test_equalizer_gain_and_spectrum(self):
        track = make_audio(Path(self.temp.name) / "tom.wav", seconds=2)
        frames = []
        player = Player(lambda: None, self.errors.append, lambda: self.ended.append(True), sink=silent_sink(),
                        spectrum=lambda levels, end: frames.append((levels, end)))
        try:
            player.set_equalizer([6, 3, 0, 0, 0, 0, 0, 0, -3, 40])
            self.assertEqual(player.deck.eq.get_property("band0"), 6)
            self.assertEqual(player.decks[1].eq.get_property("band9"), 12, "Limite de +12 dB")
            player.load(track, gain=2.0)
            self.assertAlmostEqual(player.deck.gain.get_property("volume"), 2.0)
            player.set_spectrum(True)
            self.assertTrue(pump(lambda: len(frames) >= 3, 3))
            self.assertEqual(len(frames[0][0]), 48)
            player.set_spectrum(False)
        finally:
            player.close()

    def test_missing_file_is_reported(self):
        player = self.player()
        try:
            with self.assertRaises(ValueError):
                player.load(Path(self.temp.name) / "sumiu.mp3")
        finally:
            player.close()


if __name__ == "__main__":
    unittest.main()
