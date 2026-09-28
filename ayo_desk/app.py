import os
# A forced GTK3 theme suppresses libadwaita's layout rules (notably row padding).
# Scope the native style to this process, before GTK is initialized.
os.environ.pop("GTK_THEME", None)

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from .core import ROOT, Store
from .widgets import button, label

PAGES = {
    "network": ("Rede", "network-wireless-symbolic", "network_page", "NetworkPage"),
    "audio": ("Áudio e microfone", "audio-speakers-symbolic", "audio_page", "AudioPage"),
    "bluetooth": ("Bluetooth", "bluetooth-symbolic", "bluetooth_page", "BluetoothPage"),
    "calendar": ("Calendário", "x-office-calendar-symbolic", "calendar_page", "CalendarPage"),
    "calculator": ("Calculadora", "accessories-calculator-symbolic", "calculator_page", "CalculatorPage"),
    "music": ("Música", "audio-x-generic-symbolic", "music_page", "MusicPage"),
}
SYSTEM_PAGES = ("network", "audio", "bluetooth")
INDEPENDENT_PAGES = frozenset(("calendar", "calculator", "music"))
WINDOW_SIZES = {
    "calendar": (640, 760, 520),
    "calculator": (500, 820, 400),
    "music": (1200, 780, 360),
}


class Window(Adw.ApplicationWindow):
    def __init__(self, app, page="network", standalone=False, store=None):
        standalone = standalone or page in INDEPENDENT_PAGES
        title = f"Ayo {PAGES[page][0]}" if standalone else "Ayo Desk"
        width, height, minimum = WINDOW_SIZES.get(page, (800, 800, 650)) if standalone else (1080, 800, 830)
        super().__init__(application=app, title=title, default_width=width, default_height=height)
        self.set_size_request(minimum, 480 if page == "music" else 560)
        self.store = store or Store()
        self.pages = {}
        self.standalone = standalone
        self.allowed_pages = (page,) if standalone else SYSTEM_PAGES
        self.closed = False
        self.force_quit = False
        self.rows = {}
        self.overlay = Adw.ToastOverlay()
        self.set_content(self.overlay)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.overlay.set_child(outer)
        header = self.header = Adw.HeaderBar()
        self.header_title = Adw.WindowTitle(title=title, subtitle="" if standalone else "Rede, áudio e Bluetooth")
        header.set_title_widget(self.header_title)
        self.theme = button("Alternar tema claro/escuro", self.toggle_theme, icon="display-brightness-symbolic")
        header.pack_end(self.theme)
        outer.append(header)
        layout = Gtk.Box(vexpand=True)
        outer.append(layout)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True, vexpand=True)
        self.stack.set_hhomogeneous(False)
        self.stack.set_vhomogeneous(False)
        if not standalone:
            sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20, width_request=220)
            sidebar.add_css_class("desk-sidebar")
            brand = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_start=20, margin_top=22, margin_bottom=6)
            brand.append(label("AYO", "brand"))
            brand.append(label("DESK / ARCH LINUX", "brand-caption"))
            sidebar.append(brand)
            self.navigation = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
            self.navigation.add_css_class("navigation-sidebar")
            for key in SYSTEM_PAGES:
                name, icon, _module, _class = PAGES[key]
                item = Gtk.ListBoxRow()
                content = Gtk.Box(spacing=12, margin_start=12, margin_end=12, margin_top=14, margin_bottom=14)
                content.append(Gtk.Image.new_from_icon_name(icon))
                content.append(Gtk.Label(label=name, xalign=0))
                item.set_child(content)
                item.page_name = key
                self.rows[key] = item
                self.navigation.append(item)
            self.navigation.connect("row-selected", lambda _list, selected: self.show_page(selected.page_name) if selected else None)
            sidebar.append(self.navigation)
            footer = label("Conexões e dispositivos", "dim-label")
            footer.set_vexpand(True)
            footer.set_valign(Gtk.Align.END)
            footer.set_margin_start(20)
            footer.set_margin_end(20)
            footer.set_margin_bottom(22)
            sidebar.append(footer)
            layout.append(sidebar)
            layout.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        layout.append(self.stack)
        self.connect("close-request", self._closing)
        self.show_page(page)

    def show_page(self, key):
        if self.closed:
            return
        if key not in self.allowed_pages:
            raise ValueError("Abra esta ferramenta pelo seu próprio aplicativo Ayo.")
        if key not in self.pages:
            import importlib
            name, _icon, module_name, class_name = PAGES[key]
            module = importlib.import_module(f"ayo_desk.{module_name}")
            page = getattr(module, class_name)(self)
            self.pages[key] = page
            self.stack.add_titled(page, key, name)
        self.stack.set_visible_child_name(key)
        if not self.standalone and self.navigation.get_selected_row() != self.rows[key]:
            self.navigation.select_row(self.rows[key])
            return
        if self.standalone:
            self.set_title(f"Ayo {PAGES[key][0]}")
        self.pages[key].on_show()

    def toggle_theme(self):
        manager = Adw.StyleManager.get_default()
        manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT if manager.get_dark() else Adw.ColorScheme.FORCE_DARK)

    def notify(self, message):
        if not self.closed:
            self.overlay.add_toast(Adw.Toast(title=str(message), timeout=7))

    def quit_app(self):
        """Close for real, even if a page would rather keep running in the background."""
        self.force_quit = True
        self.close()

    def _closing(self, *_):
        if self.closed:
            return False
        if not self.force_quit and any(getattr(page, "keep_running", lambda: False)() for page in self.pages.values()):
            self.set_visible(False)  # e.g. music keeps playing; launching the app again shows the window
            for page in self.pages.values():
                getattr(page, "on_hidden", lambda: None)()
            return True
        self.closed = True
        for page in self.pages.values():
            page.close()
        self.store.close()
        return False


