"""Sound decisions for the player: loudness leveling, when to crossfade, background analysis, EQ."""
import threading

from gi.repository import GLib

from ...tasks import background
from ..analysis import analyze, from_bytes, leveling_factor
from ..queue import SHUFFLE_OFF

LEVELING_MODES = (("auto", "Automático"), ("track", "Por música"), ("album", "Por álbum"), ("off", "Desligado"))
FLAT = [0] * 10


class Sound:
    def __init__(self, page):
        self.page = page
        self.store = page.store
        self.gains = {}          # path -> (gain dB, peak)
        self.album_gains = {}    # album key -> (gain dB, peak)
        self.urgent = []
        self.pending = None
        self.busy = None
        self.cancel = threading.Event()

    def setting(self, key, default):
        return self.store.setting(f"music.{key}", default)

    # ── loudness leveling ──────────────────────────────────────────────────
    def reload_gains(self):
        self.gains = self.page.music.gains()
        self._album_gains()

    def _album_gains(self):
        albums = {}
        for path, values in self.gains.items():
            track = self.page.library.get(path)
            if track is not None:
                albums.setdefault(track.album_key, []).append(values)
        # Averaging track gains approximates the album gain well enough to keep an album's dynamics.
        self.album_gains = {key: (sum(g for g, _p in values) / len(values),
                                  max((p or 0.0) for _g, p in values) or None)
                            for key, values in albums.items()}

    def _album_context(self, path):
        """Automatic mode uses album gain while an album plays in order (a neighbour is from it)."""
        queue = self.page.queue
        track = self.page.library.get(path)
        if track is None or queue.shuffle != SHUFFLE_OFF:
            return False
        items = queue.items()
        if queue.current == path:
            index = queue.index
        elif queue.index + 1 < len(items) and items[queue.index + 1] == path:
            index = queue.index + 1
        else:
            return False
        for neighbour in (index - 1, index + 1):
            if 0 <= neighbour < len(items):
                other = self.page.library.get(items[neighbour])
                if other is not None and other.path != path and other.album_key == track.album_key:
                    return True
        return False

    def gain_for(self, path):
        mode = self.setting("leveling", "auto")
        if mode == "off" or not path or "://" in str(path):
            return 1.0
        track = self.page.library.get(path)
        values = None
        if track is not None and (mode == "album" or (mode == "auto" and self._album_context(path))):
            values = self.album_gains.get(track.album_key)
        values = values or self.gains.get(path)
        if not values:
            return 1.0
        return leveling_factor(values[0], values[1], float(self.setting("preamp", 0.0)))

    # ── crossfade ──────────────────────────────────────────────────────────
    def crossfade_for(self, current, upcoming, duration):
        """Seconds of crossfade between two tracks, or 0 for a gapless/normal transition."""
        seconds = float(self.setting("crossfade", 0))
        if seconds <= 0 or not current or not upcoming or "://" in str(upcoming):
            return 0
        if duration and duration < seconds * 2 + 2:
            return 0
        if self.setting("crossfade_skip_albums", True) and self.page.queue.shuffle == SHUFFLE_OFF:
            # Like Plexamp's "sweet fades": consecutive tracks of one album keep their own transitions.
            a, b = self.page.library.get(current), self.page.library.get(upcoming)
            if a is not None and b is not None and a.album_key == b.album_key:
                return 0
        return seconds

    # ── background analysis (loudness + waveform) ──────────────────────────
    def request(self, *paths):
        """Measure these first (current and next track)."""
        for path in reversed([p for p in paths if p and "://" not in str(p)]):
            if path in self.urgent:
                self.urgent.remove(path)
            self.urgent.insert(0, path)
        self._next()

    def start_library(self):
        self.pending = None
        self._next()

    def library_enabled(self):
        return self.setting("analyze_library", True) and self.setting("leveling", "auto") != "off"

    def progress(self):
        """(measured, total) for the preferences page."""
        total = self.page.library.tracks.get_n_items()
        pending = len(self.page.music.pending_analysis())
        return max(0, total - pending), total

    def _next(self):
        if self.busy or self.page.closed:
            return
        music, library = self.page.music, self.page.library
        path = None
        while self.urgent and path is None:
            candidate = self.urgent.pop(0)
            info = music.analysis(candidate)
            if library.get(candidate) is not None and not (info and info["fresh"]):
                path = candidate
        if path is None and self.library_enabled():
            if self.pending is None:
                self.pending = music.pending_analysis()
            while self.pending and path is None:
                candidate = self.pending.pop(0)
                if library.get(candidate) is not None:
                    path = candidate
        if path is None:
            return
        row = music.db.execute("SELECT mtime_ns FROM music_meta WHERE path=?", (path,)).fetchone()
        if row is None:
            GLib.idle_add(lambda: self._next() or False)
            return
        duration = library.get(path).duration
        self.busy = path
        cancel = self.cancel
        background(lambda: analyze(path, duration, cancel=cancel),
                   lambda result, error: self._done(path, row[0], result, error))

    def _done(self, path, mtime, result, error):
        self.busy = None
        if self.page.closed:
            return
        try:
            if error is None and result:
                self.page.music.save_analysis(path, mtime, result["gain"], result["peak"], result["waveform"])
                track = self.page.library.get(path)
                if result["gain"] is not None and (track is None or track.rg_track_gain is None):
                    self.gains[path] = (result["gain"], result["peak"])
                    self._album_gains()
            else:
                # Unreadable files are remembered too, so they are not retried on every start.
                self.page.music.save_analysis(path, mtime, None, None, b"")
        except Exception:  # noqa: BLE001 - a database hiccup must not stop playback
            pass
        self.page.on_analysis(path)
        GLib.timeout_add(30, lambda: self._next() or False)

    def waveform(self, path):
        if not path or not self.setting("waveform", True):
            return []
        info = self.page.music.analysis(path)
        return from_bytes(info["waveform"]) if info and info["fresh"] else []

    # ── equalizer ──────────────────────────────────────────────────────────
    def equalizer_settings(self):
        saved = self.setting("eq", None) or {}
        return {"enabled": bool(saved.get("enabled", False)), "preset": saved.get("preset", "Plano"),
                "bands": list(saved.get("bands", FLAT))[:10]}

    def set_equalizer(self, enabled, bands, preset):
        self.store.set_setting("music.eq", {"enabled": bool(enabled), "preset": preset, "bands": list(bands)})
        self.page.player.set_equalizer(bands if enabled else FLAT)

    def apply_equalizer(self):
        settings = self.equalizer_settings()
        self.page.player.set_equalizer(settings["bands"] if settings["enabled"] else FLAT)

    def close(self):
        self.cancel.set()
