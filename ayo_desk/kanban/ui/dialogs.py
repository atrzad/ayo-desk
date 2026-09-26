"""Editors for cards, columns and boards, the archive and the about/shortcut windows."""
import datetime as dt

from gi.repository import Adw, GLib, Gtk

from ...widgets import Form, button, clear, confirm, group, label, row
from ..board import MAX_WIP, PRIORITIES

WEEKDAY_NAMES = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
SHORTCUTS = (
    ("Quadro", (("Novo cartão", "<Primary>n"), ("Buscar (#etiqueta, !!! prioridade)", "<Primary>f"),
                ("Novo quadro", "<Primary><Shift>n"), ("Fechar", "<Primary>q"))),
    ("Cartão selecionado", (("Abrir o cartão", "Return"), ("Mover para a coluna ao lado", "<Alt>Left <Alt>Right"),
                            ("Subir ou descer na coluna", "<Alt>Up <Alt>Down"), ("Arquivar", "Delete"),
                            ("Menu do cartão", "Menu"))),
)


def field(title, widget):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.append(label(title))
    box.append(widget)
    return box


def date_text(iso):
    date = dt.date.fromisoformat(iso)
    return f"{WEEKDAY_NAMES[date.weekday()]}, {date:%d/%m/%Y}"


class DuePicker(Gtk.Box):
    """Deadline chosen on a calendar popover; empty means no deadline."""

    def __init__(self, value=None):
        super().__init__(spacing=6)
        self.value = value
        self.calendar = Gtk.Calendar()
        if value:
            date = dt.date.fromisoformat(value)
            when = GLib.DateTime.new_local(date.year, date.month, date.day, 12, 0, 0)
            if hasattr(self.calendar, "set_date"):
                self.calendar.set_date(when)
            else:
                self.calendar.select_day(when)
        self.calendar.connect("day-selected", self._picked)
        shortcuts = Gtk.Box(spacing=6, homogeneous=True)
        for text, days in (("Hoje", 0), ("Amanhã", 1), ("Em 1 semana", 7)):
            shortcuts.append(button(text, lambda d=days: self.set_value(dt.date.today() + dt.timedelta(days=d))))
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.append(self.calendar)
        content.append(shortcuts)
        self.popover = Gtk.Popover(child=content)
        self.button = Gtk.MenuButton(popover=self.popover, hexpand=True, always_show_arrow=True)
        self.clear_button = button("Remover prazo", lambda: self.set_value(None), icon="edit-clear-symbolic")
        self.append(self.button)
        self.append(self.clear_button)
        self._show()

    def set_value(self, date):
        self.value = date.isoformat() if date else None
        self.popover.popdown()
        self._show()

    def _picked(self, calendar):
        self.value = calendar.get_date().format("%Y-%m-%d")
        self._show()

    def _show(self):
        self.button.set_label(date_text(self.value) if self.value else "Sem prazo")
        self.clear_button.set_sensitive(bool(self.value))


class ChecklistEditor(Gtk.Box):
    def __init__(self, items):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, max_content_height=220,
                                         propagate_natural_height=True, child=self.list)
        self.append(self.scroll)
        self.entry = Gtk.Entry(placeholder_text="Novo item — Enter para incluir")
        self.entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "list-add-symbolic")
        self.entry.connect("activate", self._add_from_entry)
        self.append(self.entry)
        for item in items:
            self.add_item(item["text"], bool(item["done"]))
        self._update()

    def _add_from_entry(self, entry):
        if entry.get_text().strip():
            self.add_item(entry.get_text())
            entry.set_text("")

    def add_item(self, text, done=False):
        item = Gtk.ListBoxRow(activatable=False)
        box = Gtk.Box(spacing=8, margin_start=10, margin_end=6, margin_top=4, margin_bottom=4)
        item.check = Gtk.CheckButton(active=done, tooltip_text="Feito")
        item.check.connect("toggled", lambda _c: self._update())
        item.text = Gtk.Entry(text=text, hexpand=True, has_frame=False)
        box.append(item.check)
        box.append(item.text)
        box.append(button("Remover item", lambda: (self.list.remove(item), self._update()),
                          "flat", icon="list-remove-symbolic"))
        item.set_child(box)
        self.list.append(item)
        self._update()

    def rows(self):
        child, rows = self.list.get_first_child(), []
        while child:
            rows.append(child)
            child = child.get_next_sibling()
        return rows

    def items(self):
        return [(item.text.get_text(), item.check.get_active()) for item in self.rows()]

    def _update(self):
        self.scroll.set_visible(bool(self.rows()))


