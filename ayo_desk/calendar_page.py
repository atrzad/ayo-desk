import datetime as dt
from gi.repository import GLib, Gtk
from .widgets import Page, Form, button, clear, confirm, group, label, row


class CalendarPage(Page):
    def __init__(self, window):
        super().__init__(window, "Calendário", "Seus compromissos, guardados neste computador.")
        self.toolbar.append(button("Novo evento", self.edit, "suggested-action"))
        self.calendar = Gtk.Calendar(show_week_numbers=True, halign=Gtk.Align.CENTER)
        self.calendar.add_css_class("desk-calendar")
        self.calendar.connect("day-selected", lambda _: self.refresh())
        for signal in ("next-month", "prev-month", "next-year", "prev-year"):
            self.calendar.connect(signal, lambda _: self.refresh())
        self.body.append(self.calendar)
        today = button("Voltar para hoje", lambda: self.calendar.select_day(GLib.DateTime.new_now_local()))
        today.set_halign(Gtk.Align.CENTER)
        self.body.append(today)
        self.agenda = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.body.append(self.agenda)
        self.refresh()

    def selected_day(self):
        return self.calendar.get_date().format("%Y-%m-%d")

    def refresh(self):
        if not hasattr(self, "agenda"):
            return
        date = self.calendar.get_date()
        self.calendar.clear_marks()
        for day in self.window.store.event_days(date.get_year(), date.get_month()):
            self.calendar.mark_day(day)
        clear(self.agenda)
        section = group(self.agenda, date.format("%d/%m/%Y"))
        events = self.window.store.events(self.selected_day())
        for event in events:
            item = row(event["title"], (event["time"] or "Dia inteiro") + (f"  •  {event['notes']}" if event["notes"] else ""),
                       "appointment-soon-symbolic")
            item.add_suffix(button("Editar", lambda e=event: self.edit(e), icon="document-edit-symbolic"))
            item.add_suffix(button("Excluir", lambda e=event: self.delete(e), icon="user-trash-symbolic"))
            section.add(item)
        if not events:
            section.add(row("Seu dia está livre", "Adicione um compromisso usando Novo evento.", "weather-clear-symbolic"))

    def edit(self, event=None):
        form = Form(self.window, "Editar compromisso" if event else "Novo compromisso")
        title = form.entry("Título", event["title"] if event else "")
        day = form.entry("Data (AAAA-MM-DD)", event["day"] if event else self.selected_day())
        time = form.entry("Horário (HH:MM; vazio para dia inteiro)", event["time"] if event else "")
        form.add(label("Notas"))
        notes = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
        notes.get_buffer().set_text(event["notes"] if event else "")
        scroll = Gtk.ScrolledWindow(min_content_height=110, child=notes)
        scroll.add_css_class("card")
        form.add(scroll)

        def save():
            buffer = notes.get_buffer()
            self.window.store.save_event(day.get_text(), time.get_text().strip(), title.get_text(),
                                         buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False),
                                         event["id"] if event else None)
            date = dt.date.fromisoformat(day.get_text())
            self.calendar.select_day(GLib.DateTime.new_local(date.year, date.month, date.day, 12, 0, 0))
            self.refresh()
            self.notify("Compromisso salvo.")
        form.callback = save
        form.present()

    def delete(self, event):
        def remove():
            self.window.store.delete_event(event["id"])
            self.refresh()
        confirm(self.window, "Excluir compromisso?", event["title"], remove, "Excluir", True)

    def on_show(self):
        self.refresh()
