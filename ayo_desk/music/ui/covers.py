"""Cover widgets with a small texture cache; full-size images load off the main thread."""
from collections import OrderedDict

import gi
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, GLib, GObject, Graphene, Gsk, Gtk

from .. import covers
from ...tasks import background

_textures = OrderedDict()
CACHE_LIMIT = 400


def texture(key, thumbnail=True):
    if not key:
        return None
    cache_key = (key, thumbnail)
    if cache_key in _textures:
        _textures.move_to_end(cache_key)
        return _textures[cache_key]
    path = covers.path(key, thumbnail)
    if path is None:
        return None
    try:
        result = Gdk.Texture.new_from_filename(str(path))
    except GLib.Error:
        return None
    _textures[cache_key] = result
    while len(_textures) > CACHE_LIMIT:
        _textures.popitem(last=False)
    return result


class Cover(Gtk.Widget):
    """Square cover art at a fixed size (a `size` property, so breakpoints can change it).

    Draws the texture cropped to fill, with rounded corners; shows a symbolic icon when empty.
    """
    __gtype_name__ = "AyoCover"
    size = GObject.Property(type=int, default=64)

    def __init__(self, size, icon="audio-x-generic-symbolic", circular=False):
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.key = None
        self.texture = None
        self.icon = icon
        self.circular = circular
        self.add_css_class("cover")
        self.add_css_class("cover-empty")
        self.set_overflow(Gtk.Overflow.HIDDEN)
        if circular:
            self.add_css_class("circular-cover")
        self.connect("notify::size", lambda *_: self.queue_resize())
        self.set_property("size", size)

    def do_measure(self, _orientation, _for_size):
        size = self.get_property("size")
        return size, size, -1, -1

    def _snapshot_icon(self, snapshot, width, height):
        side = max(16, min(width, height) // 3)
        theme = Gtk.IconTheme.get_for_display(self.get_display())
        icon = theme.lookup_icon(self.icon, None, side, self.get_scale_factor(), self.get_direction(), 0)
        snapshot.save()
        snapshot.translate(Graphene.Point().init((width - side) / 2, (height - side) / 2))
        icon.snapshot_symbolic(snapshot, side, side, [self.get_color()])
        snapshot.restore()

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        if self.texture is None:
            self._snapshot_icon(snapshot, width, height)
            return
        radius = min(width, height) / 2 if self.circular else min(14.0, width * 0.06 + 3)
        bounds = Graphene.Rect().init(0, 0, width, height)
        clip = Gsk.RoundedRect()
        clip.init_from_rect(bounds, radius)
        snapshot.push_rounded_clip(clip)
        tw, th = self.texture.get_width(), self.texture.get_height()
        scale = max(width / tw, height / th)
        dw, dh = tw * scale, th * scale
        snapshot.append_scaled_texture(self.texture, Gsk.ScalingFilter.TRILINEAR,
                                       Graphene.Rect().init((width - dw) / 2, (height - dh) / 2, dw, dh))
        snapshot.pop()

    def _show(self, paintable):
        self.texture = paintable
        if paintable is None:
            self.add_css_class("cover-empty")
        else:
            self.remove_css_class("cover-empty")
        self.queue_draw()

    def set_key(self, key, full=False):
        self.key = key
        self._show(texture(key))
        if full and key:
            cached = _textures.get((key, False))
            if cached:
                self._show(cached)
                return
            path = covers.path(key, thumbnail=False)
            if path is None:
                return

            def loaded(result, error):
                if error or self.key != key or result is None:
                    return
                _textures[(key, False)] = result
                self._show(result)
            background(lambda: Gdk.Texture.new_from_filename(str(path)), loaded)