def card_editor(page, card=None, column_id=None):
    kanban = page.kanban
    columns = kanban.columns(page.board_id)
    ids = [c["id"] for c in columns]
    form = Form(page.window, "Editar cartão" if card else "Novo cartão")
    form.set_default_size(540, -1)
    title = form.entry("Título", card["title"] if card else "")
    column = Gtk.DropDown.new_from_strings([c["name"] for c in columns])
    current = card["column_id"] if card else column_id
    column.set_selected(ids.index(current) if current in ids else 0)
    priority = Adw.ToggleGroup(homogeneous=True)
    for number, name in enumerate(PRIORITIES):
        priority.add(Adw.Toggle(label=name, name=str(number)))
    priority.set_active_name(str(card["priority"] if card else 0))
    placement = Gtk.Box(spacing=12, homogeneous=True)
    placement.append(field("Coluna", column))
    placement.append(field("Prazo", due := DuePicker(card["due"] if card else None)))
    form.add(placement)
    form.add(field("Prioridade", priority))
    tags = form.entry("Etiquetas (separadas por espaço ou vírgula)", " ".join(card["tags"]) if card else "")
    notes = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
    notes.get_buffer().set_text(card["notes"] if card else "")
    scroll = Gtk.ScrolledWindow(min_content_height=110, max_content_height=240, propagate_natural_height=True,
                                child=notes, css_classes=["card"])
    form.add(field("Notas", scroll))
    checklist = ChecklistEditor(kanban.checklist(card["id"]) if card else [])
    form.add(field("Lista de tarefas", checklist))
    if card:
        created = dt.datetime.fromisoformat(card["created"]).strftime("%d/%m/%Y %H:%M")
        form.add(label(f"Criado em {created}.", "dim-label"))

    def save():
        buffer = notes.get_buffer()
        values = {"title": title.get_text(), "priority": int(priority.get_active_name()), "due": due.value,
                  "notes": buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False),
                  "tags": tags.get_text(), "checklist": checklist.items()}
        target = ids[column.get_selected()]
        if card:
            kanban.update_card(card["id"], **values)
            if target != card["column_id"]:
                kanban.move_card(card["id"], target)
            card_id = card["id"]
        else:
            card_id = kanban.add_card(target, **values)
        page.refresh(focus=("card", card_id))
        page.warn_limit(target)
    form.callback = save
    form.present()
    title.grab_focus()
    return form


def column_editor(page, column=None):
    form = Form(page.window, "Editar coluna" if column else "Nova coluna")
    name = form.entry("Nome", column["name"] if column else "")
    limit = Gtk.SpinButton.new_with_range(0, MAX_WIP, 1)
    limit.set_value(column["wip_limit"] if column else 0)
    form.add(field("Limite de cartões (0 = sem limite)", limit))
    done_row = Gtk.Box(spacing=12)
    texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
    texts.append(label("Coluna de concluídos"))
    texts.append(label("Cartões aqui contam como feitos, ficam riscados e não aparecem como atrasados.", "dim-label"))
    done_row.append(texts)
    done = Gtk.Switch(active=bool(column["done"]) if column else False, valign=Gtk.Align.CENTER)
    done_row.append(done)
    form.add(done_row)

    def save():
        limit.update()
        if column:
            page.kanban.update_column(column["id"], name.get_text(), limit.get_value_as_int(), done.get_active())
        else:
            page.kanban.add_column(page.board_id, name.get_text(), limit.get_value_as_int(), done.get_active())
        page.refresh()
    form.callback = save
    form.present()
    return form


