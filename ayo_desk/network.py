"""NetworkManager integration through libnm. Passwords never enter a command line."""
import os
import shutil
import uuid
import gi
gi.require_version("NM", "1.0")
from gi.repository import GLib, NM

AP_SECURITY = getattr(NM, "80211ApSecurityFlags")
AP_FLAGS = getattr(NM, "80211ApFlags")


def ssid_text(ssid):
    return bytes(ssid.get_data()).decode("utf-8", "replace") if ssid else "Rede oculta"


def base_connection(name, kind, interface=None):
    connection = NM.SimpleConnection.new()
    settings = NM.SettingConnection.new()
    settings.props.id = name
    settings.props.uuid = str(uuid.uuid4())
    settings.props.type = kind
    settings.props.autoconnect = False
    if interface:
        settings.props.interface_name = interface
    settings.add_permission("user", os.environ.get("USER") or __import__("getpass").getuser(), None)
    connection.add_setting(settings)
    ipv4 = NM.SettingIP4Config.new()
    ipv4.props.method = "auto"
    connection.add_setting(ipv4)
    ipv6 = NM.SettingIP6Config.new()
    ipv6.props.method = "auto"
    connection.add_setting(ipv6)
    return connection


def wifi_connection(ssid, security="open", password="", hidden=False, interface=None):
    if not isinstance(ssid, bytes) or not 1 <= len(ssid) <= 32:
        raise ValueError("O nome da rede deve conter entre 1 e 32 bytes.")
    if security not in ("open", "wpa-psk", "sae", "owe"):
        raise ValueError("Esta segurança exige um perfil de rede já configurado.")
    if security == "wpa-psk" and not (8 <= len(password.encode()) <= 63 or
                                     len(password) == 64 and all(c in "0123456789abcdefABCDEF" for c in password)):
        raise ValueError("WPA/WPA2 exige uma senha de 8 a 63 bytes ou uma chave hexadecimal de 64 dígitos.")
    if security == "sae" and not 1 <= len(password.encode()) <= 63:
        raise ValueError("Informe a senha WPA3 (até 63 bytes).")
    connection = base_connection(ssid.decode("utf-8", "replace"), "802-11-wireless", interface)
    connection.get_setting_connection().props.autoconnect = True
    wireless = NM.SettingWireless.new()
    wireless.props.ssid = GLib.Bytes.new(ssid)
    wireless.props.mode = "infrastructure"
    wireless.props.hidden = hidden
    connection.add_setting(wireless)
    if security != "open":
        auth = NM.SettingWirelessSecurity.new()
        auth.props.key_mgmt = security
        if security != "owe":
            auth.props.psk = password
        connection.add_setting(auth)
    connection.verify()
    return connection


def shared_connection(interface):
    connection = base_connection(f"Ayo • Internet por {interface}", "802-3-ethernet", interface)
    connection.add_setting(NM.SettingWired.new())
    connection.get_setting_ip4_config().props.method = "shared"
    connection.get_setting_ip6_config().props.method = "disabled"
    connection.verify()
    return connection


def ap_security(ap):
    flags = ap.get_rsn_flags() | ap.get_wpa_flags()
    if flags & AP_SECURITY.KEY_MGMT_PSK:
        return "wpa-psk"
    if flags & AP_SECURITY.KEY_MGMT_SAE:
        return "sae"
    if flags & AP_SECURITY.KEY_MGMT_OWE:
        return "owe"
    if flags & AP_SECURITY.KEY_MGMT_802_1X:
        return "enterprise"
    return "legacy" if ap.get_flags() & AP_FLAGS.PRIVACY else "open"


