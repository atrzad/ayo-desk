"""Local-file GStreamer playback. No recording or playback during startup."""
from pathlib import Path
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst


class Player:
    def __init__(self, changed, error, eos, *, sink=None):
        Gst.init(None)
        self.changed, self.error, self.eos = changed, error, eos
        self.playbin = Gst.ElementFactory.make("playbin", "ayo-player")
        self.available = bool(self.playbin and (sink or Gst.ElementFactory.find("autoaudiosink")))
        self.playing = False
        self.path = None
        self.title = "Nenhuma música selecionada"
        self.artist = "Adicione arquivos à sua biblioteca para começar."
        self.bus = None
        self.handler = None
        if not self.available:
            return
        # Audio only: avoid creating a video window when given a video container.
        self.playbin.set_property("flags", 2 | 16)
        if sink:
            self.playbin.set_property("audio-sink", sink)
        self.bus = self.playbin.get_bus()
        self.bus.add_signal_watch()
        self.handler = self.bus.connect("message", self._message)

    def load(self, path):
        if not self.available:
            raise ValueError("Instale gst-plugins-base e gst-plugins-good para habilitar a reprodução.")
        file = Path(path)
        if not file.is_file():
            raise ValueError("O arquivo não está mais disponível. Remova-o da biblioteca ou reconecte a unidade.")
        self.playbin.set_state(Gst.State.NULL)
        self.path = str(file.resolve())
        self.title, self.artist = file.stem, file.parent.name
        self.playbin.set_property("uri", file.resolve().as_uri())
        result = self.playbin.set_state(Gst.State.PLAYING)
        self.playing = result != Gst.StateChangeReturn.FAILURE
        if not self.playing:
            raise ValueError("Não foi possível reproduzir este arquivo.")
        self.changed()

    def toggle(self):
        if not self.available or not self.path:
            return
        self.playing = not self.playing
        self.playbin.set_state(Gst.State.PLAYING if self.playing else Gst.State.PAUSED)
        self.changed()

    def stop(self):
        if self.playbin:
            self.playbin.set_state(Gst.State.NULL)
        self.playing = False
        self.path = None
        self.title, self.artist = "Nenhuma música selecionada", "Selecione uma faixa da biblioteca."
        self.changed()

    def volume(self, percent):
        if self.playbin:
            self.playbin.set_property("volume", max(0, min(100, percent)) / 100)

    def position(self):
        if not self.available or not self.path:
            return 0, 0
        ok_pos, position = self.playbin.query_position(Gst.Format.TIME)
        ok_dur, duration = self.playbin.query_duration(Gst.Format.TIME)
        return (position / Gst.SECOND if ok_pos else 0, duration / Gst.SECOND if ok_dur else 0)

    def seek(self, seconds):
        if self.available and self.path:
            return self.playbin.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                                            int(seconds * Gst.SECOND))
        return False

    def _message(self, _bus, message):
        if message.type == Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            self.playbin.set_state(Gst.State.NULL)
            self.playing = False
            self.error(str(error))
            self.changed()
        elif message.type == Gst.MessageType.EOS:
            self.playbin.set_state(Gst.State.NULL)
            self.playing = False
            self.eos()
        elif message.type == Gst.MessageType.TAG:
            tags = message.parse_tag()
            for tag, attribute in ((Gst.TAG_TITLE, "title"), (Gst.TAG_ARTIST, "artist")):
                found, value = tags.get_string(tag)
                if found:
                    setattr(self, attribute, value)
            self.changed()

    def close(self):
        if self.playbin:
            self.playbin.set_state(Gst.State.NULL)
        if self.bus:
            self.bus.disconnect(self.handler)
            self.bus.remove_signal_watch()
