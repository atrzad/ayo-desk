"""The play queue: what played, what is playing and what comes next (drag rows to reorder)."""
from gi.repository import Adw, Gdk, GObject, Gtk

from ..queue import SHUFFLE_ALBUMS, SHUFFLE_OFF, SHUFFLE_TRACKS
from .covers import Cover
from .model import duration_text

ORDERS = ((SHUFFLE_OFF, "Na ordem"), (SHUFFLE_TRACKS, "Músicas aleatórias"), (SHUFFLE_ALBUMS, "Álbuns aleatórios"))


class QueueView(Gtk.Box):
    def __init__(self, controller):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.controller = controller
        top = Gtk.Box(spacing=12, margin_top=18, margin_bottom=12, margin_start=24, margin_end=24)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        title = Gtk.Label(label="Fila", xalign=0)
        title.add_css_class("title-1")
        self.summary = Gtk.Label(xalign=0)
        self.summary.add_css_class("dim-label")
        titles.append(title)
        titles.append(self.summary)
        top.append(titles)
        self.syncing = False
        self.order = Gtk.DropDown.new_from_strings([name for _mode, name in ORDERS])
        self.order.set_valign(Gtk.Align.CENTER)
        self.order.set_tooltip_text("Ordem de reprodução")
        self.order.connect("notify::selected", self._order_changed)
        top.append(self.order)
        self.clear = Gtk.Button(icon_name="edit-clear-all-symbolic", tooltip_text="Limpar as próximas músicas",
                                valign=Gtk.Align.CENTER)
        self.clear.connect("clicked", lambda _b: controller.clear_upcoming())
        top.append(self.clear)
        self.append(top)
        self.stack = Gtk.Stack()
        self.stack.add_named(Adw.StatusPage(icon_name="view-list-bullet-symbolic", title="A fila está vazia",
                                            description="Toque um álbum, uma playlist ou uma música para começar."),
                             "empty")
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("boxed-list")
        self.list.connect("row-activated", lambda _l, row: controller.jump(row.position))
        clamp = Adw.Clamp(maximum_size=900, child=self.list, margin_start=12, margin_end=12, margin_bottom=24)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp)
        self.stack.add_named(scroll, "list")
        self.append(self.stack)

    def _order_changed(self, dropdown, _pspec):
        if not self.syncing:
            self.controller.set_shuffle_mode(ORDERS[dropdown.get_selected()][0])

    def _draggable(self, row, position):
        """Rows after the current one can be dragged; any row accepts a drop."""
        if position > self.current:
            source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
            source.connect("prepare", lambda _s, _x, _y: Gdk.ContentProvider.new_for_value(
                GObject.Value(GObject.TYPE_INT, position)))
            source.connect("drag-begin", lambda src, _drag: src.set_icon(Gtk.WidgetPaintable.new(row), 0, 0))
            row.add_controller(source)
        target = Gtk.DropTarget.new(GObject.TYPE_INT, Gdk.DragAction.MOVE)
        target.connect("drop", lambda _t, value, _x, _y: self._dropped(value, position))
        row.add_controller(target)

    def _dropped(self, source, target):
        target = max(target, self.current + 1)  # nothing moves above what is playing
        if source != target:
            self.controller.move_in_queue(source, target)
        return True

    def render(self, queue, library):
        self.syncing = True
        self.order.set_selected(next(n for n, (mode, _name) in enumerate(ORDERS) if mode == queue.shuffle))
        self.syncing = False
        self.current = queue.index
        while child := self.list.get_first_child():
            self.list.remove(child)
        items = queue.items()
        self.stack.set_visible_child_name("list" if items else "empty")
        upcoming = len(items) - queue.index - 1 if queue.index >= 0 else len(items)
        self.summary.set_text(f"{max(0, upcoming)} música" + ("s" if upcoming != 1 else "") + " a seguir"
                              if items else "")
        self.clear.set_sensitive(upcoming > 0)
        # Show a window around the current track: recent history and what is next.
        start = max(0, queue.index - 20)
        for position in range(start, min(len(items), start + 500)):
            path = items[position]
            track = library.get(path)
            row = Adw.ActionRow(activatable=True, use_markup=False)
            row.position = position
            row.set_title(track.title if track else path.rsplit("/", 1)[-1])
            row.set_subtitle(f"{track.display_artist} — {track.display_album}" if track else "")
            row.set_title_lines(1)
            row.set_subtitle_lines(1)
            cover = Cover(40)
            cover.set_key(track.cover if track else "")
            row.add_prefix(cover)
            if track:
                row.add_suffix(Gtk.Label(label=duration_text(track.duration), css_classes=["dim-label", "numeric"]))
            if position == queue.index:
                row.add_css_class("queue-current")
                row.add_suffix(Gtk.Image(icon_name="media-playback-start-symbolic"))
            elif position < queue.index:
                row.add_css_class("queue-played")
            else:
                remove = Gtk.Button(icon_name="list-remove-symbolic", tooltip_text="Tirar da fila",
                                    valign=Gtk.Align.CENTER)
                remove.add_css_class("flat")
                remove.connect("clicked", lambda _b, p=position: self.controller.remove_from_queue(p))
                row.add_suffix(remove)
                handle = Gtk.Image(icon_name="list-drag-handle-symbolic", tooltip_text="Arraste para reordenar")
                handle.add_css_class("dim-label")
                row.add_prefix(handle)
            self._draggable(row, position)
            self.list.append(row)