class Application(Adw.Application):
    def __init__(self, page="network", standalone=False):
        standalone = standalone or page in INDEPENDENT_PAGES
        app_id = "io.github.ayodesk.Desk" + ("." + page if standalone else "")
        super().__init__(application_id=app_id, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.page = page
        self.standalone = standalone
        self.allowed_pages = (page,) if standalone else SYSTEM_PAGES
        self.window = None
        self.add_main_option("page", ord("p"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING, "Seção inicial", "PAGE")
        self.add_main_option("standalone", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Abrir ferramenta separadamente", None)

    def do_startup(self):
        Adw.Application.do_startup(self)
        # DEFAULT follows the desktop's light/dark preference. CSS above keeps accents monochrome.
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)
        provider = Gtk.CssProvider()
        provider.load_from_path(str(ROOT / "data/style.css"))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.window.quit_app() if self.window else self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])

    def do_activate(self):
        if self.window is None or self.window.closed:
            self.window = Window(self, self.page, self.standalone)
        self.window.show_page(self.page)
        self.window.present()

    def do_command_line(self, command_line):
        options = command_line.get_options_dict()
        page = options.lookup_value("page", GLib.VariantType.new("s"))
        requested = page.unpack() if page else self.page
        if requested not in self.allowed_pages:
            command_line.printerr("Este aplicativo permite: " + ", ".join(self.allowed_pages) + "\n")
            return 2
        self.page = requested
        self.activate()
        files = [command_line.create_file_for_arg(arg).get_path() or arg for arg in file_arguments(command_line)]
        page = self.window.pages.get(self.page) if self.window else None
        if files and hasattr(page, "open_files"):
            page.open_files(files)
        return 0


def file_arguments(command_line):
    """Positional arguments (files or URIs) left after --page/--standalone."""
    args, result, skip = command_line.get_arguments()[1:], [], False
    for arg in args:
        if skip:
            skip = False
        elif arg in ("--page", "-p"):
            skip = True
        elif not arg.startswith("-"):
            result.append(arg)
    return result
