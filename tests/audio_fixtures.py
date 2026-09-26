"""Small real audio files generated on the fly (never touches the user's library)."""
from pathlib import Path
import wave

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst

Gst.init(None)
ENCODERS = {".mp3": "lamemp3enc ! id3v2mux", ".flac": "flacenc", ".ogg": "vorbisenc ! oggmux",
            ".opus": "opusenc ! oggmux"}


def can_encode(suffix):
    return suffix == ".wav" or all(Gst.ElementFactory.find(part.strip().split()[0])
                                   for part in ENCODERS[suffix].split("!"))


def make_audio(path, seconds=1.0, rate=44100, tone=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".wav":
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(b"\0\0" * int(8000 * seconds))
        return path
    buffers = max(1, int(seconds * rate / 1024))
    wave_kind = "sine" if tone else "silence"
    pipeline = Gst.parse_launch(
        f"audiotestsrc num-buffers={buffers} samplesperbuffer=1024 wave={wave_kind} ! "
        f"audio/x-raw,rate={rate},channels=2 ! audioconvert ! {ENCODERS[path.suffix]} ! "
        f"filesink location=\"{path}\"")
    pipeline.set_state(Gst.State.PLAYING)
    message = pipeline.get_bus().timed_pop_filtered(20 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
    pipeline.set_state(Gst.State.NULL)
    if message is None or message.type == Gst.MessageType.ERROR:
        raise RuntimeError(f"Não foi possível gerar {path.name}")
    return path


def png_bytes(color=(200, 30, 30), size=8):
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, size, size)
    pixbuf.fill((color[0] << 24) | (color[1] << 16) | (color[2] << 8) | 0xFF)
    return bytes(pixbuf.save_to_bufferv("png", [], [])[1])
