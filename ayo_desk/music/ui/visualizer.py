"""Monochrome spectrum bars, kept in step with what is heard (not with what was decoded)."""
from collections import deque

from gi.repository import Gdk, Graphene, Gtk

FLOOR_DB = -70.0


class Visualizer(Gtk.Widget):
    __gtype_name__ = "AyoVisualizer"

    def __init__(self, running_time):
        super().__init__(hexpand=True)
        self.add_css_class("visualizer")
        self.running_time = running_time   # callable → pipeline running time (ns) or None
        self.frames = deque(maxlen=200)
        self.levels = []
        self.tick = 0
        self.connect("map", lambda _w: self._start())
        self.connect("unmap", lambda _w: self._stop())

    def do_measure(self, orientation, _for_size):
        return (120, 480, -1, -1) if orientation == Gtk.Orientation.HORIZONTAL else (72, 96, -1, -1)

    def push(self, magnitudes, endtime):
        self.frames.append((endtime, magnitudes))

    def clear(self):
        self.frames.clear()
        self.levels = []
        self.queue_draw()

    def _start(self):
        if not self.tick:
            self.tick = self.add_tick_callback(self._on_tick)

    def _stop(self):
        if self.tick:
            self.remove_tick_callback(self.tick)
            self.tick = 0

    def _on_tick(self, _widget, _clock):
        now = self.running_time()
        current = None
        while self.frames and (now is None or self.frames[0][0] is None or self.frames[0][0] <= now):
            current = self.frames.popleft()[1]
        if current is not None:
            target = [max(0.0, (value - FLOOR_DB) / -FLOOR_DB) for value in current]
            if len(self.levels) != len(target):
                self.levels = target
            else:  # fast attack, slow release: bars fall smoothly
                self.levels = [t if t > old else old * 0.82 + t * 0.18 for old, t in zip(self.levels, target)]
        elif self.levels:
            self.levels = [value * 0.9 for value in self.levels]
        self.queue_draw()
        return True

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        if not self.levels or width <= 0:
            return
        color = self.get_color()
        fill = Gdk.RGBA()
        fill.red, fill.green, fill.blue, fill.alpha = color.red, color.green, color.blue, color.alpha * 0.85
        count = len(self.levels)
        slot = width / count
        bar = max(2.0, slot * 0.6)
        for number, level in enumerate(self.levels):
            size = max(2.0, level * height)
            snapshot.append_color(fill, Graphene.Rect().init(number * slot + (slot - bar) / 2, height - size,
                                                             bar, size))
