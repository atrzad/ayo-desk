"""A card on the board: summary, drag source, context menu and keyboard moves."""
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from ..board import PRIORITIES, due_label

DRAG_PREFIX = "ayo-kanban-card:"
PRIORITY_STYLES = {1: "soft", 2: "outline", 3: "strong"}
DUE_STYLES = {"overdue": "strong", "today": "outline", "soon": "soft", "later": "soft", "done": "soft"}


def menu_item(text, action, value):
    item = Gio.MenuItem.new(text, None)
    item.set_action_and_target_value(action, value)
    return item


def badge(text, style, icon=None):
    box = Gtk.Box(spacing=4, css_classes=["kanban-badge", style], valign=Gtk.Align.CENTER)
    if icon:
        box.append(Gtk.Image.new_from_icon_name(icon))
    box.append(Gtk.Label(label=text))
    return box


def card_menu(card, columns):
    card_id = GLib.Variant("x", card["id"])
    main = Gio.Menu()
    main.append_item(menu_item("Editar…", "kanban.edit-card", card_id))
    move = Gio.Menu()
    for column in columns:
        if column["id"] != card["column_id"]:
            move.append_item(menu_item(column["name"], "kanban.move-to",
                                       GLib.Variant("(xx)", (card["id"], column["id"]))))
    if move.get_n_items():
        main.append_submenu("Mover para", move)
    main.append_item(menu_item("Mover para o topo", "kanban.move-top", card_id))
    end = Gio.Menu()
    end.append_item(menu_item("Arquivar", "kanban.archive-card", card_id))
    end.append_item(menu_item("Excluir…", "kanban.delete-card", card_id))
    menu = Gio.Menu()
    menu.append_section(None, main)
    menu.append_section(None, end)
    return menu


class CardRow(Gtk.ListBoxRow):
    def __init__(self, page, card, columns, today):
        super().__init__(activatable=True)
        self.page = page
        self.card = card
        self.card_id = card["id"]
        self.hot_spot = (0, 0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      css_classes=["kanban-card", f"priority-{card['priority']}"])
        if card["column_done"]:
            box.add_css_class("kanban-done")
        top = Gtk.Box(spacing=6)
        title = Gtk.Label(label=card["title"], xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                          hexpand=True, css_classes=["kanban-card-title"])
        top.append(title)
        self.menu_button = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=card_menu(card, columns),
                                          valign=Gtk.Align.START, tooltip_text="Ações do cartão",
                                          css_classes=["flat", "circular", "kanban-card-menu"])
        top.append(self.menu_button)
        box.append(top)
        if card["notes"] and not page.compact:
            box.append(Gtk.Label(label=" ".join(card["notes"].split()), xalign=0, wrap=True, lines=2,
                                 ellipsize=Pango.EllipsizeMode.END, css_classes=["dim-label", "kanban-card-notes"]))
        if card["tags"] and not page.compact:
            tags = Adw.WrapBox(child_spacing=4, line_spacing=4)
            for tag in card["tags"]:
                chip = Gtk.Button(label=f"#{tag}", tooltip_text=f"Mostrar só #{tag}", css_classes=["flat", "kanban-tag"])
                chip.connect("clicked", lambda _b, t=tag: page.filter_tag(t))
                tags.append(chip)
            box.append(tags)
        footer = Gtk.Box(spacing=6)
        if card["priority"]:
            footer.append(badge(PRIORITIES[card["priority"]], PRIORITY_STYLES[card["priority"]]))
        due, state = due_label(card["due"], today, bool(card["column_done"]))
        if due:
            footer.append(badge(due, DUE_STYLES[state], "alarm-symbolic"))
        if card["checklist_total"]:
            complete = card["checklist_done"] == card["checklist_total"]
            footer.append(badge(f"{card['checklist_done']}/{card['checklist_total']}", "outline" if complete else "soft",
                                "checkbox-checked-symbolic"))
        if footer.get_first_child():
            box.append(footer)
        self.set_child(box)
        self.box = box
        summary = [card["title"], f"prioridade {PRIORITIES[card['priority']].lower()}" if card["priority"] else "",
                   f"prazo {due}" if due else "", " ".join(f"#{t}" for t in card["tags"])]
        self.update_property([Gtk.AccessibleProperty.LABEL], [", ".join(s for s in summary if s)])

        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect("prepare", self._prepare)
        source.connect("drag-begin", self._drag_begin)
        source.connect("drag-end", lambda *_: self.box.remove_css_class("dragging"))
        self.add_controller(source)
        secondary = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        secondary.connect("pressed", lambda *_: self.menu_button.popup())
        self.add_controller(secondary)
        keys = Gtk.ShortcutController()
        for accel, callback in (("<Alt>Left", lambda: page.step_card(self.card_id, columns=-1)),
                                ("<Alt>Right", lambda: page.step_card(self.card_id, columns=1)),
                                ("<Alt>Up", lambda: page.step_card(self.card_id, rows=-1)),
                                ("<Alt>Down", lambda: page.step_card(self.card_id, rows=1)),
                                ("Delete", lambda: page.archive_card(self.card_id)),
                                ("Menu|<Shift>F10", self.menu_button.popup)):
            keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(accel),
                                               Gtk.CallbackAction.new(lambda *_a, c=callback: c() or True)))
        self.add_controller(keys)

    def _prepare(self, _source, x, y):
        self.hot_spot = (int(x), int(y))
        return Gdk.ContentProvider.new_for_value(f"{DRAG_PREFIX}{self.card_id}")

    def _drag_begin(self, source, _drag):
        # A still image: the row itself fades while it is being dragged.
        source.set_icon(Gtk.WidgetPaintable.new(self.box).get_current_image(), *self.hot_spot)
        self.box.add_css_class("dragging")
