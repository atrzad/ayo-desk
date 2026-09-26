import shutil
import time
import unittest

from gi.repository import Gio, GLib

from ayo_desk.music.mpris import BUS_NAME, PATH, PLAYER, ROOT, Mpris, track_id


class Target:
    def __init__(self):
        self.actions = []
        self.state = {"status": "Paused", "loop": "off", "shuffle": False, "volume": 0.7, "rate": 1.0,
                      "position": 12.5, "can_next": True, "can_previous": True, "can_play": True, "can_seek": True,
                      "metadata": {"path": "/m/Álbum/01 - Faixa.mp3", "title": "Faixa", "artist": "Terno Rei",
                                   "album": "Álbum", "length": 241.5, "track": 1, "rating": 4}}

    def mpris_state(self):
        return self.state

    def mpris_action(self, name, *args):
        self.actions.append((name, *args))


def pump(until, seconds=3):
    deadline = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while not until() and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.002)
    return until()


@unittest.skipUnless(shutil.which("dbus-daemon"), "dbus-daemon indisponível")
class MprisTests(unittest.TestCase):
    def setUp(self):
        self.bus = Gio.TestDBus.new(Gio.TestDBusFlags.NONE)
        self.bus.up()
        flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
        self.service = Gio.DBusConnection.new_for_address_sync(self.bus.get_bus_address(), flags, None, None)
        self.client = Gio.DBusConnection.new_for_address_sync(self.bus.get_bus_address(), flags, None, None)
        self.target = Target()
        self.mpris = Mpris(self.service, self.target)
        self.assertTrue(pump(lambda: self.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                               "NameHasOwner", GLib.Variant("(s)", (BUS_NAME,)))[0]))

    def tearDown(self):
        self.mpris.close()
        self.client.close_sync(None)
        self.service.close_sync(None)
        self.bus.down()

    def call(self, name, path, interface, method, params=None):
        result = []
        self.client.call(name, path, interface, method, params, None, Gio.DBusCallFlags.NONE, 2000, None,
                         lambda conn, res: result.append(conn.call_finish(res)))
        self.assertTrue(pump(lambda: result))
        return result[0].unpack()

    def prop(self, interface, name):
        return self.call(BUS_NAME, PATH, "org.freedesktop.DBus.Properties", "Get",
                         GLib.Variant("(ss)", (interface, name)))[0]

    def set_prop(self, name, value):
        self.call(BUS_NAME, PATH, "org.freedesktop.DBus.Properties", "Set", GLib.Variant("(ssv)", (PLAYER, name, value)))

    def test_identity_and_playback_properties(self):
        self.assertEqual(self.prop(ROOT, "Identity"), "Ayo Música")
        self.assertEqual(self.prop(PLAYER, "PlaybackStatus"), "Paused")
        self.assertEqual(self.prop(PLAYER, "Position"), 12_500_000)
        metadata = self.prop(PLAYER, "Metadata")
        self.assertEqual(metadata["xesam:title"], "Faixa")
        self.assertEqual(metadata["xesam:artist"], ["Terno Rei"])
        self.assertEqual(metadata["mpris:length"], 241_500_000)
        self.assertEqual(metadata["xesam:userRating"], 0.8)
        self.assertTrue(metadata["xesam:url"].startswith("file:///m/"))

    def test_media_key_methods_reach_the_player(self):
        for method in ("PlayPause", "Next", "Previous", "Stop", "Raise"):
            interface = ROOT if method == "Raise" else PLAYER
            self.call(BUS_NAME, PATH, interface, method)
        self.call(BUS_NAME, PATH, PLAYER, "Seek", GLib.Variant("(x)", (-5_000_000,)))
        self.assertEqual(self.target.actions, [("PlayPause",), ("Next",), ("Previous",), ("Stop",), ("Raise",),
                                               ("Seek", -5.0)])

    def test_set_position_ignores_other_tracks(self):
        current = track_id(self.target.state["metadata"]["path"])
        self.call(BUS_NAME, PATH, PLAYER, "SetPosition", GLib.Variant("(ox)", (track_id("/outra.mp3"), 1_000_000)))
        self.call(BUS_NAME, PATH, PLAYER, "SetPosition", GLib.Variant("(ox)", (current, 30_000_000)))
        self.assertEqual(self.target.actions, [("SetPosition", 30.0)])

    def test_writable_properties_are_validated(self):
        self.set_prop("LoopStatus", GLib.Variant("s", "Track"))
        self.set_prop("Shuffle", GLib.Variant("b", True))
        self.set_prop("Volume", GLib.Variant("d", 1.7))
        self.set_prop("Rate", GLib.Variant("d", 0.0))
        self.assertEqual(self.target.actions, [("LoopStatus", "one"), ("Shuffle", True), ("Volume", 1.0), ("Pause",)])

    def test_properties_changed_signal_only_sends_differences(self):
        signals = []
        self.client.signal_subscribe(None, "org.freedesktop.DBus.Properties", "PropertiesChanged", PATH, None,
                                     Gio.DBusSignalFlags.NONE, lambda *args: signals.append(args[5].unpack()))
        self.mpris.changed()
        self.assertTrue(pump(lambda: signals))
        self.target.state["status"] = "Playing"
        self.mpris.changed("PlaybackStatus", "Volume")
        self.assertTrue(pump(lambda: len(signals) == 2))
        self.assertEqual(signals[1][1], {"PlaybackStatus": "Playing"})


if __name__ == "__main__":
    unittest.main()
