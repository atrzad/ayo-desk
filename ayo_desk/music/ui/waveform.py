"""Seek bar drawn as the track's waveform (a plain line until it is measured)."""
from gi.repository import Gdk, GObject, Graphene, Gtk

from ..analysis import resample
from .model import duration_text

BAR, GAP = 2, 1


class Waveform(Gtk.Widget):
    __gtype_name__ = "AyoWaveform"
    __gsignals__ = {"seek": (GObject.SignalFlags.RUN_FIRST, None, (float,))}

    def __init__(self):
        super().__init__(hexpand=True, focusable=False)
        self.add_css_class("waveform")
        self.levels = []
        self.fraction = 0.0
        self.duration = 0.0
        self.hover = None
        self.dragging = False
        self._cache = (0, [])
        click = Gtk.GestureDrag()
        click.connect("drag-begin", self._drag_begin)
        click.connect("drag-update", self._drag_update)
        click.connect("drag-end", self._drag_end)
        self.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, _y: self._hover(x))
        motion.connect("leave", lambda _c: self._hover(None))
        self.add_controller(motion)
        self.set_cursor(Gdk.Cursor.new_from_name("pointer"))

    def do_measure(self, orientation, _for_size):
        return (60, 240, -1, -1) if orientation == Gtk.Orientation.HORIZONTAL else (28, 28, -1, -1)

    def set_levels(self, levels):
        self.levels = list(levels or [])
        self._cache = (0, [])
        self.queue_draw()

    def set_position(self, position, duration):
        self.duration = duration
        if not self.dragging:
            self.fraction = position / duration if duration > 0 else 0.0
            self.queue_draw()

    def _x_to_fraction(self, x):
        width = self.get_width()
        return min(1.0, max(0.0, x / width)) if width else 0.0

    def _drag_begin(self, gesture, x, _y):
        if self.duration <= 0:
            return
        self.dragging = True
        self.start_x = x
        self.fraction = self._x_to_fraction(x)
        self.queue_draw()

    def _drag_update(self, _gesture, dx, _dy):
        if self.dragging:
            self.fraction = self._x_to_fraction(self.start_x + dx)
            self._hover(self.start_x + dx)

    def _drag_end(self, _gesture, dx, _dy):
        if not self.dragging:
            return
        self.dragging = False
        self.fraction = self._x_to_fraction(self.start_x + dx)
        self.emit("seek", self.fraction * self.duration)
        self.queue_draw()

    def _hover(self, x):
        self.hover = x
        if x is not None and self.duration > 0:
            self.set_tooltip_text(duration_text(self._x_to_fraction(x) * self.duration))
        else:
            self.set_tooltip_text(None)
        self.queue_draw()

    def _bars(self, count):
        if self._cache[0] != count:
            self._cache = (count, resample(self.levels, count) if self.levels else [])
        return self._cache[1]

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        if width <= 0:
            return
        color = self.get_color()
        played = Gdk.RGBA()
        played.red, played.green, played.blue, played.alpha = color.red, color.green, color.blue, color.alpha
        rest = played.copy()
        rest.alpha = color.alpha * (0.45 if self.hover is not None else 0.3)
        cut = self.fraction * width
        count = max(1, int(width // (BAR + GAP)))
        bars = self._bars(count)
        if not bars:
            line = 4
            top = (height - line) / 2
            snapshot.append_color(rest, Graphene.Rect().init(0, top, width, line))
            snapshot.append_color(played, Graphene.Rect().init(0, top, cut, line))
            return
        middle = height / 2
        for number, level in enumerate(bars):
            x = number * (BAR + GAP)
            bar = max(2.0, level * (height - 2))
            snapshot.append_color(played if x + BAR <= cut else rest,
                                  Graphene.Rect().init(x, middle - bar / 2, BAR, bar))
