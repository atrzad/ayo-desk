from concurrent.futures import ThreadPoolExecutor
import json
from gi.repository import Adw, GLib, Gtk
from .tasks import Audio, background, volume_percent
from .widgets import Page, button, clear, group, row


class AudioPage(Page):
    def __init__(self, window):
        super().__init__(window, "Áudio e microfone", "Escolha seus dispositivos e ajuste cada aplicativo.")
        self.toolbar.append(button("Atualizar", self.refresh, icon="view-refresh-symbolic"))
        self.loading = False
        self.closed = False
        self.syncing = False
        self.pending = {}
        self.controls = {}
        self.signature = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ayo-audio")
        self.timer = GLib.timeout_add_seconds(4, self._tick)
        group(self.body, "Dispositivos").add(row("Abra esta seção para carregar o áudio."))

    def _tick(self):
        if self.get_mapped() and not self.pending:
            self.refresh()
        return GLib.SOURCE_CONTINUE

    def on_show(self):
        self.refresh()

    def refresh(self):
        if self.loading or self.closed:
            return
        self.loading = True
        background(Audio.snapshot, self._loaded)

    def _loaded(self, snapshot, error):
        self.loading = False
        if self.closed:
            return
        if error:
            clear(self.body)
            self.signature = None
            group(self.body, "Áudio indisponível").add(row("Não foi possível acessar o servidor de áudio", error))
            return
        signature = json.dumps({
            "devices": {k: [(d["index"], d.get("name"), d.get("description"), d.get("sink"), d.get("source"))
                             for d in snapshot[k]] for k in ("sinks", "sources", "sink-inputs", "source-outputs")},
            "cards": snapshot["cards"],
            "defaults": [snapshot["info"].get("default_sink_name"), snapshot["info"].get("default_source_name")],
        }, sort_keys=True)
        self.syncing = True
        if signature != self.signature:
            self.signature = signature
            self._build(snapshot)
        else:
            for kind in ("sink", "source", "sink-input", "source-output"):
                plural = {"sink": "sinks", "source": "sources", "sink-input": "sink-inputs", "source-output": "source-outputs"}[kind]
                for device in snapshot[plural]:
                    key = (kind, device["index"])
                    if key in self.controls and key not in self.pending:
                        scale, mute = self.controls[key]
                        if not scale.has_focus():
                            scale.set_value(volume_percent(device))
                        mute.set_active(device.get("mute", False))
        self.syncing = False

    def _build(self, snapshot):
        clear(self.body)
        self.controls = {}
        for kind, plural, title, description, icon in (
            ("sink", "sinks", "Saídas de som", "Fones, caixas e HDMI. Padrão vale para novas reproduções.", "audio-speakers-symbolic"),
            ("source", "sources", "Microfones", "Volume de entrada e silenciamento do microfone.", "audio-input-microphone-symbolic"),
            ("sink-input", "sink-inputs", "Aplicativos reproduzindo", "Ajuste o volume e a saída de cada aplicativo.", "multimedia-player-symbolic"),
            ("source-output", "source-outputs", "Aplicativos capturando", "Veja quem está usando uma entrada de áudio.", "media-record-symbolic"),
        ):
            section = group(self.body, title, description)
            devices = snapshot[plural]
            if kind == "source":
                devices = [d for d in devices if not d.get("name", "").endswith(".monitor")]
            if not devices:
                section.add(row("Nenhum dispositivo disponível" if kind in ("sink", "source") else "Nenhum aplicativo ativo"))
            for device in devices:
                props = device.get("properties", {})
                title_text = device.get("description") or props.get("application.name") or device.get("name") or "Aplicativo"
                subtitle = props.get("media.name", "") if "input" in kind or "output" in kind else device.get("name", "")
                item = row(title_text, subtitle, icon)
                scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
                scale.set_size_request(160, -1)
                scale.set_valign(Gtk.Align.CENTER)
                scale.set_draw_value(True)
                scale.set_value(volume_percent(device))
                scale.set_tooltip_text("Volume (%)")
                scale.connect("value-changed", lambda s, k=kind, i=device["index"]: self.volume(k, i, s.get_value()))
                item.add_suffix(scale)
                mute = Gtk.ToggleButton(icon_name="audio-volume-muted-symbolic", tooltip_text="Silenciar", valign=Gtk.Align.CENTER)
                mute.set_active(device.get("mute", False))
                mute.connect("toggled", lambda b, k=kind, i=device["index"]: self.change(f"set-{k}-mute", i, "1" if b.get_active() else "0") if not self.syncing else None)
                item.add_suffix(mute)
                self.controls[(kind, device["index"])] = (scale, mute)
                section.add(item)
                if kind in ("sink", "source"):
                    default = device["name"] == snapshot["info"].get(f"default_{kind}_name")
                    control = button("Padrão" if default else "Usar como padrão",
                                     lambda k=kind, n=device["name"]: self.change(f"set-default-{k}", n))
                    control.set_sensitive(not default)
                    item.add_suffix(control)
                else:
                    target_kind = "sink" if kind == "sink-input" else "source"
                    targets = snapshot["sinks" if target_kind == "sink" else "sources"]
                    names = [t.get("description", t["name"]) for t in targets]
                    if names:
                        select = Adw.ComboRow(title="Reproduzir em" if target_kind == "sink" else "Capturar de", use_markup=False)
                        select.set_model(Gtk.StringList.new(names))
                        select.set_selected(next((i for i, t in enumerate(targets) if t["index"] == device.get(target_kind)), 0))
                        select.connect("notify::selected", lambda c, _p, k=kind, i=device["index"], t=targets:
                                       self.change(f"move-{k}", i, t[c.get_selected()]["name"]) if not self.syncing and c.get_selected() < len(t) else None)
                        section.add(select)
        cards = group(self.body, "Perfis dos dispositivos", "Troque entre saída de alta qualidade e modos com microfone, quando disponíveis.")
        if not snapshot["cards"]:
            cards.add(row("Nenhuma placa de áudio disponível"))
        for card in snapshot["cards"]:
            profiles = card.get("profiles", {})
            if isinstance(profiles, dict):
                profiles = [{**value, "name": key} for key, value in profiles.items()]
            profiles = [p for p in profiles if p.get("available") != "no"]
            if not profiles:
                continue
            control = Adw.ComboRow(title=card.get("properties", {}).get("device.description", card["name"]), use_markup=False)
            control.set_model(Gtk.StringList.new([p.get("description", p["name"]) for p in profiles]))
            control.set_selected(next((i for i, p in enumerate(profiles) if p["name"] == card.get("active_profile")), 0))
            control.connect("notify::selected", lambda c, _p, n=card["name"], p=profiles:
                            self.change("set-card-profile", n, p[c.get_selected()]["name"]) if not self.syncing and c.get_selected() < len(p) else None)
            cards.add(control)

    def volume(self, kind, index, value):
        if self.syncing:
            return
        key = (kind, index)
        if timer := self.pending.pop(key, None):
            GLib.source_remove(timer)

        def apply():
            self.pending.pop(key, None)
            self.change(f"set-{kind}-volume", index, f"{round(value)}%")
            return GLib.SOURCE_REMOVE
        self.pending[key] = GLib.timeout_add(180, apply)

    def change(self, action, *args):
        def done(_value, error):
            if self.closed:
                return
            if error:
                self.notify(error)
            self.refresh()
        background(lambda: Audio.change(action, *args), done, self.executor)

    def close(self):
        self.closed = True
        GLib.source_remove(self.timer)
        for timer in self.pending.values():
            GLib.source_remove(timer)
        self.executor.shutdown(wait=False, cancel_futures=True)
