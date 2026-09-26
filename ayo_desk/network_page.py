import datetime as dt
from gi.repository import GLib, Gtk
from .network import Network, NM, ap_security, ssid_text, wifi_connection
from .widgets import Page, Form, button, clear, confirm, group, label, row

SECURITY = {"open": "Aberta", "wpa-psk": "WPA/WPA2", "sae": "WPA3", "owe": "OWE",
            "enterprise": "Corporativa", "legacy": "Segurança antiga"}


class NetworkPage(Page):
    def __init__(self, window):
        super().__init__(window, "Rede", "Wi-Fi, conexões salvas e internet pela RJ45.")
        self.timer = None
        self.closed = False
        self.search_text = ""
        self.radio = Gtk.Switch(valign=Gtk.Align.CENTER, sensitive=False)
        self.radio.connect("state-set", self._radio)
        self.toolbar.append(self.radio)
        self.toolbar.append(button("Buscar redes", self.scan, "suggested-action", "view-refresh-symbolic"))
        self.search = Gtk.SearchEntry(placeholder_text="Filtrar redes pelo nome")
        self.search.connect("search-changed", lambda e: self._filter(e.get_text()))
        self.body.append(self.search)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self.body.append(self.content)
        self.network = Network(self.schedule, self.notify, window.store)
        self.render()

    def _filter(self, text):
        self.search_text = text.casefold()
        self.render()

    def _radio(self, _switch, enabled):
        if self.network.client and enabled != self.network.client.wireless_get_enabled():
            self.network.set_enabled(enabled)
        return True

    def scan(self):
        self.network.scan()

    def schedule(self):
        if not self.closed and not self.timer:
            self.timer = GLib.timeout_add(300, self._render_later)

    def _render_later(self):
        self.timer = None
        self.render()
        return GLib.SOURCE_REMOVE

    def render(self):
        clear(self.content)
        net = self.network
        client = net.client
        if net.error or not client or not client.get_nm_running():
            self.radio.set_sensitive(False)
            group(self.content, "Serviço de rede").add(row("Rede indisponível", net.error or "Inicie NetworkManager.service."))
            return
        self.radio.set_sensitive(client.wireless_hardware_get_enabled())
        self.radio.set_state(client.wireless_get_enabled())
        self.radio.set_active(client.wireless_get_enabled())
        active_group = group(self.content, "Conexões ativas")
        active = client.get_active_connections()
        if not active:
            active_group.add(row("Sem conexão ativa", "Escolha uma rede abaixo para conectar.", "network-offline-symbolic"))
        for connection in active:
            devices = connection.get_devices()
            detail = ", ".join(d.get_iface() for d in devices)
            ipv4 = connection.get_ip4_config()
            if ipv4:
                detail += "  •  " + ", ".join(a.get_address() for a in ipv4.get_addresses())
            if connection.get_state() != NM.ActiveConnectionState.ACTIVATED:
                detail += "  •  Conectando…"
            item = row(connection.get_id(), detail, "network-transmit-receive-symbolic")
            if devices:
                item.add_suffix(button("Desconectar", lambda d=devices[0]: net.disconnect_device(d)))
            active_group.add(item)
        networks = group(self.content, "Redes Wi-Fi", "As redes exibidas vêm da última busca do adaptador.")
        hidden = button("Rede oculta", self.hidden)
        networks.set_header_suffix(hidden)
        visible = [(d, ap) for d, ap in net.available() if self.search_text in ssid_text(ap.get_ssid()).casefold()]
        if not visible:
            networks.add(row("Nenhuma rede encontrada", "Ligue o Wi-Fi e clique em Buscar redes."))
        for device, ap in visible:
            connected = ap == device.get_active_access_point()
            security = ap_security(ap)
            strength = ap.get_strength()
            signal = "excellent" if strength >= 75 else "good" if strength >= 50 else "ok" if strength >= 25 else "weak"
            subtitle = f"{strength}% de sinal  •  {SECURITY[security]}  •  {device.get_iface()}"
            if net.saved_for(ap):
                subtitle += "  •  Salva"
            item = row(ssid_text(ap.get_ssid()), subtitle, f"network-wireless-signal-{signal}-symbolic")
            control = button("Conectada" if connected else "Conectar", lambda d=device, a=ap: self.connect_ap(d, a))
            control.set_sensitive(not connected)
            item.add_suffix(control)
            networks.add(item)
        wired = group(self.content, "Compartilhar internet pela RJ45",
                      "Receba internet pelo Wi-Fi e entregue pelo cabo ao outro computador.")
        ethernet = net.devices(NM.DeviceType.ETHERNET)
        if not ethernet:
            wired.add(row("Nenhuma porta Ethernet encontrada", "Conecte um adaptador USB/Ethernet, se necessário."))
        for device in ethernet:
            item = row(device.get_iface(), "Cabo conectado" if device.get_carrier() else "Sem cabo", "network-wired-symbolic")
            connection = device.get_active_connection()
            shared = bool(connection and connection.get_connection().get_setting_ip4_config()
                          and connection.get_connection().get_setting_ip4_config().props.method == "shared")
            if shared:
                item.set_subtitle("Compartilhando internet")
                item.add_suffix(button("Parar", lambda d=device: net.disconnect_device(d)))
            else:
                item.add_suffix(button("Compartilhar", lambda d=device: self.share(d)))
            wired.add(item)
        saved = group(self.content, "Conexões salvas", "Perfis do NetworkManager, incluindo a última utilização conhecida.")
        for connection in sorted(client.get_connections(), key=lambda c: c.get_id().casefold()):
            setting = connection.get_setting_connection()
            timestamp = setting.get_timestamp()
            used = dt.datetime.fromtimestamp(timestamp).strftime("%d/%m/%Y %H:%M") if timestamp else "Nunca utilizada"
            item = row(connection.get_id(), used)
            item.add_suffix(button("Conectar", lambda c=connection: net.activate(c)))
            item.add_suffix(button("Esquecer", lambda c=connection: self.forget(c), icon="user-trash-symbolic"))
            saved.add(item)
        if not client.get_connections():
            saved.add(row("Nenhuma conexão salva"))
        history = group(self.content, "Histórico do Ayo", "Conexões concluídas por este aplicativo. Senhas não entram no histórico.")
        history.set_header_suffix(button("Limpar histórico", self.clear_history, icon="edit-clear-all-symbolic"))
        records = self.window.store.history("network")
        for record in records[:20]:
            when = dt.datetime.fromisoformat(record["created"]).strftime("%d/%m %H:%M")
            history.add(row(record["title"], f"{when}  •  {record['detail']}"))
        if not records:
            history.add(row("O histórico aparecerá após conectar pelo Ayo."))

    def connect_ap(self, device, ap):
        security = ap_security(ap)
        if self.network.saved_for(ap) or security in ("open", "owe"):
            self._safe(lambda: self.network.connect_wifi(device, ap))
            return
        if security in ("enterprise", "legacy"):
            self.notify("Esta rede precisa de um perfil corporativo ou legado já configurado no NetworkManager.")
            return
        form = Form(self.window, f"Conectar a {ssid_text(ap.get_ssid())}", "Conectar")
        entry = form.entry("Senha do Wi-Fi", password=True)

        def connect():
            self.network.connect_wifi(device, ap, entry.get_text())
            entry.set_text("")
        form.callback = connect
        form.present()

    def hidden(self):
        devices = self.network.devices(NM.DeviceType.WIFI)
        if not devices:
            self.notify("Nenhum adaptador Wi-Fi disponível.")
            return
        form = Form(self.window, "Conectar a uma rede oculta", "Conectar")
        name = form.entry("Nome da rede (SSID)")
        adapter = Gtk.DropDown.new_from_strings([d.get_iface() for d in devices])
        form.add(adapter)
        security = Gtk.DropDown.new_from_strings(["WPA/WPA2", "WPA3", "Aberta"])
        form.add(security)
        password = form.entry("Senha", password=True)

        def connect():
            device = devices[adapter.get_selected()]
            connection = wifi_connection(name.get_text().encode(), ["wpa-psk", "sae", "open"][security.get_selected()],
                                         password.get_text(), True, device.get_iface())
            self.network.add_activate(connection, device)
            password.set_text("")
        form.callback = connect
        form.present()

    def _safe(self, work):
        try:
            work()
        except (ValueError, GLib.Error) as exc:
            self.notify(str(exc))

    def share(self, device):
        confirm(self.window, "Compartilhar internet?",
                f"A porta {device.get_iface()} passará a fornecer internet, endereços IP e DNS ao dispositivo conectado por cabo. "
                "A conexão atual dessa porta será substituída até você parar o compartilhamento.",
                lambda: self._safe(lambda: self.network.share(device)), "Compartilhar")

    def forget(self, connection):
        confirm(self.window, "Esquecer conexão?", f"O perfil e a senha salva de {connection.get_id()} serão removidos.",
                lambda: self.network.forget(connection), "Esquecer", True)

    def clear_history(self):
        def clear_records():
            self.window.store.clear_history("network")
            self.render()
        confirm(self.window, "Limpar histórico?", "Apagar os registros locais de conexão do Ayo?", clear_records, "Limpar", True)

    def on_show(self):
        self.render()

    def close(self):
        self.closed = True
        if self.timer:
            GLib.source_remove(self.timer)
        self.network.close()
