import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk


def label(text, style=None):
    widget = Gtk.Label(label=text, xalign=0, wrap=True)
    if style:
        widget.add_css_class(style)
    return widget


def button(text, callback, style=None, icon=None):
    widget = Gtk.Button(label=text) if not icon else Gtk.Button(icon_name=icon, tooltip_text=text)
    widget.set_valign(Gtk.Align.CENTER)
    if style:
        widget.add_css_class(style)
    widget.connect("clicked", lambda _: callback())
    return widget


def clear(box):
    while child := box.get_first_child():
        box.remove(child)


def row(title, subtitle="", icon=None):
    widget = Adw.ActionRow(title=str(title), subtitle=str(subtitle), use_markup=False)
    widget.set_title_lines(1)
    widget.set_subtitle_lines(2)
    if icon:
        widget.add_prefix(Gtk.Image.new_from_icon_name(icon))
    return widget


def group(container, title, description=None):
    widget = Adw.PreferencesGroup(title=title)
    if description:
        widget.set_description(description)
    container.append(widget)
    return widget


def confirm(window, title, body, action, text="Confirmar", destructive=False):
    dialog = Adw.AlertDialog(heading=title, body=body)
    dialog.add_response("cancel", "Cancelar")
    dialog.add_response("ok", text)
    dialog.set_close_response("cancel")
    dialog.set_default_response("cancel" if destructive else "ok")
    dialog.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED)
    dialog.connect("response", lambda _d, response: action() if response == "ok" else None)
    dialog.present(window)


class Form(Adw.Window):
    def __init__(self, parent, title, submit="Salvar", callback=None):
        super().__init__(title=title, transient_for=parent, modal=True, default_width=440,
                         application=parent.get_application(), destroy_with_parent=True)
        self.callback = callback
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(Adw.HeaderBar())
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                            margin_top=12, margin_bottom=24, margin_start=24, margin_end=24)
        outer.append(self.body)
        self.error = label("", "error")
        self.error.set_visible(False)
        self.body.append(self.error)
        self.submit = button(submit, self._submit, "suggested-action")
        self.body.append(self.submit)
        self.set_content(outer)

    def add(self, widget):
        self.body.insert_child_after(widget, self.error.get_prev_sibling())
        return widget

    def entry(self, title, value="", password=False):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(label(title))
        entry = Gtk.PasswordEntry(show_peek_icon=True) if password else Gtk.Entry()
        entry.set_text(value)
        entry.set_hexpand(True)
        entry.connect("activate", lambda _: self._submit())
        box.append(entry)
        self.add(box)
        return entry

    def _submit(self):
        try:
            if self.callback:
                self.callback()
            self.close()
        except (ValueError, RuntimeError) as exc:
            self.error.set_text(str(exc))
            self.error.set_visible(True)


class Page(Gtk.Box):
    def __init__(self, window, title, description):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window = window
        self.toolbar = Gtk.Box(spacing=8, margin_top=26, margin_bottom=20, margin_start=28, margin_end=28)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        titles.append(label(title, "title-1"))
        titles.append(label(description, "dim-label"))
        self.toolbar.append(titles)
        self.append(self.toolbar)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                            margin_start=28, margin_end=28, margin_bottom=28)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=1000, tightening_threshold=750)
        clamp.set_child(self.body)
        scroll.set_child(clamp)
        self.append(scroll)

    def notify(self, text):
        self.window.notify(text)

    def on_show(self):
        pass

    def close(self):
        pass
