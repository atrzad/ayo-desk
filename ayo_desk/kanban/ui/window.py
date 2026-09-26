"""Ayo Kanban: board picker, search, columns and every action on boards, columns and cards."""
import datetime as dt
from pathlib import Path

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

from ... import __version__
from ...core import ROOT
from ...tasks import background
from ...widgets import clear, confirm
from .. import exchange
from ..board import KanbanDB, matches, parse_quick
from .column import ColumnView, EdgeScroll
from .dialogs import ArchiveDialog, board_editor, card_editor, column_editor, show_about, show_shortcuts

BIND = GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE


def plural(count, one, many):
    return f"{count} {one if count == 1 else many}"


def file_name(text):
    return "".join("-" if c in '/\\:*?"<>|' else c for c in text).strip(". ") or "quadro"


class KanbanPage(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.kanban = KanbanDB()
        # Lets the about dialog find the app icon when running from the source tree.
        Gtk.IconTheme.get_for_display(window.get_display()).add_search_path(str(ROOT / "data/icons"))
        self.closed = False
        self.syncing = False
        self.board_ids = []
        self.columns = {}
        self.rows = {}
        self.compact = bool(self.kanban.setting("compact", False))
        saved = self.kanban.setting("board")
        self.board_id = saved if saved in [b["id"] for b in self.kanban.boards()] else self.kanban.ensure_board()

        self._install_actions()
        self._build_header()
        self.search_bar = Gtk.SearchBar(show_close_button=True)
        self.search_entry = Gtk.SearchEntry(hexpand=True,
                                            placeholder_text="Buscar cartões… (#etiqueta, !!! para prioridade alta)")
        self.search_entry.connect("search-changed", lambda _e: self.refresh())
        self.search_bar.set_child(Adw.Clamp(maximum_size=640, child=self.search_entry))
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.bind_property("search-mode-enabled", self.search_button, "active", BIND)
        self.search_bar.connect("notify::search-mode-enabled",
                                lambda bar, _p: None if bar.get_search_mode() else self.search_entry.set_text(""))
        self.append(self.search_bar)
        self.scroller = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.board = Gtk.Box(spacing=14, css_classes=["kanban-board"])
        self.scroller.set_child(self.board)
        EdgeScroll(self.scroller, Gtk.Orientation.HORIZONTAL)
        self.append(self.scroller)
        self.refresh()

    # Setup

    def _install_actions(self):
        self.actions = Gio.SimpleActionGroup()
        simple = {"new-card": lambda: self.new_card(None), "search": self.toggle_search,
                  "new-board": lambda: board_editor(self), "rename-board": self.rename_board,
                  "delete-board": self.delete_board, "new-column": lambda: column_editor(self),
                  "archived": lambda: ArchiveDialog(self).present(self.window), "archive-done": self.archive_done,
                  "export-json": lambda: self.export("json"), "export-all": lambda: self.export("all"),
                  "export-markdown": lambda: self.export("markdown"), "import-json": self.import_json,
                  "shortcuts": lambda: show_shortcuts(self.window),
                  "about": lambda: show_about(self.window, __version__)}
        for name, callback in simple.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, c=callback: self._guard(c))
            self.actions.add_action(action)
        with_id = {"edit-card": self.edit_card, "archive-card": self.archive_card, "delete-card": self.delete_card,
                   "move-top": lambda card: self.move_card(card, None, 0), "new-card-in": self.new_card,
                   "edit-column": lambda column: column_editor(self, self.kanban.column(column)),
                   "column-left": lambda column: self.move_column(column, -1),
                   "column-right": lambda column: self.move_column(column, 1),
                   "archive-column": self.archive_column, "delete-column": self.delete_column}
        for name, callback in with_id.items():
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("x"))
            action.connect("activate", lambda _a, value, c=callback: self._guard(c, value.unpack()))
            self.actions.add_action(action)
        move = Gio.SimpleAction.new("move-to", GLib.VariantType.new("(xx)"))
        move.connect("activate", lambda _a, value: self._guard(self.move_card, *value.unpack()))
        self.actions.add_action(move)
        compact = Gio.SimpleAction.new_stateful("compact", None, GLib.Variant("b", self.compact))
        compact.connect("change-state", self._set_compact)
        self.actions.add_action(compact)
        self.window.insert_action_group("kanban", self.actions)
        app = self.window.get_application()
        if app:
            for action, accels in (("new-card", ["<Primary>n"]), ("search", ["<Primary>f"]),
                                   ("new-board", ["<Primary><Shift>n"]), ("shortcuts", ["<Primary>question"])):
                app.set_accels_for_action(f"kanban.{action}", accels)

    def _build_header(self):
        header = self.window.header
        self.board_list = Gtk.StringList()
        self.board_picker = Gtk.DropDown(model=self.board_list, tooltip_text="Trocar de quadro")
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _f, item: item.set_child(
            Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=26)))
        factory.connect("bind", lambda _f, item: item.get_child().set_label(item.get_item().get_string()))
        self.board_picker.set_factory(factory)
        self.board_picker.connect("notify::selected", self._board_selected)
        header.pack_start(self.board_picker)
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Novo cartão (Ctrl+N)", action_name="kanban.new-card")
        header.pack_start(add)
        self.search_button = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text="Buscar (Ctrl+F)")
        header.pack_start(self.search_button)
        sections = (
            (("Novo quadro…", "new-board"), ("Renomear quadro…", "rename-board"), ("Excluir quadro…", "delete-board")),
            (("Nova coluna…", "new-column"), ("Cartões arquivados", "archived"),
             ("Arquivar cartões concluídos", "archive-done"), ("Cartões compactos", "compact")),
            (("Exportar quadro (JSON)…", "export-json"), ("Exportar todos os quadros (JSON)…", "export-all"),
             ("Exportar como Markdown…", "export-markdown"), ("Importar quadros (JSON)…", "import-json")),
            (("Atalhos de teclado", "shortcuts"), ("Sobre o Ayo Kanban", "about")),
        )
        menu = Gio.Menu()
        for items in sections:
            section = Gio.Menu()
            for text, action in items:
                section.append(text, f"kanban.{action}")
            menu.append_section(None, section)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Menu principal"))

    # Board

    def refresh(self, focus=None):
        if self.closed:
            return
        boards = self.kanban.boards()
        if self.board_id not in [b["id"] for b in boards]:
            self.board_id = boards[0]["id"] if boards else self.kanban.ensure_board()
            boards = self.kanban.boards()
        if self.kanban.setting("board") != self.board_id:
            self.kanban.set_setting("board", self.board_id)
        self._sync_boards(boards)
        columns = self.kanban.columns(self.board_id)
        cards = self.kanban.cards(self.board_id)
        query = self.search_entry.get_text().strip()
        today = dt.date.today()
        offsets = {column_id: view.scroller.get_vadjustment().get_value() for column_id, view in self.columns.items()}
        clear(self.board)
        self.columns = {}
        self.rows = {}
        for column in columns:
            own = [c for c in cards if c["column_id"] == column["id"] and (not query or matches(c, query))]
            view = ColumnView(self, column, own, columns, today, filtering=bool(query))
            self.columns[column["id"]] = view
            self.rows.update((row.card_id, row) for row in view.rows)
            self.board.append(view)
            to_end = focus == ("entry", column["id"])
            if to_end or offsets.get(column["id"]):
                self._restore_scroll(view.scroller, None if to_end else offsets[column["id"]])
        add = Gtk.Button(css_classes=["flat", "kanban-add-column"], valign=Gtk.Align.START,
                         action_name="kanban.new-column", tooltip_text="Nova coluna")
        content = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        content.append(Gtk.Image.new_from_icon_name("list-add-symbolic"))
        content.append(Gtk.Label(label="Nova coluna"))
        add.set_child(content)
        self.board.append(add)
        stats = self.kanban.stats(self.board_id, today)
        name = next(b["name"] for b in boards if b["id"] == self.board_id)
        parts = [name, plural(stats["cards"], "cartão", "cartões"), plural(stats["done"], "concluído", "concluídos")]
        if stats["overdue"]:
            parts.append(plural(stats["overdue"], "atrasado", "atrasados"))
        self.window.header_title.set_subtitle(" · ".join(parts))
        self.actions.lookup_action("archive-done").set_enabled(stats["done"] > 0)
        if focus and focus[0] == "entry" and focus[1] in self.columns:
            self.columns[focus[1]].entry.grab_focus()
        elif focus and focus[0] == "card" and focus[1] in self.rows:
            self.rows[focus[1]].grab_focus()

    def _restore_scroll(self, scroller, value):
        """Apply once the new column has been measured; `None` scrolls to the end."""
        adjustment = scroller.get_vadjustment()

        def apply(adj):
            end = adj.get_upper() - adj.get_page_size()
            adj.set_value(end if value is None else min(value, end))
            adj.disconnect(handler)
        handler = adjustment.connect("changed", apply)

    def _sync_boards(self, boards):
        self.syncing = True
        try:
            self.board_ids = [b["id"] for b in boards]
            names = [b["name"] for b in boards]
            current = [self.board_list.get_string(n) for n in range(self.board_list.get_n_items())]
            if current != names:
                self.board_list.splice(0, self.board_list.get_n_items(), names)
            self.board_picker.set_selected(self.board_ids.index(self.board_id))
        finally:
            self.syncing = False

    def _board_selected(self, picker, _param):
        index = picker.get_selected()
        if self.syncing or index >= len(self.board_ids) or self.board_ids[index] == self.board_id:
            return
        self.board_id = self.board_ids[index]
        self.refresh()

    def rename_board(self):
        board_editor(self, self.kanban.board(self.board_id))

    def delete_board(self):
        board = self.kanban.board(self.board_id)
        total = len(self.kanban.cards(board["id"])) + len(self.kanban.cards(board["id"], archived=True))

        def remove():
            self.kanban.delete_board(board["id"])
            self.refresh()
            self.notify(f"Quadro “{board['name']}” excluído.")
        confirm(self.window, "Excluir quadro?",
                f"“{board['name']}”, suas colunas e {plural(total, 'cartão', 'cartões')} serão apagados deste "
                "computador. Se quiser guardar uma cópia, exporte o quadro antes.", remove, "Excluir quadro", True)

    # Cards

    def new_card(self, column_id):
        columns = self.kanban.columns(self.board_id)
        return card_editor(self, None, column_id or columns[0]["id"])

    def edit_card(self, card_id):
        return card_editor(self, self.kanban.card(card_id))

    def quick_add(self, column_id, entry):
        if not entry.get_text().strip():
            return
        try:
            self.kanban.add_card(column_id, **parse_quick(entry.get_text()))
        except ValueError as exc:
            self.notify(exc)
            return
        self.refresh(focus=("entry", column_id))
        self.warn_limit(column_id)

    def move_card(self, card_id, column_id=None, index=None, before=None):
        card = self.kanban.card(card_id)
        column_id = column_id or card["column_id"]
        if self.kanban.move_card(card_id, column_id, index, before):
            self.refresh(focus=("card", card_id))
            if column_id != card["column_id"]:
                self.warn_limit(column_id)

    def drop_card(self, card_id, column_id, before):
        self._guard(self.move_card, card_id, column_id, None, before)

    def step_card(self, card_id, columns=0, rows=0):
        """Keyboard moves: to the neighbour column at the same height, or one place up/down."""
        card = self.kanban.card(card_id)
        if columns:
            ids = [c["id"] for c in self.kanban.columns(self.board_id)]
            target = ids.index(card["column_id"]) + columns
            if 0 <= target < len(ids):
                self._guard(self.move_card, card_id, ids[target], card["position"])
        elif card["position"] + rows >= 0:
            self._guard(self.move_card, card_id, card["column_id"], card["position"] + rows)

    def archive_card(self, card_id):
        card = self.kanban.card(card_id)
        self.kanban.archive_card(card_id)
        self.refresh()
        self.toast(f"“{card['title']}” arquivado.", lambda: self.kanban.restore_cards([card_id]))

    def delete_card(self, card_id):
        card = self.kanban.card(card_id)

        def remove():
            self.kanban.delete_card(card_id)
            self.refresh()
        confirm(self.window, "Excluir cartão?", f"“{card['title']}” será apagado deste computador. "
                "Para só tirar do quadro, use Arquivar.", remove, "Excluir", True)

    def archive_done(self):
        ids = self.kanban.archive_done(self.board_id)
        self.refresh()
        if ids:
            self.toast(f"{plural(len(ids), 'cartão concluído arquivado', 'cartões concluídos arquivados')}.",
                       lambda: self.kanban.restore_cards(ids))

    def warn_limit(self, column_id):
        column = self.kanban.column(column_id)
        count = len([c for c in self.kanban.cards(column["board"]) if c["column_id"] == column_id])
        if column["wip_limit"] and count > column["wip_limit"]:
            self.notify(f"A coluna “{column['name']}” passou do limite de {column['wip_limit']} cartões.")

    def filter_tag(self, tag):
        self.search_bar.set_search_mode(True)
        self.search_entry.set_text(f"#{tag}")

    # Columns

    def move_column(self, column_id, offset):
        self.kanban.move_column(column_id, offset)
        self.refresh()

    def archive_column(self, column_id):
        ids = [c["id"] for c in self.kanban.cards(self.board_id) if c["column_id"] == column_id]
        for card_id in ids:
            self.kanban.archive_card(card_id)
        self.refresh()
        if ids:
            self.toast(f"{plural(len(ids), 'cartão arquivado', 'cartões arquivados')}.",
                       lambda: self.kanban.restore_cards(ids))

    def delete_column(self, column_id):
        column = self.kanban.column(column_id)
        columns = self.kanban.columns(self.board_id)
        if len(columns) == 1:
            self.notify("O quadro precisa de pelo menos uma coluna.")
            return
        index = [c["id"] for c in columns].index(column_id)
        neighbour = columns[index - 1] if index else columns[1]
        body = f"Os cartões dela vão para “{neighbour['name']}”." if column["count"] else "A coluna está vazia."

        def remove():
            self.kanban.delete_column(column_id)
            self.refresh()
        confirm(self.window, f"Excluir a coluna “{column['name']}”?", body, remove, "Excluir coluna", True)

    # Files

    def export(self, kind):
        board = self.kanban.board(self.board_id)
        if kind == "markdown":
            text, name = exchange.to_markdown(self.kanban, self.board_id), f"{file_name(board['name'])}.md"
        else:
            ids = self.board_ids if kind == "all" else [self.board_id]
            text = exchange.dumps(exchange.export_boards(self.kanban, ids))
            name = "Ayo Kanban.json" if kind == "all" else f"{file_name(board['name'])}.json"
        dialog = Gtk.FileDialog(title="Exportar", initial_name=name)

        def chosen(dialog, result):
            try:
                path = Path(dialog.save_finish(result).get_path())
            except GLib.Error:
                return
            background(lambda: exchange.save(path, text),
                       lambda _v, error: self.notify(f"Não foi possível exportar: {error}" if error
                                                     else f"Exportado para {path.name}."))
        dialog.save(self.window, None, chosen)

    def import_json(self):
        json_filter = Gtk.FileFilter(name="Quadros do Ayo Kanban (JSON)")
        json_filter.add_suffix("json")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(json_filter)
        dialog = Gtk.FileDialog(title="Importar quadros", filters=filters, default_filter=json_filter)

        def chosen(dialog, result):
            try:
                path = Path(dialog.open_finish(result).get_path())
            except GLib.Error:
                return
            background(lambda: exchange.read(path), self.finish_import)
        dialog.open(self.window, None, chosen)

    def finish_import(self, boards, error=None):
        if self.closed:
            return
        if error:
            self.notify(f"Não foi possível importar: {error}")
            return
        ids = self.kanban.insert_boards(boards)
        self.board_id = ids[0]
        self.refresh()
        self.notify(f"{plural(len(ids), 'quadro importado', 'quadros importados')}.")

    # Misc

    def toggle_search(self):
        self.search_bar.set_search_mode(not self.search_bar.get_search_mode())

    def _set_compact(self, action, value):
        action.set_state(value)
        self.compact = value.get_boolean()
        self.kanban.set_setting("compact", self.compact)
        self.refresh()

    def _guard(self, callback, *args):
        """Actions come from menus and shortcuts; a card deleted meanwhile must not crash the window."""
        try:
            return callback(*args)
        except ValueError as exc:
            self.notify(exc)
            self.refresh()

    def notify(self, text):
        self.window.notify(text)

    def toast(self, message, undo):
        if self.window.closed:
            return
        toast = Adw.Toast(title=message, timeout=6, button_label="Desfazer")

        def restore(*_):
            self._guard(undo)
            self.refresh()
        toast.connect("button-clicked", restore)
        self.window.overlay.add_toast(toast)

    def on_show(self):
        self.refresh()

    def close(self):
        if not self.closed:
            self.closed = True
            self.kanban.close()
