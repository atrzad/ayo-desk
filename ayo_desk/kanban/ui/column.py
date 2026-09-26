"""A board column: header with counter and limit, its cards, quick add and the drop target."""
from gi.repository import Gdk, Gio, GLib, GObject, Graphene, Gtk, Pango

from .card import DRAG_PREFIX, CardRow, menu_item

EDGE = 56
QUICK_ADD_HINT = ("Enter adiciona. Use #etiqueta, ! a !!! para a prioridade "
                  "e @hoje, @amanhã, @sexta ou @25/12 para o prazo.")


class EdgeScroll:
    """Scroll a ScrolledWindow while something is dragged close to its edges; GTK does not do it by itself."""

    def __init__(self, scrolled, orientation):
        self.scrolled = scrolled
        self.horizontal = orientation == Gtk.Orientation.HORIZONTAL
        self.speed = 0.0
        self.tick = 0
        self.motion = Gtk.DropControllerMotion()
        self.motion.connect("motion", self._motion)
        self.motion.connect("leave", self._stop)
        scrolled.add_controller(self.motion)

    def _motion(self, _controller, x, y):
        position, size = (x, self.scrolled.get_width()) if self.horizontal else (y, self.scrolled.get_height())
        if position < EDGE:
            self.speed = -(EDGE - position) / EDGE * 18
        elif position > size - EDGE:
            self.speed = (position - (size - EDGE)) / EDGE * 18
        else:
            self.speed = 0.0
        if self.speed and not self.tick:
            self.tick = self.scrolled.add_tick_callback(self._step)

    def _step(self, *_):
        if self.motion.get_drop() is None:  # the drag ended or was cancelled
            self.speed = 0.0
        if not self.speed:
            self.tick = 0
            return GLib.SOURCE_REMOVE
        adjustment = self.scrolled.get_hadjustment() if self.horizontal else self.scrolled.get_vadjustment()
        adjustment.set_value(adjustment.get_value() + self.speed)
        return GLib.SOURCE_CONTINUE

    def _stop(self, *_):
        self.speed = 0.0


def column_menu(column, first, last):
    column_id = GLib.Variant("x", column["id"])
    main = Gio.Menu()
    main.append_item(menu_item("Novo cartão…", "kanban.new-card-in", column_id))
    main.append_item(menu_item("Editar coluna…", "kanban.edit-column", column_id))
    order = Gio.Menu()
    if not first:
        order.append_item(menu_item("Mover para a esquerda", "kanban.column-left", column_id))
    if not last:
        order.append_item(menu_item("Mover para a direita", "kanban.column-right", column_id))
    end = Gio.Menu()
    end.append_item(menu_item("Arquivar cartões da coluna", "kanban.archive-column", column_id))
    end.append_item(menu_item("Excluir coluna…", "kanban.delete-column", column_id))
    menu = Gio.Menu()
    for section in (main, order, end):
        if section.get_n_items():
            menu.append_section(None, section)
    return menu


class ColumnView(Gtk.Box):
    def __init__(self, page, column, cards, columns, today, filtering=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["kanban-column"],
                         width_request=296)
        self.page = page
        self.column = column
        self.column_id = column["id"]
        self.marked = None

        header = Gtk.Box(spacing=8, margin_start=6, margin_end=2)
        if column["done"]:
            header.append(Gtk.Image(icon_name="object-select-symbolic", tooltip_text="Coluna de concluídos"))
        header.append(Gtk.Label(label=column["name"], xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
                                tooltip_text=column["name"], css_classes=["kanban-column-title"]))
        count, limit = column["count"], column["wip_limit"]
        text = f"{count}/{limit}" if limit else str(count)
        if filtering:
            text = f"{len(cards)} de {text}"
        counter = Gtk.Label(label=text, valign=Gtk.Align.CENTER, css_classes=["kanban-count", "numeric"])
        if limit and count > limit:
            counter.add_css_class("over-limit")
            counter.set_tooltip_text(f"Acima do limite de {limit} cartões")
        elif limit:
            counter.set_tooltip_text(f"Limite de {limit} cartões")
        header.append(counter)
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Novo cartão nesta coluna",
                         css_classes=["flat", "circular"])
        add.connect("clicked", lambda _b: page.new_card(self.column_id))
        header.append(add)
        index = [c["id"] for c in columns].index(self.column_id)
        header.append(Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text="Ações da coluna",
                                     menu_model=column_menu(column, index == 0, index == len(columns) - 1),
                                     css_classes=["flat", "circular"]))
        self.append(header)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["kanban-list"])
        self.list.set_activate_on_single_click(True)
        self.list.connect("row-activated", lambda _l, row: page.edit_card(row.card_id))
        self.list.set_placeholder(Gtk.Label(label="Nada encontrado" if filtering else "Arraste cartões para cá",
                                            css_classes=["dim-label", "kanban-placeholder"]))
        self.rows = []
        for card in cards:
            row = CardRow(page, card, columns, today)
            self.rows.append(row)
            self.list.append(row)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=self.list)
        EdgeScroll(self.scroller, Gtk.Orientation.VERTICAL)
        self.append(self.scroller)

        self.entry = Gtk.Entry(placeholder_text="Adicionar cartão…", tooltip_text=QUICK_ADD_HINT,
                               css_classes=["kanban-quick-add"])
        self.entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "list-add-symbolic")
        self.entry.connect("activate", lambda entry: page.quick_add(self.column_id, entry))
        self.append(self.entry)

        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("motion", self._motion)
        target.connect("leave", lambda *_: self._mark(None))
        target.connect("drop", self._drop)
        self.add_controller(target)

    def _dragged(self, target):
        value = target.get_value()
        if isinstance(value, str) and value.startswith(DRAG_PREFIX):
            return int(value[len(DRAG_PREFIX):])
        return None

    def _before(self, x, y, dragged):
        """The row the card goes above, or None for the end of the column."""
        ok, point = self.compute_point(self.list, Graphene.Point().init(x, y))
        if not ok:
            return None
        for row in self.rows:
            if row.card_id == dragged:
                continue
            ok, bounds = row.compute_bounds(self.list)
            if ok and point.y < bounds.get_y() + bounds.get_height() / 2:
                return row
        return None

    def _motion(self, target, x, y):
        before = self._before(x, y, self._dragged(target))
        self._mark(before if before else ("end" if self.rows else "empty"))
        return Gdk.DragAction.MOVE

    def _mark(self, mark):
        if self.marked == mark:
            return
        if isinstance(self.marked, Gtk.Widget):
            self.marked.remove_css_class("drop-before")
        for style in ("drop-end", "drop-target"):
            self.remove_css_class(style)
        self.marked = mark
        if mark is None:
            return
        self.add_css_class("drop-target")
        if mark == "end":
            self.add_css_class("drop-end")
        elif isinstance(mark, Gtk.Widget):
            mark.add_css_class("drop-before")

    def _drop(self, target, value, x, y):
        self._mark(None)
        if not isinstance(value, str) or not value.startswith(DRAG_PREFIX):
            return False
        card_id = int(value[len(DRAG_PREFIX):])
        before = self._before(x, y, card_id)
        before_id = before.card_id if before else None

        def move():
            self.page.drop_card(card_id, self.column_id, before_id)
            return GLib.SOURCE_REMOVE
        # Rebuilding the board inside the drop handler would destroy the drag source mid-gesture.
        GLib.idle_add(move)
        return True