class Network:
    def __init__(self, changed, notify, store):
        self.closed = False
        self.client = None
        self.error = "Carregando serviço de rede…"
        self.changed, self.notify, self.store = changed, notify, store
        self.device_handlers = {}
        self.pending = {}
        NM.Client.new_async(None, self._ready, None)

    def _ready(self, _source, result, _data):
        try:
            self.client = NM.Client.new_finish(result)
            if self.closed:
                return
            self.error = None
            self.client.connect("notify", self._changed)
            self.client.connect("connection-added", self._changed)
            self.client.connect("connection-removed", self._changed)
            self.client.connect("device-added", self._changed)
            self.client.connect("device-removed", self._changed)
            self._changed()
        except GLib.Error as exc:
            self.error = str(exc)
            self.changed()

    def _changed(self, *_args):
        if self.closed:
            return
        if self.client:
            current = set(self.client.get_devices())
            for device in list(self.device_handlers):
                if device not in current:
                    for handler in self.device_handlers.pop(device):
                        device.disconnect(handler)
            for device in current:
                if device not in self.device_handlers:
                    handlers = [device.connect("notify", lambda *_: self.changed())]
                    if isinstance(device, NM.DeviceWifi):
                        handlers += [device.connect(signal, lambda *_: self.changed())
                                     for signal in ("access-point-added", "access-point-removed")]
                    self.device_handlers[device] = handlers
        self.changed()

    def devices(self, kind):
        if not self.client:
            return []
        return [d for d in self.client.get_devices() if d.get_device_type() == kind]

    def available(self):
        result = []
        for device in self.devices(NM.DeviceType.WIFI):
            best = {}
            for ap in device.get_access_points():
                if not ap.get_ssid():
                    continue
                key = (bytes(ap.get_ssid().get_data()), ap_security(ap))
                if key not in best or ap.get_strength() > best[key].get_strength() or ap == device.get_active_access_point():
                    if key not in best or best[key] != device.get_active_access_point():
                        best[key] = ap
            result += [(device, ap) for ap in best.values()]
        return sorted(result, key=lambda pair: -pair[1].get_strength())

    def saved_for(self, ap):
        return next((c for c in self.client.get_connections() if ap.connection_valid(c)), None)

    def _finish(self, finish_name, message=None, then=None):
        def callback(source, result, *_):
            try:
                value = getattr(source, finish_name)(result)
                if self.closed:
                    return
                if then:
                    then(value)
                elif message:
                    self.notify(message)
            except (GLib.Error, ValueError) as exc:
                self.notify(str(exc))
            self.changed()
        return callback

    def scan(self):
        devices = self.devices(NM.DeviceType.WIFI)
        if not devices:
            self.notify("Nenhum adaptador Wi-Fi encontrado.")
        for device in devices:
            device.request_scan_async(None, self._finish("request_scan_finish", "Busca de redes solicitada."), None)

    def set_enabled(self, enabled):
        self.client.dbus_set_property("/org/freedesktop/NetworkManager", "org.freedesktop.NetworkManager",
                                      "WirelessEnabled", GLib.Variant("b", enabled), 10000, None,
                                      self._finish("dbus_set_property_finish"), None)

    def _watch_activation(self, active):
        path = active.get_path()
        self.notify("Conectando…")

        def state_changed(*_):
            state = active.get_state()
            if state not in (NM.ActiveConnectionState.ACTIVATED, NM.ActiveConnectionState.DEACTIVATED):
                return
            pending = self.pending.pop(path, None)
            if pending:
                active.disconnect(pending[1])
                GLib.source_remove(pending[2])
            if state == NM.ActiveConnectionState.ACTIVATED:
                self.store.log("network", active.get_id(), "Conexão ativada")
                self.notify(f"Conectado: {active.get_id()}")
            else:
                self.notify("A conexão não foi concluída. Verifique a senha e o sinal.")
            self.changed()

        def timeout():
            pending = self.pending.pop(path, None)
            if pending:
                active.disconnect(pending[1])
            self.notify("A conexão continua pendente no sistema; confira o estado da rede.")
            return GLib.SOURCE_REMOVE
        handler = active.connect("notify::state", state_changed)
        timer = GLib.timeout_add_seconds(90, timeout)
        self.pending[path] = (active, handler, timer)
        state_changed()

    def connect_wifi(self, device, ap, password=""):
        saved = self.saved_for(ap)
        if saved:
            self.activate(saved, device, ap.get_path())
            return
        connection = wifi_connection(bytes(ap.get_ssid().get_data()), ap_security(ap), password,
                                     interface=device.get_iface())
        self.add_activate(connection, device, ap.get_path())

    def add_activate(self, connection, device, specific=None):
        self.client.add_and_activate_connection_async(connection, device, specific, None,
            self._finish("add_and_activate_connection_finish", then=self._watch_activation), None)

    def activate(self, connection, device=None, specific=None):
        self.client.activate_connection_async(connection, device, specific, None,
            self._finish("activate_connection_finish", then=self._watch_activation), None)

    def disconnect_device(self, device):
        device.disconnect_async(None, self._finish("disconnect_finish", "Conexão desconectada."), None)

    def forget(self, connection):
        connection.delete_async(None, self._finish("delete_finish", "Perfil removido."), None)

    def share(self, device):
        if not shutil.which("dnsmasq"):
            raise ValueError("Instale o pacote dnsmasq para compartilhar a internet pela RJ45.")
        primary = self.client.get_primary_connection()
        if not primary or primary.get_state() != NM.ActiveConnectionState.ACTIVATED:
            raise ValueError("Conecte este computador à internet antes de compartilhar.")
        if device in primary.get_devices():
            raise ValueError("Escolha uma porta Ethernet diferente da conexão que fornece a internet.")
        existing = next((c for c in self.client.get_connections()
                         if c.get_id() == f"Ayo • Internet por {device.get_iface()}"
                         and c.get_setting_ip4_config() and c.get_setting_ip4_config().props.method == "shared"), None)
        if existing:
            self.activate(existing, device)
        else:
            self.add_activate(shared_connection(device.get_iface()), device)

    def close(self):
        self.closed = True
        for active, handler, timer in self.pending.values():
            active.disconnect(handler)
            GLib.source_remove(timer)
        self.pending.clear()
