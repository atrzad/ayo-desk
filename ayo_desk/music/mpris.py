"""MPRIS 2 on the session bus, so media keys, playerctl and Waybar control Ayo Música.

The `target` object provides two methods and knows nothing about D-Bus:
    target.mpris_state() -> dict with status, loop, shuffle, volume, rate, position (seconds),
                            metadata (dict), can_next, can_previous, can_play, can_seek
    target.mpris_action(name, *args) for Raise, Quit, Play, Pause, PlayPause, Stop, Next, Previous,
                            Seek(seconds), SetPosition(seconds), OpenUri(uri), LoopStatus(str),
                            Shuffle(bool), Volume(float 0..1), Rate(float)
"""
import hashlib

from gi.repository import Gio, GLib

BUS_NAME = "org.mpris.MediaPlayer2.ayo_musica"
PATH = "/org/mpris/MediaPlayer2"
ROOT = "org.mpris.MediaPlayer2"
PLAYER = "org.mpris.MediaPlayer2.Player"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
MIME_TYPES = ["audio/mpeg", "audio/flac", "audio/x-flac", "audio/ogg", "audio/x-vorbis+ogg", "audio/opus",
              "audio/wav", "audio/x-wav", "audio/mp4", "audio/x-m4a", "audio/aac", "audio/x-aiff"]

XML = f"""
<node>
  <interface name="{ROOT}">
    <method name="Raise"/>
    <method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="{PLAYER}">
    <method name="Next"/>
    <method name="Previous"/>
    <method name="Pause"/>
    <method name="PlayPause"/>
    <method name="Stop"/>
    <method name="Play"/>
    <method name="Seek"><arg direction="in" name="Offset" type="x"/></method>
    <method name="SetPosition">
      <arg direction="in" name="TrackId" type="o"/>
      <arg direction="in" name="Position" type="x"/>
    </method>
    <method name="OpenUri"><arg direction="in" name="Uri" type="s"/></method>
    <signal name="Seeked"><arg name="Position" type="x"/></signal>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="LoopStatus" type="s" access="readwrite"/>
    <property name="Rate" type="d" access="readwrite"/>
    <property name="Shuffle" type="b" access="readwrite"/>
    <property name="Metadata" type="a{{sv}}" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
  </interface>
</node>
"""
USEC = 1_000_000


def track_id(path):
    """A stable, valid D-Bus object path for a file."""
    if not path:
        return NO_TRACK
    return "/io/github/ayodesk/Music/Track/T" + hashlib.sha1(str(path).encode()).hexdigest()[:16]


def metadata_variant(meta):
    """Convert {"path", "title", "artist" (str), "album", "album_artist", "length" (s), "art" (path)...}."""
    if not meta or not meta.get("path"):
        return GLib.Variant("a{sv}", {"mpris:trackid": GLib.Variant("o", NO_TRACK)})
    data = {"mpris:trackid": GLib.Variant("o", track_id(meta["path"]))}
    if meta.get("length"):
        data["mpris:length"] = GLib.Variant("x", int(meta["length"] * USEC))
    if meta.get("art"):
        data["mpris:artUrl"] = GLib.Variant("s", Gio.File.new_for_path(str(meta["art"])).get_uri())
    if "://" in str(meta["path"]):
        data["xesam:url"] = GLib.Variant("s", str(meta["path"]))
    else:
        data["xesam:url"] = GLib.Variant("s", Gio.File.new_for_path(str(meta["path"])).get_uri())
    for key, field in (("xesam:title", "title"), ("xesam:album", "album")):
        if meta.get(field):
            data[key] = GLib.Variant("s", str(meta[field]))
    for key, field in (("xesam:artist", "artist"), ("xesam:albumArtist", "album_artist"), ("xesam:genre", "genre")):
        if meta.get(field):
            data[key] = GLib.Variant("as", [str(meta[field])])
    for key, field in (("xesam:trackNumber", "track"), ("xesam:discNumber", "disc"), ("xesam:useCount", "plays")):
        if meta.get(field):
            data[key] = GLib.Variant("i", int(meta[field]))
    if meta.get("rating"):
        data["xesam:userRating"] = GLib.Variant("d", meta["rating"] / 5)
    return GLib.Variant("a{sv}", data)


LOOP_TO_MPRIS = {"off": "None", "all": "Playlist", "one": "Track"}
LOOP_FROM_MPRIS = {value: key for key, value in LOOP_TO_MPRIS.items()}


