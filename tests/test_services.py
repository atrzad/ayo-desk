import subprocess
import unittest
from unittest.mock import patch
from ayo_desk.network import wifi_connection, shared_connection, ap_security, AP_SECURITY, AP_FLAGS
from ayo_desk.tasks import Audio, volume_percent
from ayo_desk.bluetooth import Bluetooth, AGENT_XML
from gi.repository import Gio, GLib


class NetworkProfileTests(unittest.TestCase):
    def test_wifi_security_profiles(self):
        for security, password in (("open", ""), ("wpa-psk", "a valid passphrase"), ("sae", "test"), ("owe", "")):
            with self.subTest(security=security):
                connection = wifi_connection("Café : \\ rede".encode(), security, password, True, "wlan0")
                self.assertTrue(connection.verify())
                self.assertTrue(connection.get_setting_wireless().props.hidden)
                self.assertEqual(bytes(connection.get_setting_wireless().props.ssid.get_data()), "Café : \\ rede".encode())
                auth = connection.get_setting_wireless_security()
                self.assertEqual(auth.props.key_mgmt if auth else "open", security)

    def test_invalid_credentials(self):
        for ssid, security, password in ((b"", "open", ""), (b"x" * 33, "open", ""),
                                         (b"wifi", "wpa-psk", "123"), (b"wifi", "sae", ""),
                                         (b"wifi", "enterprise", "password")):
            with self.assertRaises(ValueError):
                wifi_connection(ssid, security, password)

    def test_sharing_is_manual_and_bound_to_ethernet(self):
        connection = shared_connection("enp7s0")
        self.assertTrue(connection.verify())
        self.assertEqual(connection.get_setting_ip4_config().props.method, "shared")
        self.assertEqual(connection.get_setting_ip6_config().props.method, "disabled")
        setting = connection.get_setting_connection()
        self.assertEqual(setting.props.interface_name, "enp7s0")
        self.assertFalse(setting.props.autoconnect)
        self.assertEqual(setting.props.type, "802-3-ethernet")


class AudioTests(unittest.TestCase):
    def test_channel_volume(self):
        self.assertEqual(volume_percent({"volume": {"left": {"value_percent": "40%"}, "right": {"value_percent": "60%"}}}), 50)
        self.assertEqual(volume_percent({}), 0)

    @patch("ayo_desk.tasks.subprocess.run")
    def test_actions_are_argument_vectors(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        Audio.change("set-default-sink", "a name; $(test)")
        self.assertEqual(run.call_args.args[0], ["pactl", "set-default-sink", "a name; $(test)"])
        self.assertNotIn("shell", run.call_args.kwargs)
        with self.assertRaises(ValueError):
            Audio.change("arbitrary-command")


class BluetoothTests(unittest.TestCase):
    def test_pairing_agent_interface(self):
        interface = Gio.DBusNodeInfo.new_for_xml(AGENT_XML).interfaces[0]
        names = {method.name for method in interface.methods}
        self.assertTrue({"RequestConfirmation", "RequestPasskey", "RequestPinCode", "Cancel"}.issubset(names))

    def test_agent_does_not_accept_untrusted_bus_sender(self):
        from unittest.mock import Mock
        bt = Bluetooth.__new__(Bluetooth)
        bt.owner = ":1.2"
        invocation = Mock()
        bt._agent(None, ":1.99", "", "", "RequestAuthorization", GLib.Variant("(o)", ("/device",)), invocation)
        invocation.return_dbus_error.assert_called_once()
        invocation.return_value.assert_not_called()


if __name__ == "__main__":
    unittest.main()
