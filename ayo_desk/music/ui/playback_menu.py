"""Player extras: playback speed, sleep timer and output device."""
import json

from gi.repository import GLib, Gtk

from ...tasks import background, command

SLEEP_CHOICES = (("Desligado", None), ("15 minutos", 15), ("30 minutos", 30), ("45 minutos", 45),
                 ("1 hora", 60), ("1 hora e meia", 90), ("Fim desta música", "track"), ("Fim da fila", "queue"))
SPEED_MARKS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)


def speed_text(rate):
    return f"{rate:.2f}×".replace(".", ",")


def list_outputs():
    """[(name, description)] of PulseAudio/PipeWire sinks; empty if pactl is unavailable."""
    sinks = json.loads(command(["pactl", "--format=json", "list", "sinks"]))
    return [(sink["name"], sink.get("description") or sink["name"]) for sink in sinks]


def section(title):
    label = Gtk.Label(label=title, xalign=0)
    label.add_css_class("heading")
    return label


class PlaybackMenu(Gtk.MenuButton):
    def __init__(self, controller):
        super().__init__(icon_name="preferences-system-time-symbolic", tooltip_text="Velocidade, timer de sono e saída",
                         valign=Gtk.Align.CENTER)
        self.add_css_class("flat")
        self.controller = controller
        self.syncing = False
        self.outputs = []
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=12,
                      margin_start=12, margin_end=12, width_request=300)

        box.append(section("Velocidade"))
        speed_row = Gtk.Box(spacing=8)
        self.speed = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.5, 2.0, 0.05)
        self.speed.set_hexpand(True)
        self.speed.set_draw_value(False)
        for mark in SPEED_MARKS:
            self.speed.add_mark(mark, Gtk.PositionType.BOTTOM, None)
        self.speed.connect("value-changed", self._speed_changed)
        self.speed_label = Gtk.Label(label="1,00×", width_chars=6)
        self.speed_label.add_css_class("numeric")
        reset = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Velocidade normal")
        reset.add_css_class("flat")
        reset.connect("clicked", lambda _b: self.speed.set_value(1.0))
        speed_row.append(self.speed)
        speed_row.append(self.speed_label)
        speed_row.append(reset)
        box.append(speed_row)
        hint = Gtk.Label(label="O tom da voz é mantido.", xalign=0)
        hint.add_css_class("dim-label")
        hint.add_css_class("caption")
        box.append(hint)

        box.append(Gtk.Separator(margin_top=4, margin_bottom=4))
        box.append(section("Timer de sono"))
        self.sleep = Gtk.DropDown.new_from_strings([name for name, _value in SLEEP_CHOICES])
        self.sleep.connect("notify::selected", self._sleep_changed)
        box.append(self.sleep)
        self.sleep_status = Gtk.Label(xalign=0, visible=False)
        self.sleep_status.add_css_class("dim-label")
        self.sleep_status.add_css_class("caption")
        box.append(self.sleep_status)

        box.append(Gtk.Separator(margin_top=4, margin_bottom=4))
        box.append(section("Saída de áudio"))
        self.output = Gtk.DropDown.new_from_strings(["Padrão do sistema"])
        self.output.connect("notify::selected", self._output_changed)
        box.append(self.output)

        popover = Gtk.Popover(child=box)
        popover.connect("show", lambda _p: self.refresh())
        self.set_popover(popover)

    def refresh(self):
        self.syncing = True
        self.speed.set_value(self.controller.player.rate)
        self.speed_label.set_text(speed_text(self.controller.player.rate))
        self.syncing = False
        self.show_sleep()

        def loaded(outputs, error):
            self.syncing = True
            self.outputs = outputs or []
            names = ["Padrão do sistema"] + [description for _name, description in self.outputs]
            self.output.set_model(Gtk.StringList.new(names))
            current = self.controller.player.output
            chosen = next((n + 1 for n, (name, _d) in enumerate(self.outputs) if name == current), 0)
            self.output.set_selected(chosen)
            self.output.set_sensitive(not error)
            self.syncing = False
        background(list_outputs, loaded)

    def show_sleep(self):
        text = self.controller.sleep_text()
        self.sleep_status.set_visible(bool(text))
        self.sleep_status.set_text(text or "")
        mode = self.controller.sleep_mode
        self.syncing = True
        if mode is None:
            self.sleep.set_selected(0)
        elif mode in ("track", "queue"):
            self.sleep.set_selected(next(n for n, (_l, v) in enumerate(SLEEP_CHOICES) if v == mode))
        self.syncing = False
        self.set_icon_name("alarm-symbolic" if mode else "preferences-system-time-symbolic")

    def _speed_changed(self, scale):
        # Snap to the marks when close, so 1× is easy to hit.
        value = round(scale.get_value() / 0.05) * 0.05
        value = next((mark for mark in SPEED_MARKS if abs(mark - value) < 0.03), value)
        self.speed_label.set_text(speed_text(value))
        if not self.syncing:
            self.controller.set_rate(value)

    def _sleep_changed(self, dropdown, _pspec):
        if not self.syncing:
            self.controller.set_sleep(SLEEP_CHOICES[dropdown.get_selected()][1])
            GLib.idle_add(lambda: self.show_sleep() and False)

    def _output_changed(self, dropdown, _pspec):
        if self.syncing:
            return
        selected = dropdown.get_selected()
        name = self.outputs[selected - 1][0] if selected > 0 else None
        self.controller.set_output(name)