class Mpris:
    def __init__(self, connection, target, desktop_entry="io.github.ayodesk.Desk.music", own_name=True):
        self.connection = connection
        self.target = target
        self.desktop_entry = desktop_entry
        info = Gio.DBusNodeInfo.new_for_xml(XML)
        # GLib 2.84 renamed the closure-based registration; keep working on older systems.
        register = getattr(connection, "register_object_with_closures2", None) or connection.register_object
        self.registrations = [register(PATH, interface, self._call, self._get, self._set)
                              for interface in info.interfaces]
        self.owner = Gio.bus_own_name_on_connection(connection, BUS_NAME, Gio.BusNameOwnerFlags.DO_NOT_QUEUE,
                                                    None, None) if own_name else 0
        self.last = {}

    @classmethod
    def on_session_bus(cls, target, **kwargs):
        try:
            connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            return None
        return cls(connection, target, **kwargs)

    def _root_properties(self):
        return {"CanQuit": GLib.Variant("b", True), "CanRaise": GLib.Variant("b", True),
                "HasTrackList": GLib.Variant("b", False), "Identity": GLib.Variant("s", "Ayo Música"),
                "DesktopEntry": GLib.Variant("s", self.desktop_entry),
                "SupportedUriSchemes": GLib.Variant("as", ["file", "http", "https"]),
                "SupportedMimeTypes": GLib.Variant("as", MIME_TYPES)}

    def _player_properties(self):
        state = self.target.mpris_state()
        return {
            "PlaybackStatus": GLib.Variant("s", state["status"]),
            "LoopStatus": GLib.Variant("s", LOOP_TO_MPRIS.get(state["loop"], "None")),
            "Rate": GLib.Variant("d", float(state.get("rate", 1.0))),
            "Shuffle": GLib.Variant("b", bool(state["shuffle"])),
            "Metadata": metadata_variant(state.get("metadata")),
            "Volume": GLib.Variant("d", float(state["volume"])),
            "Position": GLib.Variant("x", int(state.get("position", 0) * USEC)),
            "MinimumRate": GLib.Variant("d", 0.5),
            "MaximumRate": GLib.Variant("d", 2.0),
            "CanGoNext": GLib.Variant("b", bool(state.get("can_next", True))),
            "CanGoPrevious": GLib.Variant("b", bool(state.get("can_previous", True))),
            "CanPlay": GLib.Variant("b", bool(state.get("can_play", True))),
            "CanPause": GLib.Variant("b", bool(state.get("can_play", True))),
            "CanSeek": GLib.Variant("b", bool(state.get("can_seek", True))),
            "CanControl": GLib.Variant("b", True),
        }

    def _get(self, _connection, _sender, _path, interface, name):
        properties = self._root_properties() if interface == ROOT else self._player_properties()
        return properties.get(name)

    def _set(self, _connection, _sender, _path, _interface, name, value):
        value = value.unpack()
        if name == "LoopStatus":
            if value not in LOOP_FROM_MPRIS:
                return False
            value = LOOP_FROM_MPRIS[value]
        elif name == "Volume":
            value = max(0.0, min(1.0, float(value)))
        elif name == "Rate":
            if value <= 0:  # the spec says a zero rate means pause
                self.target.mpris_action("Pause")
                return True
            value = max(0.5, min(2.0, float(value)))
        elif name != "Shuffle":
            return False
        self.target.mpris_action(name, value)
        return True

    def _call(self, _connection, _sender, _path, interface, method, parameters, invocation):
        args = parameters.unpack()
        if method == "Seek":
            args = (args[0] / USEC,)
        elif method == "SetPosition":
            current = self.target.mpris_state().get("metadata") or {}
            if args[0] != track_id(current.get("path")):
                invocation.return_value(None)  # stale request for another track: ignored, per the spec
                return
            args = (args[1] / USEC,)
        self.target.mpris_action(method, *args)
        invocation.return_value(None)

    def changed(self, *names):
        """Emit PropertiesChanged for the given player properties (all when empty)."""
        properties = self._player_properties()
        properties.pop("Position")
        wanted = names or tuple(properties)
        changed = {}
        for name in wanted:
            value = properties.get(name)
            if value is None:
                continue
            if self.last.get(name) != value.print_(False):
                self.last[name] = value.print_(False)
                changed[name] = value
        if changed:
            self.connection.emit_signal(None, PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                        GLib.Variant("(sa{sv}as)", (PLAYER, changed, [])))

    def seeked(self, seconds):
        self.connection.emit_signal(None, PATH, PLAYER, "Seeked", GLib.Variant("(x)", (int(seconds * USEC),)))

    def close(self):
        for registration in self.registrations:
            self.connection.unregister_object(registration)
        self.registrations = []
        if self.owner:
            Gio.bus_unown_name(self.owner)
            self.owner = 0
