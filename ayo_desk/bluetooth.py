"""Asynchronous BlueZ control, including an application-scoped pairing agent."""
from gi.repository import Gio, GLib

AGENT_PATH = "/io/github/ayodesk/Agent"
AGENT_XML = """<node><interface name="org.bluez.Agent1">
<method name="Release"/>
<method name="RequestPinCode"><arg type="o" direction="in"/><arg type="s" direction="out"/></method>
<method name="DisplayPinCode"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
<method name="RequestPasskey"><arg type="o" direction="in"/><arg type="u" direction="out"/></method>
<method name="DisplayPasskey"><arg type="o" direction="in"/><arg type="u" direction="in"/><arg type="q" direction="in"/></method>
<method name="RequestConfirmation"><arg type="o" direction="in"/><arg type="u" direction="in"/></method>
<method name="RequestAuthorization"><arg type="o" direction="in"/></method>
<method name="AuthorizeService"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
<method name="Cancel"/>
</interface></node>"""


class Bluetooth:
    def __init__(self, changed, notify, prompt):
        self.changed, self.notify, self.prompt = changed, notify, prompt
        self.bus = None
        self.objects = {}
        self.error = "Carregando Bluetooth…"
        self.registered = False
        self.registration = None
        self.discovery = set()
        self.discovery_timers = {}
        self.pending = {}
        self.prompts = []
        self.loading = False
        self.subscription = None
        self.watch = None
        self.owner = None
        self.closed = False
        self.display_code = None
        Gio.bus_get(Gio.BusType.SYSTEM, None, self._ready)

    def _ready(self, _source, result):
        try:
            self.bus = Gio.bus_get_finish(result)
            if self.closed:
                return
            self.registration = self.bus.register_object(AGENT_PATH,
                Gio.DBusNodeInfo.new_for_xml(AGENT_XML).interfaces[0], self._agent, None, None)
            self.subscription = self.bus.signal_subscribe("org.bluez", None, None, None, None,
                Gio.DBusSignalFlags.NONE, self._signal)
            self.watch = Gio.bus_watch_name_on_connection(self.bus, "org.bluez", Gio.BusNameWatcherFlags.NONE,
                                                        self._appeared, self._vanished)
        except GLib.Error as exc:
            self.error = str(exc)
            self.changed()

    def _appeared(self, _bus, _name, owner):
        self.owner = owner
        self.refresh()

    def _vanished(self, *_):
        self.owner = None
        self.objects = {}
        self.registered = False
        self.discovery.clear()
        self.error = "O serviço Bluetooth está desligado. Inicie bluetooth.service."
        self._cancel_prompts()
        self.changed()

    def _signal(self, _bus, _sender, _path, interface, signal, _params):
        if interface in ("org.freedesktop.DBus.ObjectManager", "org.freedesktop.DBus.Properties"):
            self.refresh()

    def call(self, path, interface, method, params=None, done=None, timeout=15000):
        if not self.bus:
            self.notify("Bluetooth indisponível.")
            return

        def finished(bus, result):
            try:
                value, error = bus.call_finish(result), None
            except GLib.Error as exc:
                value, error = None, str(exc)
            if done:
                done(value, error)
            elif error:
                self.notify(error)
            self.refresh()
        self.bus.call("org.bluez", path, interface, method, params, None,
                      Gio.DBusCallFlags.NONE, timeout, None, finished)

    def refresh(self):
        if self.closed or not self.bus or self.loading or not self.owner:
            return
        self.loading = True

        def done(bus, result):
            self.loading = False
            try:
                self.objects = bus.call_finish(result).unpack()[0]
                self.error = None
            except GLib.Error as exc:
                self.error = str(exc)
                self.objects = {}
            self.changed()
        self.bus.call("org.bluez", "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects",
                      None, None, Gio.DBusCallFlags.NONE, 8000, None, done)

    def adapters(self):
        return [(p, i["org.bluez.Adapter1"]) for p, i in self.objects.items() if "org.bluez.Adapter1" in i]

    def devices(self):
        return sorted([(p, i["org.bluez.Device1"]) for p, i in self.objects.items() if "org.bluez.Device1" in i],
                      key=lambda v: (not v[1].get("Connected"), not v[1].get("Paired"), v[1].get("Alias", "")))

    def set_property(self, path, interface, prop, value, done=None):
        self.call(path, "org.freedesktop.DBus.Properties", "Set",
                  GLib.Variant("(ssv)", (interface, prop, GLib.Variant("b", value))), done)

    def scan(self, path):
        if path in self.discovery:
            self.stop_scan(path)
            return

        def started(_value, error):
            if error:
                self.notify(error)
                return
            self.discovery.add(path)
            self.changed()
            self.notify("Buscando dispositivos por 30 segundos…")

            def stop():
                self.discovery_timers.pop(path, None)
                self.stop_scan(path)
                return GLib.SOURCE_REMOVE
            self.discovery_timers[path] = GLib.timeout_add_seconds(30, stop)
        self.call(path, "org.bluez.Adapter1", "StartDiscovery", done=started)

    def stop_scan(self, path):
        timer = self.discovery_timers.pop(path, None)
        if timer:
            GLib.source_remove(timer)
        if path in self.discovery:
            self.discovery.discard(path)
            self.call(path, "org.bluez.Adapter1", "StopDiscovery")

    def pair(self, path):
        if self.pending:
            self.notify("Conclua ou cancele o pareamento atual primeiro.")
            return
        self.pending[path] = True
        self.changed()

        def paired(_value, error):
            self.pending.pop(path, None)
            self._cancel_prompts()
            if error:
                self.notify(error)
            else:
                self.notify("Dispositivo pareado. Conectando…")
                self.call(path, "org.bluez.Device1", "Connect")
            self.changed()

        def registered(_value=None, error=None):
            if error:
                self.pending.pop(path, None)
                self.notify(error)
                self.changed()
                return
            self.registered = True
            self.call(path, "org.bluez.Device1", "Pair", done=paired, timeout=90000)
        if self.registered:
            registered()
        else:
            self.call("/org/bluez", "org.bluez.AgentManager1", "RegisterAgent",
                      GLib.Variant("(os)", (AGENT_PATH, "KeyboardDisplay")), registered)

    def remove(self, path, adapter):
        self.call(adapter, "org.bluez.Adapter1", "RemoveDevice", GLib.Variant("(o)", (path,)))

    def _cancel_prompts(self):
        self.display_code = None
        callbacks, self.prompts = self.prompts, []
        for cancel in callbacks:
            cancel()

    def _agent(self, _bus, sender, _path, _interface, method, parameters, invocation):
        if sender != self.owner:
            invocation.return_dbus_error("org.bluez.Error.Rejected", "Remetente inválido")
            return
        args = parameters.unpack()
        if method in ("Cancel", "Release"):
            self._cancel_prompts()
            if method == "Release":
                self.registered = False
            invocation.return_value(None)
            return
        device = self.objects.get(args[0], {}).get("org.bluez.Device1", {})
        name = device.get("Alias", "Dispositivo Bluetooth")
        if method in ("DisplayPinCode", "DisplayPasskey"):
            code = args[1] if method == "DisplayPinCode" else f"{args[1]:06d}"
            if self.display_code != (args[0], code):
                self.display_code = (args[0], code)
                self.prompts.append(self.prompt(name, f"Digite {code} no outro dispositivo e confirme nele.", "display", lambda _: None))
            invocation.return_value(None)
            return
        mode = "confirm"
        message = "Autorizar o pareamento com este dispositivo?"
        if method == "RequestConfirmation":
            message = f"O código {args[1]:06d} também aparece no outro dispositivo?"
        elif method in ("RequestPinCode", "RequestPasskey"):
            mode = "pin" if method == "RequestPinCode" else "passkey"
            message = "Informe o código mostrado pelo dispositivo."
        elif method == "AuthorizeService":
            message = f"Autorizar o serviço Bluetooth solicitado?\n{args[1]}"
        elif method != "RequestAuthorization":
            invocation.return_dbus_error("org.bluez.Error.Rejected", "Método não suportado")
            return

        answered = False

        def answer(value):
            nonlocal answered
            if answered:
                return
            answered = True
            if value is None or value is False:
                invocation.return_dbus_error("org.bluez.Error.Rejected", "Cancelado pelo usuário")
            elif mode == "pin":
                invocation.return_value(GLib.Variant("(s)", (value,)))
            elif mode == "passkey":
                invocation.return_value(GLib.Variant("(u)", (int(value),)))
            else:
                invocation.return_value(None)
        self.prompts.append(self.prompt(name, message, mode, answer))

    def close(self):
        self._cancel_prompts()
        for path in list(self.discovery):
            self.stop_scan(path)
        self.closed = True
        if self.bus:
            # Closing the D-Bus client automatically releases BlueZ discovery sessions.
            if self.registration:
                self.bus.unregister_object(self.registration)
            if self.subscription:
                self.bus.signal_unsubscribe(self.subscription)
            if self.watch:
                Gio.bus_unwatch_name(self.watch)