def board_editor(page, board=None):
    form = Form(page.window, "Renomear quadro" if board else "Novo quadro")
    name = form.entry("Nome do quadro", board["name"] if board else "")
    if not board:
        form.add(label("Começa com as colunas A fazer, Fazendo e Feito. Dá para mudar depois.", "dim-label"))

    def save():
        if board:
            page.kanban.rename_board(board["id"], name.get_text())
        else:
            page.board_id = page.kanban.create_board(name.get_text())
        page.refresh()
    form.callback = save
    form.present()
    return form


class ArchiveDialog(Adw.Dialog):
    def __init__(self, page):
        super().__init__(title="Cartões arquivados", content_width=520, content_height=560)
        self.page = page
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.empty_button = button("Excluir todos", self._empty)
        header.pack_start(self.empty_button)
        view.add_top_bar(header)
        self.stack = Gtk.Stack()
        self.status = Adw.StatusPage(icon_name="folder-symbolic", title="Nada arquivado",
                                     description="Cartões arquivados saem do quadro sem ser apagados e podem voltar "
                                                 "a qualquer momento.")
        self.stack.add_named(self.status, "empty")
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=12, margin_bottom=24,
                           margin_start=18, margin_end=18)
        self.stack.add_named(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                                child=Adw.Clamp(maximum_size=560, child=self.box)), "list")
        view.set_content(self.stack)
        self.set_child(view)
        self.refresh()

    def refresh(self):
        cards = self.page.kanban.cards(self.page.board_id, archived=True)
        clear(self.box)
        self.stack.set_visible_child_name("list" if cards else "empty")
        self.empty_button.set_sensitive(bool(cards))
        section = group(self.box, f"{len(cards)} {'cartão' if len(cards) == 1 else 'cartões'}")
        for card in cards:
            item = row(card["title"], f"Coluna {card['column_name']}" + (f" · {' '.join('#' + t for t in card['tags'])}"
                                                                          if card["tags"] else ""))
            item.add_suffix(button("Restaurar", lambda c=card: self._restore(c), "flat", icon="edit-undo-symbolic"))
            item.add_suffix(button("Excluir", lambda c=card: self._delete(c), "flat", icon="user-trash-symbolic"))
            section.add(item)

    def _restore(self, card):
        self.page.kanban.archive_card(card["id"], archived=False)
        self.page.refresh()
        self.refresh()

    def _delete(self, card):
        def remove():
            self.page.kanban.delete_card(card["id"])
            self.refresh()
        confirm(self, "Excluir cartão?", f"“{card['title']}” será apagado deste computador.", remove, "Excluir", True)

    def _empty(self):
        def remove():
            removed = self.page.kanban.empty_archive(self.page.board_id)
            self.page.notify(f"{removed} {'cartão excluído' if removed == 1 else 'cartões excluídos'}.")
            self.refresh()
        confirm(self, "Excluir todos os arquivados?", "Os cartões arquivados deste quadro serão apagados deste "
                "computador. Não dá para desfazer.", remove, "Excluir todos", True)


def show_about(parent, version):
    about = Adw.AboutDialog(application_name="Ayo Kanban", application_icon="io.github.ayodesk.Desk.kanban",
                            version=version, developer_name="Ayo", license_type=Gtk.License.MIT_X11,
                            comments="Quadro kanban local para Arch Linux, feito em Python e GTK4. "
                                     "Seus cartões ficam só neste computador.",
                            website="https://github.com/atrzad/ayo-desk")
    about.present(parent)
    return about


def show_shortcuts(parent):
    if not hasattr(Adw, "ShortcutsDialog"):
        return None
    dialog = Adw.ShortcutsDialog()
    for title, items in SHORTCUTS:
        section = Adw.ShortcutsSection(title=title)
        for text, accelerator in items:
            section.add(Adw.ShortcutsItem(title=text, accelerator=accelerator))
        dialog.add(section)
    dialog.present(parent)
    return dialog
