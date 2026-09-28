"""10-band equalizer with built-in and user presets; changes are heard immediately."""
from gi.repository import Adw, Gtk

from .playlists import ask_name

FREQUENCIES = ("29", "59", "119", "237", "474", "947", "1,9k", "3,8k", "7,5k", "15k")
PRESETS = {
    "Plano": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    "Rock": [5, 4, 3, 1, -1, -1, 1, 3, 4, 5],
    "Pop": [-1, 1, 3, 4, 3, 0, -1, -1, 1, 2],
    "Jazz": [3, 2, 1, 2, -1, -1, 0, 1, 2, 3],
    "Clássica": [4, 3, 2, 1, -1, -1, 0, 2, 3, 4],
    "Graves+": [6, 5, 4, 2, 0, 0, 0, 0, 0, 0],
    "Vocal": [-2, -2, -1, 1, 3, 4, 3, 1, 0, -1],
    "Eletrônica": [5, 4, 1, 0, -2, 1, 0, 1, 4, 5],
    "Acústico": [3, 2, 1, 1, 2, 2, 3, 3, 2, 1],
    "Noturno": [-3, -2, 0, 1, 1, 1, 0, -1, -2, -3],
}
CUSTOM = "Personalizado"


def db_text(value):
    return f"{value:+.0f}".replace("+0", "0")


class EqualizerDialog(Adw.Dialog):
    def __init__(self, controller):
        super().__init__(title="Equalizador", content_width=620)
        self.controller = controller
        self.syncing = False
        settings = controller.equalizer_settings()
        self.custom = dict(controller.store.setting("music.eq_presets", {}) or {})
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.enabled = Gtk.Switch(active=settings["enabled"], valign=Gtk.Align.CENTER,
                                  tooltip_text="Ligar ou desligar o equalizador")
        self.enabled.connect("notify::active", lambda *_: self._apply())
        header.pack_start(self.enabled)
        toolbar.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_top=12, margin_bottom=20,
                      margin_start=20, margin_end=20)
        top = Gtk.Box(spacing=8)
        self.preset = Gtk.DropDown(hexpand=True)
        self.preset.connect("notify::selected", self._preset_chosen)
        top.append(self.preset)
        save = Gtk.Button(icon_name="document-save-symbolic", tooltip_text="Salvar como preset")
        save.connect("clicked", lambda _b: ask_name(controller.window, "Salvar preset", self._save_preset,
                                                    confirm="Salvar"))
        top.append(save)
        self.delete = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Excluir este preset")
        self.delete.connect("clicked", lambda _b: self._delete_preset())
        top.append(self.delete)
        reset = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Voltar ao plano")
        reset.connect("clicked", lambda _b: self._load_bands(PRESETS["Plano"], "Plano"))
        top.append(reset)
        box.append(top)

        sliders = Gtk.Box(spacing=4, homogeneous=True)
        self.scales, self.values = [], []
        for number, frequency in enumerate(FREQUENCIES):
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            value = Gtk.Label(label="0")
            value.add_css_class("numeric")
            value.add_css_class("caption")
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, -12, 12, 1)
            scale.set_inverted(True)
            scale.set_draw_value(False)
            scale.set_size_request(-1, 200)
            scale.add_mark(0, Gtk.PositionType.RIGHT, None)
            scale.set_tooltip_text(f"{frequency}Hz")
            scale.connect("value-changed", self._band_moved, number)
            name = Gtk.Label(label=frequency)
            name.add_css_class("caption")
            name.add_css_class("dim-label")
            for widget in (value, scale, name):
                column.append(widget)
            self.scales.append(scale)
            self.values.append(value)
            sliders.append(column)
        box.append(sliders)
        hint = Gtk.Label(label="Frequências em Hz. Arraste as barras ou escolha um preset.", xalign=0)
        hint.add_css_class("dim-label")
        hint.add_css_class("caption")
        box.append(hint)
        toolbar.set_content(box)
        self.set_child(toolbar)
        self._fill_presets(settings["preset"])
        self._load_bands(settings["bands"], settings["preset"], apply=False)

    def _names(self):
        return list(PRESETS) + sorted(self.custom) + [CUSTOM]

    def _fill_presets(self, selected):
        self.syncing = True
        names = self._names()
        self.preset.set_model(Gtk.StringList.new(names))
        self.preset.set_selected(names.index(selected) if selected in names else names.index(CUSTOM))
        self.delete.set_sensitive(selected in self.custom)
        self.syncing = False

    def _current_name(self):
        names = self._names()
        return names[self.preset.get_selected()]

    def _preset_chosen(self, _dropdown, _pspec):
        if self.syncing:
            return
        name = self._current_name()
        self.delete.set_sensitive(name in self.custom)
        bands = PRESETS.get(name) or self.custom.get(name)
        if bands is not None:
            self._load_bands(bands, name)

    def _load_bands(self, bands, name, apply=True):
        self.syncing = True
        for scale, label, value in zip(self.scales, self.values, bands):
            scale.set_value(value)
            label.set_text(db_text(value))
        self.syncing = False
        self._fill_presets(name)
        if apply:
            self._apply()

    def _band_moved(self, scale, number):
        self.values[number].set_text(db_text(scale.get_value()))
        if self.syncing:
            return
        bands = self.bands()
        match = next((name for name, values in {**PRESETS, **self.custom}.items() if values == bands), CUSTOM)
        self._fill_presets(match)
        self._apply()

    def bands(self):
        return [round(scale.get_value()) for scale in self.scales]

    def _apply(self):
        self.controller.set_equalizer(self.enabled.get_active(), self.bands(), self._current_name())

    def _save_preset(self, name):
        name = " ".join(name.split())
        if name in PRESETS or name == CUSTOM:
            self.controller.notify("Esse nome já é de um preset do Ayo.")
            return
        self.custom[name] = self.bands()
        self.controller.store.set_setting("music.eq_presets", self.custom)
        self._fill_presets(name)
        self._apply()

    def _delete_preset(self):
        name = self._current_name()
        if name in self.custom:
            del self.custom[name]
            self.controller.store.set_setting("music.eq_presets", self.custom)
            self._fill_presets(CUSTOM)
            self._apply()
