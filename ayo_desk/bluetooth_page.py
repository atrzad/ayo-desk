from gi.repository import GLib, Gtk
from .bluetooth import Bluetooth
from .widgets import Page, Form, button, clear, confirm, group, label, row


class BluetoothPage(Page):
    def __init__(self, window):
        super().__init__(window, "Bluetooth", "Encontre e conecte fones, teclados e outros dispositivos.")
        self.timer = None
        self.closed = False
        self.bluetooth = Bluetooth(self.schedule, self.notify, self.prompt)
        self.toolbar.append(button("Atualizar", self.bluetooth.refresh, icon="view-refresh-symbolic"))
        self.render()

    def schedule(self):
        if not self.closed and not self.timer:
            self.timer = GLib.timeout_add(400, self._later)

    def _later(self):
        self.timer = None
        self.render()
        return GLib.SOURCE_REMOVE

    def render(self):
        clear(self.body)
        bt = self.bluetooth
        if bt.error:
            group(self.body, "Bluetooth").add(row("Serviço indisponível", bt.error))
            return
        adapters = group(self.body, "Adaptadores")
        if not bt.adapters():
            adapters.add(row("Nenhum adaptador Bluetooth encontrado", "Verifique se o adaptador está conectado e habilitado."))
        for path, adapter in bt.adapters():
            item = row(adapter.get("Alias", "Bluetooth"), adapter.get("Address", ""), "bluetooth-symbolic")
            toggle = Gtk.Switch(active=adapter.get("Powered", False), valign=Gtk.Align.CENTER)

            def toggled(_switch, enabled, p=path):
                bt.set_property(p, "org.bluez.Adapter1", "Powered", enabled)
                return True
            toggle.connect("state-set", toggled)
            item.add_suffix(toggle)
            search = button("Parar busca" if path in bt.discovery else "Buscar dispositivos", lambda p=path: bt.scan(p))
            search.set_sensitive(adapter.get("Powered", False))
            item.add_suffix(search)
            adapters.add(item)
        paired = group(self.body, "Meus dispositivos", "Dispositivos pareados. A confiança permite conexões futuras sem nova autorização.")
        nearby = group(self.body, "Dispositivos encontrados", "Clique em Buscar dispositivos para atualizar os aparelhos próximos.")
        counts = [0, 0]
        for path, device in bt.devices():
            is_paired = device.get("Paired", False)
            counts[0 if is_paired else 1] += 1
            state = "Conectado" if device.get("Connected") else "Pareado" if is_paired else "Disponível"
            details = f"{state}  •  {device.get('Address', '')}"
            battery = bt.objects.get(path, {}).get("org.bluez.Battery1", {}).get("Percentage")
            if battery is not None:
                details += f"  •  Bateria {battery}%"
            item = row(device.get("Alias", "Dispositivo"), details, device.get("Icon", "bluetooth-symbolic"))
            if path in bt.pending:
                item.set_subtitle("Pareando… confirme o código quando solicitado.")
                item.add_suffix(button("Cancelar", lambda p=path: bt.call(p, "org.bluez.Device1", "CancelPairing")))
            elif device.get("Connected"):
                item.add_suffix(button("Desconectar", lambda p=path: bt.call(p, "org.bluez.Device1", "Disconnect")))
            elif is_paired:
                item.add_suffix(button("Conectar", lambda p=path: bt.call(p, "org.bluez.Device1", "Connect")))
            else:
                item.add_suffix(button("Parear", lambda p=path: bt.pair(p), "suggested-action"))
            if is_paired:
                trusted = device.get("Trusted", False)
                item.add_suffix(button("Revogar confiança" if trusted else "Confiar",
                    lambda p=path, t=trusted: bt.set_property(p, "org.bluez.Device1", "Trusted", not t)))
                item.add_suffix(button("Esquecer dispositivo", lambda p=path, d=device: self.forget(p, d), icon="user-trash-symbolic"))
            (paired if is_paired else nearby).add(item)
        if not counts[0]:
            paired.add(row("Nenhum dispositivo pareado"))
        if not counts[1]:
            nearby.add(row("Nenhum dispositivo novo", "Deixe o aparelho no modo de pareamento e inicie uma busca."))

    def forget(self, path, device):
        confirm(self.window, "Esquecer dispositivo?", f"Será necessário parear {device.get('Alias', 'o dispositivo')} novamente.",
                lambda: self.bluetooth.remove(path, device["Adapter"]), "Esquecer", True)

    def prompt(self, name, message, mode, answer):
        form = Form(self.window, name, "Fechar" if mode == "display" else "Confirmar")
        form.add(label(message))
        entry = form.entry("Código") if mode in ("pin", "passkey") else None
        answered = False

        def submit():
            nonlocal answered
            value = entry.get_text() if entry else True
            if mode == "passkey" and (not value.isascii() or not value.isdigit() or len(value) > 6):
                raise ValueError("O código deve conter de 1 a 6 dígitos.")
            if mode == "pin" and not 1 <= len(value) <= 16:
                raise ValueError("O PIN deve conter de 1 a 16 caracteres.")
            answered = True
            answer(value)

        def closed(_form):
            nonlocal answered
            if not answered:
                answered = True
                answer(None)
            return False
        form.callback = submit
        form.connect("close-request", closed)
        form.present()
        return form.close

    def on_show(self):
        self.bluetooth.refresh()

    def close(self):
        self.closed = True
        if self.timer:
            GLib.source_remove(self.timer)
        self.bluetooth.close()
