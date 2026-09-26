from pathlib import Path
import tempfile
import time
import unittest
import wave
from ayo_desk.player import Gst, Player
from gi.repository import GLib

Gst.init(None)
HAS_WAV = bool(Gst.ElementFactory.find("wavparse") and Gst.ElementFactory.find("audioconvert"))


@unittest.skipUnless(HAS_WAV, "Instale gst-plugins-base e gst-plugins-good para testar reprodução")
class PlayerTests(unittest.TestCase):
    def test_real_decode_pause_seek_and_end_without_speaker_output(self):
        with tempfile.TemporaryDirectory() as temp:
            track = Path(temp) / "faixa com espaços.wav"
            with wave.open(str(track), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(b"\0\0" * 16000)
            errors, ended = [], []
            sink = Gst.ElementFactory.make("fakesink")
            sink.set_property("sync", True)
            player = Player(lambda: None, errors.append, lambda: ended.append(True), sink=sink)
            try:
                player.load(track)
                deadline = time.monotonic() + 5
                while player.position()[1] == 0 and time.monotonic() < deadline:
                    GLib.MainContext.default().iteration(False)
                    time.sleep(.01)
                self.assertFalse(errors)
                self.assertAlmostEqual(player.position()[1], 2, places=1)
                player.toggle()
                self.assertFalse(player.playing)
                self.assertTrue(player.seek(1.5))
                player.toggle()
                self.assertTrue(player.playing)
                deadline = time.monotonic() + 4
                while not ended and not errors and time.monotonic() < deadline:
                    GLib.MainContext.default().iteration(False)
                    time.sleep(.01)
                self.assertFalse(errors)
                self.assertTrue(ended)
            finally:
                player.close()


if __name__ == "__main__":
    unittest.main()
