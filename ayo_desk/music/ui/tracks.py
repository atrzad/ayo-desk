"""Sortable track table (Gtk.ColumnView) shared by every view that lists songs."""
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from .. import tags
from .model import Track, duration_text, track_order

STARS = "★★★★★"


def stars(rating):
    return STARS[:rating] + "☆" * (5 - rating) if rating else ""


def _key(values):
    def compare(a, b, _data=None):
        ka, kb = values(a), values(b)
        return Gtk.Ordering.SMALLER if ka < kb else Gtk.Ordering.LARGER if ka > kb else Gtk.Ordering.EQUAL
    return Gtk.CustomSorter.new(compare, None)


# name: (title, fixed width or None for expand, text, sort key, extra css)
COLUMNS = {
    "number": ("#", 52, lambda t: str(t.track_no or ""), lambda t: track_order(t), "numeric"),
    "title": ("Título", None, lambda t: t.title, lambda t: (tags.fold(t.title), t.path), None),
    "artist": ("Artista", None, lambda t: t.display_artist,
               lambda t: (tags.fold(t.display_artist), t.album_key or (), track_order(t)), None),
    "album": ("Álbum", None, lambda t: t.display_album,
              lambda t: (tags.fold(t.display_album), t.album_key or (), track_order(t)), None),
    "year": ("Ano", 64, lambda t: str(t.year or ""), lambda t: (t.year or 0, t.album_key or (), track_order(t)), "numeric"),
    "duration": ("Duração", 78, lambda t: duration_text(t.duration), lambda t: t.duration, "numeric"),
    "plays": ("Plays", 64, lambda t: str(t.plays or ""), lambda t: (t.plays, t.last_played or ""), "numeric"),
    "rating": ("Nota", 92, lambda t: stars(t.rating), lambda t: (t.rating, t.favorite), "rating"),
}
LIBRARY_COLUMNS = ("title", "artist", "album", "year", "duration", "plays", "rating")
# Secondary columns disappear as the table gets narrower (tiled windows, phone-sized widths).
HIDE_BELOW = ((720, ("year", "plays")), (560, ("album", "rating")), (400, ("artist",)))
ALBUM_COLUMNS = ("number", "title", "artist", "duration", "plays", "rating")


class TrackTable(Gtk.Box):
    """`model` is any Gio.ListModel of Track. With scroll=False it grows to fit (album pages)."""

    def __init__(self, controller, model=None, columns=LIBRARY_COLUMNS, scroll=True, sortable=True):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=scroll)
        self.controller = controller
        self.base = model if model is not None else Gio.ListStore(item_type=Track)
        self.filter = Gtk.CustomFilter.new(self._matches, None)
        self.terms = []
        self.filtered = Gtk.FilterListModel(model=self.base, filter=self.filter)
        self.view = Gtk.ColumnView(show_row_separators=False, show_column_separators=False,
                                   reorderable=False, tab_behavior=Gtk.ListTabBehavior.ITEM)
        self.view.add_css_class("track-table")
        self.sorted = Gtk.SortListModel(model=self.filtered, sorter=self.view.get_sorter() if sortable else None)
        self.selection = Gtk.MultiSelection(model=self.sorted)
        self.view.set_model(self.selection)
        self.view.connect("activate", lambda _view, position: self.play_from(position))
        self.columns = {}
        self.hidden = set()
        for name in columns:
            self.columns[name] = self._add_column(name, sortable)
        # EXTERNAL keeps the table from forcing a minimum width, so narrow windows can hide columns.
        window = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                    vscrollbar_policy=Gtk.PolicyType.AUTOMATIC if scroll else Gtk.PolicyType.NEVER,
                                    vexpand=scroll, propagate_natural_height=not scroll)
        window.set_child(self.view)
        window.get_hadjustment().connect("notify::page-size", self._width_changed)
        self.append(window)
        controller.register_table(self)

    def _width_changed(self, adjustment, _pspec):
        width = adjustment.get_page_size()
        hidden = {name for limit, names in HIDE_BELOW if 0 < width <= limit for name in names}
        if hidden != self.hidden:
            self.hidden = hidden
            GLib.idle_add(self._apply_hidden)

    def _apply_hidden(self):
        for name, column in self.columns.items():
            column.set_visible(name not in self.hidden)
        return GLib.SOURCE_REMOVE

    def _add_column(self, name, sortable):
        title, width, text, sort_key, css = COLUMNS[name]
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup, name, css)
        factory.connect("bind", self._bind, name, text)
        column = Gtk.ColumnViewColumn(title=title, factory=factory, resizable=width is None)
        if width:
            column.set_fixed_width(width)
        else:
            column.set_expand(True)
        if sortable:
            column.set_sorter(_key(sort_key))
        self.view.append_column(column)
        return column

    def _setup(self, _factory, cell, name, css):
        box = Gtk.Box(spacing=8)
        if name == "title":
            icon = Gtk.Image(icon_name="media-playback-start-symbolic", pixel_size=12, visible=False)
            icon.add_css_class("now-playing-icon")
            box.append(icon)
            box.icon = icon
        label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True, single_line_mode=True)
        if css:
            label.add_css_class(css)
        if name in ("number", "duration", "plays", "year"):
            label.set_xalign(1 if name != "number" else 0.5)
        box.append(label)
        box.label = label
        box.cell = cell
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", self._context_menu, box)
        box.add_controller(click)
        press = Gtk.GestureLongPress()
        press.connect("pressed", lambda gesture, x, y, widget=box: self._context_menu(gesture, 1, x, y, widget))
        box.add_controller(press)
        cell.set_child(box)

    def _bind(self, _factory, cell, name, text):
        box, track = cell.get_child(), cell.get_item()
        box.label.set_text(text(track) or "")
        playing = track.path == self.controller.current_path
        if name == "title":
            box.icon.set_visible(playing)
            box.set_tooltip_text(track.path)
        if playing:
            box.label.add_css_class("heading")
        else:
            box.label.remove_css_class("heading")

    def _context_menu(self, _gesture, _presses, x, y, widget):
        track = widget.cell.get_item()
        if track is None:
            return
        position = widget.cell.get_position()
        if not self.selection.is_selected(position):
            self.selection.select_item(position, True)
        self.controller.show_track_menu(self.selected_tracks() or [track], widget, x, y)

    def selected_tracks(self):
        bitset = self.selection.get_selection()
        return [self.sorted.get_item(bitset.get_nth(n)) for n in range(bitset.get_size())]

    def paths(self):
        return [self.sorted.get_item(n).path for n in range(self.sorted.get_n_items())]

    def play_from(self, position=0, shuffle=False):
        paths = self.paths()
        if paths:
            self.controller.play_paths(paths, position, shuffle=shuffle)

    def set_tracks(self, tracks):
        if isinstance(self.base, Gio.ListStore):
            self.base.splice(0, self.base.get_n_items(), list(tracks))

    def set_search(self, text):
        self.terms = tags.fold(text).split()
        self.filter.changed(Gtk.FilterChange.DIFFERENT)

    def _matches(self, track, _data=None):
        return all(term in (track.search or "") for term in self.terms)

    def refresh(self, paths):
        """Redraw rows for these paths (playing marker, rating) without rebuilding the list."""
        paths = set(p for p in paths if p)
        if not paths:
            return
        for position in range(self.base.get_n_items()):
            if self.base.get_item(position).path in paths:
                self.base.items_changed(position, 1, 1)

    def count(self):
        return self.sorted.get_n_items()

    def grab_focus_first(self):
        GLib.idle_add(lambda: self.view.grab_focus() and False)
