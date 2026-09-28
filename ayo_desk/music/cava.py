"""Runs the real `cava` in raw mode and hands its bars (0..1) to the UI, without blocking GTK."""
import os
from pathlib import Path
import shutil

from gi.repository import Gio, GLib

MAX = 1000


def available():
    return shutil.which("cava") is not None


def config_text(bars, framerate=60, smoothing=True, monstercat=True):
    return (f"[general]\nbars = {int(bars)}\nframerate = {int(framerate)}\n"
            "[input]\nmethod = pipewire\nsource = auto\n"
            "[output]\nmethod = raw\nraw_target = /dev/stdout\ndata_format = ascii\n"
            f"ascii_max_range = {MAX}\nbar_delimiter = 59\nframe_delimiter = 10\n"
            f"[smoothing]\nmonstercat = {1 if monstercat else 0}\nnoise_reduction = {77 if smoothing else 20}\n")


def parse(line):
    return [min(1.0, int(value) / MAX) for value in line.strip().split(";") if value.strip().isdigit()]


class Cava:
    """`frame(levels)` is called on the main loop for every frame cava prints."""

    def __init__(self, frame):
        self.frame = frame
        self.process = None
        self.cancellable = None

    @property
    def running(self):
        return self.process is not None

    def start(self, bars=48, smoothing=True, monstercat=True):
        self.stop()
        if not available():
            return False
        folder = Path(os.environ.get("XDG_RUNTIME_DIR") or GLib.get_user_cache_dir()) / "ayo-desk"
        folder.mkdir(parents=True, exist_ok=True)
        config = folder / "cava.conf"
        config.write_text(config_text(bars, smoothing=smoothing, monstercat=monstercat))
        try:
            self.process = Gio.Subprocess.new(["cava", "-p", str(config)],
                                              Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error:
            self.process = None
            return False
        self.cancellable = Gio.Cancellable()
        self.stream = Gio.DataInputStream.new(self.process.get_stdout_pipe())
        self._read()
        return True

    def _read(self):
        if self.process is None:
            return
        self.stream.read_line_async(GLib.PRIORITY_DEFAULT, self.cancellable, self._line, self.process)

    def _line(self, stream, result, process):
        try:
            line, _length = stream.read_line_finish_utf8(result)
        except GLib.Error:
            return
        if process is not self.process or line is None:
            return
        levels = parse(line)
        if levels:
            self.frame(levels)
        self._read()

    def stop(self):
        if self.cancellable is not None:
            self.cancellable.cancel()
        if self.process is not None:
            self.process.force_exit()
        self.process = self.cancellable = None
