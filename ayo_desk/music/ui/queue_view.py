"""The play queue: what played, what is playing and what comes next."""
from gi.repository import Adw, Gtk

from .covers import Cover
from .model import duration_text


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
        self.clear = Gtk.Button(label="Limpar próximas", valign=Gtk.Align.CENTER)
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

    def render(self, queue, library):
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
            self.list.append(row)
