"""Spectrum visualizer in the style of CAVA: bars, mirrored bars, wave or dots, monochrome.

Levels come either from the real `cava` (already real-time) or from GStreamer's spectrum
element, whose frames are held back until the audio clock reaches them.
"""
from collections import deque
import math

from gi.repository import Gdk, Graphene, Gsk, Gtk

FLOOR_DB = -70.0
STYLES = (("bars", "Barras"), ("mirror", "Espelhado"), ("wave", "Onda"), ("dots", "Pontos"))


class Visualizer(Gtk.Widget):
    __gtype_name__ = "AyoVisualizer"

    def __init__(self, running_time, height=(72, 96)):
        super().__init__(hexpand=True)
        self.add_css_class("visualizer")
        self.running_time = running_time   # callable → pipeline running time (ns) or None
        self.heights = height
        self.frames = deque(maxlen=200)
        self.levels = []
        self.target = None
        self.style = "bars"
        self.tick = 0
        self.connect("map", lambda _w: self._start())
        self.connect("unmap", lambda _w: self._stop())

    def do_measure(self, orientation, _for_size):
        return (120, 480, -1, -1) if orientation == Gtk.Orientation.HORIZONTAL else (*self.heights, -1, -1)

    def set_style(self, style):
        self.style = style if style in dict(STYLES) else "bars"
        self.queue_draw()

    def push(self, magnitudes, endtime):
        """GStreamer spectrum frame (dB values), shown when the audio clock reaches `endtime`."""
        self.frames.append((endtime, [max(0.0, (v - FLOOR_DB) / -FLOOR_DB) for v in magnitudes]))

    def set_levels(self, levels):
        """Ready-to-show levels (0..1), e.g. from cava."""
        self.target = levels

    def clear(self):
        self.frames.clear()
        self.levels, self.target = [], None
        self.queue_draw()

    def _start(self):
        if not self.tick:
            self.tick = self.add_tick_callback(self._on_tick)

    def _stop(self):
        if self.tick:
            self.remove_tick_callback(self.tick)
            self.tick = 0

    def _on_tick(self, _widget, _clock):
        target, self.target = self.target, None
        if target is None and self.frames:
            now = self.running_time()
            while self.frames and (now is None or self.frames[0][0] is None or self.frames[0][0] <= now):
                target = self.frames.popleft()[1]
        if target is not None:
            if len(self.levels) != len(target):
                self.levels = list(target)
            else:  # fast attack, slow release: bars fall smoothly
                self.levels = [t if t > old else old * 0.8 + t * 0.2 for old, t in zip(self.levels, target)]
        elif self.levels:
            self.levels = [value * 0.92 for value in self.levels]
        self.queue_draw()
        return True

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        if not self.levels or width <= 0:
            return
        base = self.get_color()
        color = Gdk.RGBA()
        color.red, color.green, color.blue, color.alpha = base.red, base.green, base.blue, base.alpha * 0.85
        count = len(self.levels)
        slot = width / count
        bar = max(2.0, slot * 0.62)
        if self.style == "wave":
            builder = Gsk.PathBuilder.new()
            builder.move_to(0, height)
            for n, level in enumerate(self.levels):
                builder.line_to(n * slot + slot / 2, height - max(1.0, level * height))
            builder.line_to(width, height)
            builder.close()
            snapshot.append_fill(builder.to_path(), Gsk.FillRule.WINDING, color)
            return
        for n, level in enumerate(self.levels):
            x = n * slot + (slot - bar) / 2
            size = max(2.0, level * height)
            if self.style == "mirror":
                snapshot.append_color(color, Graphene.Rect().init(x, (height - size) / 2, bar, size))
            elif self.style == "dots":
                dots = max(1, math.ceil(level * height / (bar + 2)))
                for d in range(dots):
                    y = height - (d + 1) * (bar + 2)
                    if y < 0:
                        break
                    snapshot.append_color(color, Graphene.Rect().init(x, y, bar, bar))
            else:
                snapshot.append_color(color, Graphene.Rect().init(x, height - size, bar, size))
