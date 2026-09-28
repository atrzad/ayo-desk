"""Ayo Música: sidebar, views and player bar, plus the controller that ties library, queue and engine."""
import os
from pathlib import Path
import random
import sqlite3
import threading
import time
import weakref

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from ... import __version__
from ...tasks import background
from .. import covers as cover_cache
from .. import m3u, tags
from ..db import MusicDB
from ..engine import Player
from ..library import AUDIO_EXTENSIONS, read_changes, scan_music_folder
from ..queue import REPEAT_CYCLE, REPEAT_OFF, SHUFFLE_OFF, SHUFFLE_TRACKS, PlayQueue
from ...widgets import confirm
from .dialogs import open_containing_folder, show_about, show_preferences, show_properties
from .equalizer import EqualizerDialog
from .integration import Integration
from .model import Library, artist_names
from .now_playing import NowPlaying
from .playback_menu import PlaybackMenu
from .playlists import PlaylistView, ask_name
from .player_bar import PlayerBar
from .queue_view import QueueView
from .sound import Sound
from .tracks import parse_payload
from .views import AlbumsView, SongsView, album_page, artist_page, artists_view, folders_view, genres_view

SECTIONS = (
    (None, (("now", "Tocando agora", "multimedia-player-symbolic"),
            ("queue", "Fila", "view-list-bullet-symbolic"))),
    ("Biblioteca", (("songs", "Músicas", "audio-x-generic-symbolic"),
                    ("albums", "Álbuns", "media-optical-cd-audio-symbolic"),
                    ("artists", "Artistas", "avatar-default-symbolic"),
                    ("genres", "Gêneros", "view-grid-symbolic"),
                    ("folders", "Pastas", "folder-music-symbolic"))),
)
BIND = GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE
SAVE_EVERY = 10.0
LONG_TRACK = 20 * 60      # resume these from where they stopped (audiobooks, podcasts, DJ sets)
SLEEP_FADE = 8.0
MAX_WATCHED_FOLDERS = 2000
WATCH_EVENTS = {Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.DELETED, Gio.FileMonitorEvent.CREATED,
                Gio.FileMonitorEvent.MOVED_IN, Gio.FileMonitorEvent.MOVED_OUT, Gio.FileMonitorEvent.RENAMED}


class MusicPage(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.store = window.store
        self.music = MusicDB(self.store)
        self.library = Library()
        self.queue = PlayQueue()
        self.tables = weakref.WeakSet()
        self.current_path = None
        self.closed = False
        self.scanning = False
        self.rescan_again = False
        self.scan_cancel = threading.Event()
        self.monitors = []
        self.monitor_timer = 0
        self.volume_timer = 0
        self.listened = 0.0
        self.last_tick = None
        self.since_save = 0.0
        self.volume_value = float(self.store.setting("music.volume", 70))
        self.muted = False
        self.sleep_mode = None      # None, minutes (int), "track" or "queue"
        self.sleep_deadline = None
        self.player = Player(self.on_state, self.on_error, self.on_queue_end, started=self.on_gapless,
                             spectrum=self.on_spectrum)
        self.player.rate = float(self.store.setting("music.rate", 1.0))
        output = self.store.setting("music.output")
        if output:
            self.player.set_output(output)

        self._install_actions()
        self._build_header()
        self.split = Adw.OverlaySplitView(vexpand=True, min_sidebar_width=210, max_sidebar_width=260,
                                          sidebar_width_fraction=0.2)
        self.split.set_sidebar(self._build_sidebar())
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.search_bar = Gtk.SearchBar(show_close_button=True)
        self.search_entry = Gtk.SearchEntry(placeholder_text="Buscar músicas, artistas, álbuns, gêneros…",
                                            hexpand=True)
        self.search_entry.connect("search-changed", self.on_search)
        clamp = Adw.Clamp(maximum_size=640, child=self.search_entry)
        self.search_bar.set_child(clamp)
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.bind_property("search-mode-enabled", self.search_button, "active", BIND)
        content.append(self.search_bar)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True, vexpand=True)
        content.append(self.stack)
        self.split.set_content(content)
        self.append(self.split)
        self.bar = PlayerBar(self)
        self.playback_menu = PlaybackMenu(self)
        self.bar.side.prepend(self.playback_menu)
        self.append(self.bar)

        self.now_view = NowPlaying(self)
        self.queue_view = QueueView(self)
        self.songs = SongsView(self)
        self.albums = AlbumsView(self)
        self.artists = artists_view(self)
        self.genres = genres_view(self)
        self.folders = folders_view(self)
        self.playlist_view = PlaylistView(self)
        for name, widget in (("now", self.now_view), ("queue", self.queue_view), ("songs", self.songs),
                             ("albums", self.albums), ("artists", self.artists), ("genres", self.genres),
                             ("folders", self.folders), ("playlist", self.playlist_view),
                             ("welcome", self._build_welcome())):
            self.stack.add_named(widget, name)
        self._add_breakpoints()
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self.on_drop)
        self.add_controller(drop)

        self.system = Integration(self)
        self.sound = Sound(self)
        self.sound.apply_equalizer()
        self.set_grayscale(self.store.setting("music.grayscale_covers", False), save=False)
        self.player.volume(self.volume_value)
        self.bar.show_volume(self.volume_value, False)
        if not self.player.available:
            self.notify("Faltam codecs de áudio. Instale gst-plugins-base, gst-plugins-good e gst-libav.")
        self.reload_library()
        self.refresh_playlists()
        self.restore_session()
        self.show_view("songs" if self.library.tracks.get_n_items() else "welcome")
        self.timer = GLib.timeout_add(500, self.tick)
        if self.store.music_folder() or self.library.tracks.get_n_items():
            self.scan()

    # ── construction ────────────────────────────────────────────────────────────
    def _install_actions(self):
        self.actions = Gio.SimpleActionGroup()
        simple = {"choose-folder": self.choose_folder, "add-files": self.add_files, "rescan": self.scan,
                  "preferences": self.show_preferences, "shortcuts": lambda: self.system.show_shortcuts(), "search": self.toggle_search,
                  "about": lambda: show_about(self.window, __version__),
                  "new-playlist": lambda: self.new_playlist([]), "import-playlist": self.import_playlist,
                  "save-queue": lambda: self.new_playlist(self.queue.items()),
                  "playlist-rename": self.rename_playlist, "playlist-delete": self.delete_playlist,
                  "playlist-export": self.export_playlist}
        for name, callback in simple.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, c=callback: c())
            self.actions.add_action(action)
        with_paths = {"play": lambda p: self.play_paths(p, 0), "play-next": self.play_next,
                      "enqueue": self.enqueue, "go-album": lambda p: self.open_album_of(self.library.get(p[0])),
                      "go-artist": lambda p: self.open_artist(self.library.get(p[0]).display_artist),
                      "properties": lambda p: show_properties(self.window, self.library.get(p[0])),
                      "open-folder": lambda p: open_containing_folder(self.window, p[0]),
                      "remove": self.remove, "new-playlist-with": self.new_playlist}
        for name, callback in with_paths.items():
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("as"))
            action.connect("activate", lambda _a, value, c=callback: c(value.unpack()))
            self.actions.add_action(action)
        for name, signature, callback in (
                ("add-to-playlist", "(xas)", lambda value: self.add_to_playlist(*value)),
                ("remove-from-playlist", "(xax)", lambda value: self.remove_from_playlist(*value))):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new(signature))
            action.connect("activate", lambda _a, value, c=callback: c(value.unpack()))
            self.actions.add_action(action)
        self.window.insert_action_group("music", self.actions)
        app = self.window.get_application()
        if app:
            app.set_accels_for_action("music.search", ["<Primary>f"])

    def _build_header(self):
        header = self.window.header
        self.sidebar_button = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", tooltip_text="Mostrar barra lateral",
                                               visible=False)
        header.pack_start(self.sidebar_button)
        self.search_button = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text="Buscar (Ctrl+F)")
        header.pack_start(self.search_button)
        menu = Gio.Menu()
        library = Gio.Menu()
        library.append("Escolher pasta de músicas…", "music.choose-folder")
        library.append("Adicionar arquivos…", "music.add-files")
        library.append("Atualizar biblioteca", "music.rescan")
        menu.append_section(None, library)
        playlists = Gio.Menu()
        playlists.append("Nova playlist…", "music.new-playlist")
        playlists.append("Importar playlist (M3U)…", "music.import-playlist")
        menu.append_section(None, playlists)
        other = Gio.Menu()
        other.append("Preferências", "music.preferences")
        other.append("Atalhos do teclado", "music.shortcuts")
        other.append("Sobre o Ayo Música", "music.about")
        menu.append_section(None, other)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Menu principal"))

    def _build_sidebar(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class("music-sidebar")
        self.navigation = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.navigation.add_css_class("navigation-sidebar")
        self.rows = {}
        for section, entries in SECTIONS:
            for key, title, icon in entries:
                row = self._sidebar_row(key, title, icon, section)
                self.rows[key] = row
                self.navigation.append(row)

        def header(row, before):
            if row.section and (before is None or before.section != row.section):
                title = Gtk.Label(label=row.section, xalign=0, margin_start=18, margin_top=14, margin_bottom=4)
                title.add_css_class("heading")
                title.add_css_class("dim-label")
                row.set_header(title)
            else:
                row.set_header(None)
        self.navigation.set_header_func(header)
        self.playlist_rows = []
        self.navigation.connect("row-selected", self._row_selected)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.navigation)
        box.append(scroll)
        status = Gtk.Box(spacing=8, margin_start=14, margin_end=8, margin_top=6, margin_bottom=10)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.status_title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.status_title.add_css_class("caption-heading")
        self.status_detail = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.status_detail.add_css_class("caption")
        self.status_detail.add_css_class("dim-label")
        texts.append(self.status_title)
        texts.append(self.status_detail)
        status.append(texts)
        self.spinner = Adw.Spinner(visible=False)
        status.append(self.spinner)
        self.rescan_button = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Atualizar biblioteca",
                                        valign=Gtk.Align.CENTER)
        self.rescan_button.add_css_class("flat")
        self.rescan_button.connect("clicked", lambda _b: self.scan())
        status.append(self.rescan_button)
        box.append(Gtk.Separator())
        box.append(status)
        return box

    def _build_welcome(self):
        page = Adw.StatusPage(icon_name="folder-music-symbolic", title="Sua biblioteca começa aqui",
                              description="Escolha a pasta onde ficam suas músicas. O Ayo Música lê as subpastas, "
                                          "as tags e as capas — e acompanha as mudanças sozinho.")
        buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER)
        choose = Gtk.Button(label="Escolher pasta de músicas")
        choose.add_css_class("pill")
        choose.add_css_class("suggested-action")
        choose.connect("clicked", lambda _b: self.choose_folder())
        buttons.append(choose)
        music = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_MUSIC) or str(Path.home() / "Music")
        if Path(music).is_dir():
            use = Gtk.Button(label=f"Usar {music.replace(str(Path.home()), '~')}")
            use.add_css_class("pill")
            use.connect("clicked", lambda _b: self.scan(music))
            buttons.append(use)
        buttons.append(Gtk.Label(label="Você também pode arrastar arquivos e pastas para esta janela.",
                                 css_classes=["dim-label", "caption"]))
        page.set_child(buttons)
        return page

    def _add_breakpoints(self):
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 820sp"))
        narrow.add_setter(self.split, "collapsed", True)
        narrow.add_setter(self.sidebar_button, "visible", True)
        narrow.add_setter(self.bar.side, "visible", False)
        narrow.add_setter(self.bar.center, "width-request", 260)
        compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 560sp"))
        compact.add_setter(self.bar.center, "width-request", 170)
        compact.add_setter(self.now_view.cover, "size", 220)
        compact.add_setter(self.bar.elapsed, "visible", False)
        compact.add_setter(self.bar.remaining, "visible", False)
        compact.add_setter(self.split, "collapsed", True)
        compact.add_setter(self.sidebar_button, "visible", True)
        compact.add_setter(self.bar.side, "visible", False)
        compact.add_setter(self.bar.favorite, "visible", False)
        compact.add_setter(self.bar.shuffle, "visible", False)
        compact.add_setter(self.bar.repeat, "visible", False)
        self.window.add_breakpoint(narrow)
        self.window.add_breakpoint(compact)
        self.split.bind_property("show-sidebar", self.sidebar_button, "active", BIND)

    def _sidebar_row(self, key, title, icon, section):
        row = Gtk.ListBoxRow()
        content = Gtk.Box(spacing=12, margin_start=6, margin_end=6, margin_top=8, margin_bottom=8)
        content.append(Gtk.Image(icon_name=icon))
        label = Gtk.Label(label=title, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        content.append(label)
        row.set_child(content)
        row.view, row.section, row.label = key, section, label
        return row

    def _row_selected(self, _list, row):
        if row is None:
            return
        if row.view == "new-playlist":
            self.new_playlist([])
            current = self.rows.get(self.stack.get_visible_child_name())
            if self.stack.get_visible_child_name() == "playlist":
                current = self.rows.get(f"playlist:{self.playlist_view.playlist_id}")
            if current is not None:
                self.navigation.select_row(current)
            else:
                self.navigation.unselect_all()
            return
        self.show_view(row.view)

    def refresh_playlists(self):
        """Rebuild the "Playlists" section of the sidebar (names, counts, drop targets)."""
        for row in self.playlist_rows:
            self.rows.pop(row.view, None)
            self.navigation.remove(row)
        self.playlist_rows = []
        for playlist in self.music.playlists():
            key = f"playlist:{playlist['id']}"
            row = self._sidebar_row(key, playlist["name"], "media-playlist-consecutive-symbolic", "Playlists")
            count = Gtk.Label(label=str(playlist["count"]))
            count.add_css_class("dim-label")
            count.add_css_class("caption")
            row.get_child().append(count)
            row.set_tooltip_text(f"{playlist['name']} — solte músicas aqui para adicionar")
            drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.COPY)
            drop.connect("drop", lambda _t, value, _x, _y, pid=playlist["id"]: self._drop_on_playlist(pid, value))
            row.add_controller(drop)
            self.rows[key] = row
            self.playlist_rows.append(row)
            self.navigation.append(row)
        new = self._sidebar_row("new-playlist", "Nova playlist", "list-add-symbolic", "Playlists")
        new.add_css_class("new-playlist-row")
        self.playlist_rows.append(new)
        self.navigation.append(new)
        if self.stack.get_visible_child_name() == "playlist":
            row = self.rows.get(f"playlist:{self.playlist_view.playlist_id}")
            if row is not None:
                self.navigation.select_row(row)

    def _drop_on_playlist(self, playlist_id, value):
        payload = parse_payload(value)
        if payload is None:
            return False
        self.add_to_playlist(playlist_id, payload[2])
        return True

    # ── navigation ──────────────────────────────────────────────────────────────
    def show_view(self, name):
        if name.startswith("playlist:"):
            self.playlist_view.show(int(name.split(":", 1)[1]))
            if self.playlist_view.playlist_id is None:
                return
            self.stack.set_visible_child_name("playlist")
        elif self.stack.get_child_by_name(name) is None:
            return
        else:
            self.stack.set_visible_child_name(name)
        row = self.rows.get(name)
        if row is not None and self.navigation.get_selected_row() is not row:
            self.navigation.select_row(row)
        if name == "welcome":
            self.navigation.unselect_all()
        if name == "queue":
            self.queue_view.render(self.queue, self.library)
        self._update_spectrum()
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)

    def toggle_search(self):
        self.search_bar.set_search_mode(not self.search_bar.get_search_mode())
        if self.search_bar.get_search_mode():
            self.search_entry.grab_focus()

    def on_search(self, entry):
        text = entry.get_text()
        self.songs.search(text)
        if text.strip():
            self.show_view("songs")

    def open_album(self, group):
        if group is None:
            return
        self.show_view("albums")
        self.albums.pop_to_tag("albums")
        self.albums.push(album_page(self, group))

    def open_album_of(self, track):
        if track is not None:
            self.open_album(self.library.album_by_key.get(track.album_key))

    def open_artist(self, name):
        if not name:
            return
        group = self.library.artist_by_name.get(tags.fold(artist_names(name)[0]))
        if group is None:
            return
        self.show_view("artists")
        self.artists.pop_to_tag("list")
        self.artists.push(artist_page(self, group))

    def show_track_menu(self, tracks, widget, x, y, table=None):
        path_list = [track.path for track in tracks]
        paths = GLib.Variant("as", path_list)
        menu = Gio.Menu()
        play = Gio.Menu()
        for title, action in (("Tocar", "music.play"), ("Tocar a seguir", "music.play-next"),
                              ("Adicionar à fila", "music.enqueue")):
            item = Gio.MenuItem.new(title, None)
            item.set_action_and_target_value(action, paths)
            play.append_item(item)
        menu.append_section(None, play)
        playlists = Gio.Menu()
        choices = Gio.Menu()
        for playlist in self.music.playlists():
            if playlist["kind"] != "manual":
                continue
            item = Gio.MenuItem.new(playlist["name"], None)
            item.set_action_and_target_value("music.add-to-playlist",
                                             GLib.Variant("(xas)", (playlist["id"], path_list)))
            choices.append_item(item)
        new = Gio.Menu()
        item = Gio.MenuItem.new("Nova playlist…", None)
        item.set_action_and_target_value("music.new-playlist-with", paths)
        new.append_item(item)
        choices.append_section(None, new)
        playlists.append_submenu("Adicionar à playlist", choices)
        if table is not None and table.playlist_id is not None:
            item = Gio.MenuItem.new("Remover desta playlist", None)
            item.set_action_and_target_value("music.remove-from-playlist",
                                             GLib.Variant("(xax)", (table.playlist_id, table.selected_positions())))
            playlists.append_item(item)
        menu.append_section(None, playlists)
        if len(tracks) == 1:
            go = Gio.Menu()
            for title, action in (("Ir para o álbum", "music.go-album"), ("Ir para o artista", "music.go-artist"),
                                  ("Propriedades", "music.properties"), ("Abrir pasta", "music.open-folder")):
                item = Gio.MenuItem.new(title, None)
                item.set_action_and_target_value(action, paths)
                go.append_item(item)
            menu.append_section(None, go)
        remove = Gio.Menu()
        item = Gio.MenuItem.new("Remover da biblioteca" if len(tracks) == 1 else
                                f"Remover {len(tracks)} músicas da biblioteca", None)
        item.set_action_and_target_value("music.remove", paths)
        remove.append_item(item)
        menu.append_section(None, remove)
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(widget)
        popover.set_has_arrow(False)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: GLib.idle_add(lambda: p.unparent() or False))
        popover.popup()

    def register_table(self, table):
        self.tables.add(table)

    def refresh_tables(self, *paths):
        for table in list(self.tables):
            table.refresh(paths)

    # ── playback ────────────────────────────────────────────────────────────────
    def album_of(self, path):
        track = self.library.get(path)
        return track.album_key if track else os.path.dirname(path)

    def play_paths(self, paths, index=0, shuffle=None):
        if not paths:
            return
        if shuffle is not None:
            self.queue.set_shuffle(SHUFFLE_TRACKS if shuffle else SHUFFLE_OFF, self.album_of)
            if shuffle:
                index = random.randrange(len(paths))
        self.start(self.queue.replace(paths, index))

    def play_all(self, shuffle=False):
        paths = [self.library.tracks.get_item(n).path for n in range(self.library.tracks.get_n_items())]
        self.play_paths(paths, 0, shuffle=shuffle)

    def start(self, path, play=True, position=0.0, automatic=False):
        self.finish_listen(automatic)
        self.save_resume()
        if path is None:
            self.stop_playback()
            return
        track = self.library.get(path)
        if not position and track and track.duration >= LONG_TRACK and self.store.setting("music.resume_long", True):
            saved = self.music.resume_position(path)
            if 30 < saved < track.duration - 30:
                position = saved
        try:
            self.player.load(path, play=play, start=position, gain=self.sound.gain_for(path))
        except ValueError as exc:
            self.notify(str(exc))
            self.stop_playback()
            return
        self.track_started(path, announce=play)

    def track_started(self, path, announce=True):
        previous, self.current_path = self.current_path, path
        self.listened, self.last_tick = 0.0, time.monotonic()
        self.refresh_tables(previous, path)
        upcoming = self.upcoming()
        self.player.set_next(upcoming, self.sound.gain_for(upcoming))
        self.bar.seekbar.set_levels(self.sound.waveform(path))
        self.sound.request(path, self.next_candidate())
        track = self.library.get(path)
        self.bar.show_track(track, self.player)
        self.bar.show_modes(self.queue.shuffle, self.queue.repeat)
        self.now_view.show_track(track, self.player)
        if self.stack.get_visible_child_name() == "queue":
            self.queue_view.render(self.queue, self.library)
        self.save_session()
        self.system.changed()
        if announce:
            self.system.notify_track(track)

    def next_candidate(self):
        """What plays next on its own, honouring the sleep timer."""
        if self.sleep_mode == "track":
            return None
        if self.sleep_mode == "queue" and self.queue.index + 1 >= len(self.queue):
            return None
        return self.queue.peek()

    def upcoming(self):
        """What follows without a gap; None when it will crossfade instead (see tick)."""
        candidate = self.next_candidate()
        track = self.library.get(self.current_path) if self.current_path else None
        if candidate and self.sound.crossfade_for(self.current_path, candidate, track.duration if track else 0):
            return None
        return candidate

    def begin_crossfade(self, path, seconds):
        self.finish_listen(automatic=True)
        if self.current_path:
            self.music.set_resume_position(self.current_path, 0)
        if self.queue.advance(automatic=True) != path:
            return
        try:
            self.player.crossfade_to(path, seconds, gain=self.sound.gain_for(path))
        except ValueError as exc:
            self.notify(str(exc))
            self.start(self.queue.advance(automatic=True), automatic=True)
            return
        self.track_started(path)

    def stop_playback(self):
        previous = self.current_path
        self.save_resume()
        self.player.stop()
        self.current_path = None
        self.refresh_tables(previous)
        self.bar.show_track(None, self.player)
        self.now_view.show_track(None, self.player)
        self.save_session()
        self.system.changed()
        if not self.window.get_visible() and not self.closed:
            self.window.quit_app()  # hidden and nothing left to play: leave for real

    def on_gapless(self, path):
        """The engine already moved to the prepared track; bring the queue along."""
        self.finish_listen(automatic=True)
        if self.queue.advance(automatic=True) != path:
            upcoming = [position for position, item in self.queue.upcoming() if item == path]
            if upcoming:
                self.queue.jump(upcoming[0])
        self.track_started(path)

    def on_queue_end(self):
        self.finish_listen(automatic=True)
        if self.current_path:
            self.music.set_resume_position(self.current_path, 0)
        mode = self.sleep_mode
        if mode == "queue" and self.queue.index + 1 >= len(self.queue):
            self.set_sleep(None)
            self.queue.index = len(self.queue)
            self.stop_playback()
            return
        path = self.queue.advance(automatic=True)
        if mode == "track":
            self.set_sleep(None)
            self.start(path, play=False, automatic=True)  # ready at the next track, paused
        elif path:
            self.start(path, automatic=True)
        else:
            self.stop_playback()

    def on_state(self):
        self.bar.show_state(self.player)
        self.system.changed("PlaybackStatus", "CanPlay", "CanSeek")

    def on_error(self, text):
        self.notify(f"Não foi possível tocar esta música: {text}")

    def toggle(self):
        if self.player.path:
            self.player.toggle(fade=0.18 if self.store.setting("music.smooth_pause", True) else 0.0)
        elif self.queue.current:
            self.start(self.queue.current)
        elif self.library.tracks.get_n_items():
            self.play_all()
        else:
            self.choose_folder()

    def next(self):
        if len(self.queue):
            self.start(self.queue.advance(automatic=False))

    def previous(self):
        if self.player.position()[0] > 3:
            self.player.seek(0)
        elif len(self.queue):
            self.start(self.queue.back())

    def jump(self, position):
        self.start(self.queue.jump(position))

    def seek(self, seconds):
        if self.player.seek(seconds):
            self.system.seeked(seconds)

    def set_rate(self, rate):
        self.player.set_rate(rate)
        self.store.set_setting("music.rate", self.player.rate)
        self.system.changed("Rate")

    def set_output(self, device):
        try:
            self.player.set_output(device)
        except ValueError as exc:
            self.notify(str(exc))
        self.store.set_setting("music.output", device)

    def set_sleep(self, mode):
        self.sleep_mode = mode
        self.sleep_deadline = time.monotonic() + mode * 60 if isinstance(mode, int) else None
        self.player.set_next(self.upcoming())
        if mode:
            self.notify(self.sleep_text())

    def sleep_text(self):
        if self.sleep_mode == "track":
            return "A música para no fim desta faixa."
        if self.sleep_mode == "queue":
            return "A música para no fim da fila."
        if self.sleep_deadline:
            remaining = max(0, self.sleep_deadline - time.monotonic())
            at = GLib.DateTime.new_now_local().add_seconds(remaining).format("%H:%M")
            return f"A música para às {at} (em {max(1, round(remaining / 60))} min)."
        return ""

    def _sleep_now(self):
        """Fade out gently, pause, and restore the volume for next time."""
        self.sleep_mode = self.sleep_deadline = None
        self.player.set_next(self.upcoming())
        self.player.set_playing(False, fade=SLEEP_FADE)
        self.playback_menu.show_sleep()

    def set_volume(self, value):
        self.volume_value = value
        self.player.volume(value)
        self.bar.show_volume(value, self.muted)
        self.system.changed("Volume")
        if self.volume_timer:
            GLib.source_remove(self.volume_timer)
        self.volume_timer = GLib.timeout_add(600, self._save_volume)

    def _save_volume(self):
        self.volume_timer = 0
        self.store.set_setting("music.volume", round(self.volume_value))
        return GLib.SOURCE_REMOVE

    def toggle_mute(self):
        self.muted = not self.muted
        self.player.set_muted(self.muted)
        self.bar.show_volume(self.volume_value, self.muted)

    def set_shuffle(self, active):
        self.set_shuffle_mode(SHUFFLE_TRACKS if active else SHUFFLE_OFF)

    def set_shuffle_mode(self, mode):
        self.queue.set_shuffle(mode, self.album_of)
        self._queue_changed()

    def move_in_queue(self, source, target):
        self.queue.move(source, target)
        self._queue_changed()

    def cycle_repeat(self):
        self.set_repeat(REPEAT_CYCLE[self.queue.repeat])

    def set_repeat(self, mode):
        self.queue.set_repeat(mode if mode in REPEAT_CYCLE else REPEAT_OFF)
        self._queue_changed()

    def _queue_changed(self):
        self.player.set_next(self.upcoming())
        self.bar.show_modes(self.queue.shuffle, self.queue.repeat)
        if self.stack.get_visible_child_name() == "queue":
            self.queue_view.render(self.queue, self.library)
        self.save_session()
        self.system.changed("LoopStatus", "Shuffle", "CanGoNext", "CanGoPrevious")

    def play_next(self, paths):
        if not self.player.path and not len(self.queue):
            self.play_paths(paths)
            return
        self.queue.play_next(paths)
        self._queue_changed()
        self.notify("Vai tocar em seguida" if len(paths) == 1 else f"{len(paths)} músicas vão tocar em seguida")

    def enqueue(self, paths):
        if not self.player.path and not len(self.queue):
            self.play_paths(paths)
            return
        self.queue.enqueue(paths)
        self._queue_changed()
        self.notify("Adicionada à fila" if len(paths) == 1 else f"{len(paths)} músicas adicionadas à fila")

    def remove_from_queue(self, position):
        self.queue.remove(position)
        self._queue_changed()

    def clear_upcoming(self):
        self.queue.clear_upcoming()
        self._queue_changed()

    def set_favorite(self, path, favorite):
        track = self.library.get(path)
        if track is None:
            return
        self.music.set_favorite(path, favorite)
        track.favorite = bool(favorite)
        self.refresh_tables(path)

    def finish_listen(self, automatic):
        """Count a play after half the track or 4 minutes (like scrobblers); a manual early skip is a skip."""
        path, listened = self.current_path, self.listened
        self.listened = 0.0
        if not path or listened <= 0:
            return
        track = self.library.get(path)
        duration = (track.duration if track else 0) or self.player.position()[1]
        threshold = min(240.0, duration / 2) if duration > 0 else 240.0
        try:
            if listened >= threshold:
                self.music.record_play(path, listened)
                if track:
                    track.plays += 1
            elif not automatic:
                self.music.record_skip(path)
                if track:
                    track.skips = (track.skips or 0) + 1
        except sqlite3.Error:
            return
        self.refresh_tables(path)

    def tick(self):
        now = time.monotonic()
        if self.player.playing and self.last_tick is not None:
            elapsed = min(2.0, now - self.last_tick)
            self.listened += elapsed
            self.since_save += elapsed
        self.last_tick = now
        position, duration = self.player.position()
        self.bar.show_position(position, duration)
        if self.player.playing and not self.player.crossfading and duration > 0:
            candidate = self.next_candidate()
            seconds = self.sound.crossfade_for(self.current_path, candidate, duration)
            remaining = (duration - position) / max(0.5, self.player.rate)
            if seconds and 0 < remaining <= seconds + 0.5:
                self.begin_crossfade(candidate, max(0.5, remaining - 0.1))
        if self.since_save >= SAVE_EVERY:
            self.since_save = 0.0
            self.save_position(position)
            self.save_resume(position)
        if self.sleep_deadline and now >= self.sleep_deadline:
            self._sleep_now()
        return GLib.SOURCE_CONTINUE

    def save_resume(self, position=None):
        """Remember where long tracks stopped (set back to 0 when they end)."""
        track = self.library.get(self.current_path) if self.current_path else None
        if track is None or track.duration < LONG_TRACK:
            return
        if position is None:
            position = self.player.position()[0]
        try:
            self.music.set_resume_position(track.path, position if position < track.duration - 30 else 0)
        except sqlite3.Error:
            pass

    # ── session ─────────────────────────────────────────────────────────────────
    def save_position(self, position=None):
        if position is None:
            position = self.player.position()[0]
        self.store.set_setting("music.position", {"path": self.current_path, "seconds": round(position, 1)})

    def save_session(self):
        try:
            self.store.set_setting("music.queue", self.queue.to_dict())
            self.save_position()
        except sqlite3.Error:
            pass

    def restore_session(self):
        saved = self.store.setting("music.queue")
        if not saved:
            return
        current = self.queue.restore(saved, exists=os.path.isfile)
        self.bar.show_modes(self.queue.shuffle, self.queue.repeat)
        if not current or not self.player.available:
            return
        position = self.store.setting("music.position", {}) or {}
        seconds = position.get("seconds", 0) if position.get("path") == current else 0
        try:
            self.player.load(current, play=False, start=seconds, gain=self.sound.gain_for(current))
        except ValueError:
            return
        self.track_started(current, announce=False)

    # ── playlists ───────────────────────────────────────────────────────────────
    def show_playlist(self, playlist_id):
        self.show_view(f"playlist:{playlist_id}")

    def new_playlist(self, paths):
        def create(name):
            try:
                playlist_id = self.music.create_playlist(name, paths)
            except (ValueError, sqlite3.Error) as exc:
                self.notify(str(exc))
                return
            self.refresh_playlists()
            self.show_playlist(playlist_id)
            if paths:
                self.notify(f"Playlist criada com {len(paths)} música" + ("s" if len(paths) != 1 else ""))
        ask_name(self.window, "Nova playlist", create)

    def add_to_playlist(self, playlist_id, paths, at=None):
        try:
            added = self.music.add_to_playlist(playlist_id, paths, at=at)
            name = self.music.playlist(playlist_id)["name"]
        except (ValueError, sqlite3.Error) as exc:
            self.notify(str(exc))
            return
        self.refresh_playlists()
        if self.playlist_view.playlist_id == playlist_id:
            self.playlist_view.reload()
        if added == 0:
            self.notify(f"Já está em “{name}”")
        else:
            self.notify(f"Adicionada a “{name}”" if added == 1 else f"{added} músicas adicionadas a “{name}”")

    def remove_from_playlist(self, playlist_id, positions):
        self.music.remove_from_playlist(playlist_id, positions)
        self.refresh_playlists()
        if self.playlist_view.playlist_id == playlist_id:
            self.playlist_view.reload()

    def rename_playlist(self):
        playlist_id = self.playlist_view.playlist_id
        if playlist_id is None:
            return

        def rename(name):
            try:
                self.music.rename_playlist(playlist_id, name)
            except (ValueError, sqlite3.Error) as exc:
                self.notify(str(exc))
                return
            self.refresh_playlists()
            self.playlist_view.reload()
        ask_name(self.window, "Renomear playlist", rename, self.music.playlist(playlist_id)["name"], "Renomear")

    def delete_playlist(self):
        playlist_id = self.playlist_view.playlist_id
        if playlist_id is None:
            return
        name = self.music.playlist(playlist_id)["name"]

        def delete():
            self.music.delete_playlist(playlist_id)
            self.playlist_view.playlist_id = None
            self.refresh_playlists()
            self.show_view("songs")
        confirm(self.window, f"Excluir “{name}”?", "As músicas continuam na biblioteca e no disco; "
                "só a playlist é apagada.", delete, "Excluir", destructive=True)

    def export_playlist(self):
        playlist_id = self.playlist_view.playlist_id
        if playlist_id is None:
            return
        name = self.music.playlist(playlist_id)["name"]
        dialog = Gtk.FileDialog(title="Exportar playlist", initial_name=f"{name}.m3u8")
        folder = self.store.music_folder()
        if folder and Path(folder).is_dir():
            dialog.set_initial_folder(Gio.File.new_for_path(folder))

        def chosen(source, result):
            try:
                target = source.save_finish(result).get_path()
            except GLib.Error as exc:
                if not exc.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                    self.notify(str(exc))
                return
            entries = []
            for path in self.music.playlist_paths(playlist_id):
                track = self.library.get(path)
                entries.append({"path": path, "duration": track.duration if track else None,
                                "artist": track.display_artist if track else None,
                                "title": track.title if track else None})
            try:
                m3u.write(target, entries)
            except OSError as exc:
                self.notify(f"Não foi possível salvar a playlist: {exc}")
                return
            self.notify(f"Playlist exportada para {Path(target).name}")
        dialog.save(self.window, None, chosen)

    def import_playlist(self):
        dialog = Gtk.FileDialog(title="Importar playlist")
        lists = Gtk.FileFilter(name="Playlists (M3U, M3U8, PLS)")
        for suffix in ("m3u", "m3u8", "pls"):
            lists.add_suffix(suffix)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(lists)
        dialog.set_filters(filters)
        dialog.set_default_filter(lists)

        def chosen(source, result):
            try:
                path = source.open_finish(result).get_path()
            except GLib.Error as exc:
                if not exc.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                    self.notify(str(exc))
                return
            background(lambda: (Path(path).stem, m3u.read(path)), self._playlist_read)
        dialog.open(self.window, None, chosen)

    def _playlist_read(self, result, error):
        if self.closed:
            return
        if error:
            self.notify(f"Não foi possível ler a playlist: {error}")
            return
        name, entries = result
        found = [e for e in entries if "://" in e or Path(e).is_file()]
        outside = [e for e in found if "://" not in e and self.library.get(e) is None]
        if outside:
            self.store.add_tracks(outside)  # songs from elsewhere join the library, with tags
            self.scan()
        playlist_id = self.music.create_playlist(name, found)
        self.refresh_playlists()
        self.show_playlist(playlist_id)
        missing = len(entries) - len(found)
        self.notify(f"{len(found)} música" + ("s importadas" if len(found) != 1 else " importada")
                    + (f" · {missing} não encontrada" + ("s" if missing != 1 else "") if missing else ""))

    # ── library ─────────────────────────────────────────────────────────────────
    def reload_library(self):
        self.library.load(self.music.library(), root=self.store.music_folder())
        self.sound.reload_gains()
        self.update_status()
        if self.playlist_view.playlist_id is not None:
            self.playlist_view.reload()
        if self.current_path:
            self.bar.show_track(self.library.get(self.current_path), self.player)
            self.now_view.show_track(self.library.get(self.current_path), self.player)

    def update_status(self, detail=None):
        folder = self.store.music_folder()
        count = self.library.tracks.get_n_items()
        self.status_title.set_text(f"{count} música" + ("s" if count != 1 else ""))
        self.status_detail.set_text(detail or (folder.replace(str(Path.home()), "~") if folder else "Sem pasta"))
        self.status_detail.set_tooltip_text(folder)
        self.spinner.set_visible(self.scanning)
        self.rescan_button.set_sensitive(not self.scanning)

    def scan(self, folder=None):
        """Find files, read tags only where something changed, then refresh every view."""
        if self.closed:
            return
        if self.scanning:
            self.rescan_again = True
            return
        folder = folder or self.store.music_folder()
        manual = [row[0] for row in self.store.db.execute("SELECT path FROM tracks ORDER BY id")]
        known = self.music.signatures()
        self.scanning = True
        self.scan_cancel.clear()
        self.update_status("Procurando músicas…")
        cancel = self.scan_cancel

        def progress(done, total):
            GLib.idle_add(lambda: (not self.closed and self.update_status(f"Lendo tags: {done} de {total}")) and False)

        def work():
            root, found = scan_music_folder(folder, cancel) if folder else (None, [])
            paths = list(dict.fromkeys([*manual, *found]))
            return root, found, read_changes(paths, known, root=root, cancel=cancel, progress=progress)

        background(work, self._scanned)

    def _scanned(self, result, error):
        if self.closed:
            return
        self.scanning = False
        if error:
            self.update_status("Falha ao ler a pasta")
            self.notify(error)
        else:
            root, found, changed = result
            try:
                if root:
                    self.store.sync_music_folder(root, found)
                self.music.save_meta(changed)
                self.music.prune_meta(self.store.tracks())
            except sqlite3.Error as exc:
                self.notify(f"Não foi possível salvar a biblioteca: {exc}")
            else:
                keep = self.music.cover_keys()
                background(lambda: cover_cache.prune(keep), lambda *_: None)
                self.reload_library()
                self.watch_folder()
                self.sound.start_library()
                if self.library.tracks.get_n_items() and self.stack.get_visible_child_name() == "welcome":
                    self.show_view("albums")
        self.update_status()
        if self.rescan_again:
            self.rescan_again = False
            self.scan()

    def choose_folder(self):
        dialog = Gtk.FileDialog(title="Escolher a pasta de músicas", accept_label="Usar esta pasta")
        folder = self.store.music_folder()
        start = folder if folder and Path(folder).is_dir() else GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_MUSIC)
        if start and Path(start).is_dir():
            dialog.set_initial_folder(Gio.File.new_for_path(start))

        def selected(source, result):
            try:
                chosen = source.select_folder_finish(result)
            except GLib.Error as exc:
                if not exc.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                    self.notify(str(exc))
                return
            if self.closed:
                return
            path = chosen.get_path()
            if not path:
                self.notify("Escolha uma pasta local ou uma unidade montada neste computador.")
                return
            self.scan(path)
        dialog.select_folder(self.window, None, selected)

    def add_files(self):
        dialog = Gtk.FileDialog(title="Adicionar músicas")
        audio = Gtk.FileFilter(name="Arquivos de áudio")
        audio.add_mime_type("audio/*")
        for extension in sorted(AUDIO_EXTENSIONS):
            audio.add_suffix(extension.lstrip("."))
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(audio)
        dialog.set_filters(filters)
        dialog.set_default_filter(audio)

        def selected(source, result):
            try:
                files = source.open_multiple_finish(result)
            except GLib.Error as exc:
                if not exc.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                    self.notify(str(exc))
                return
            paths = [files.get_item(i).get_path() for i in range(files.get_n_items())]
            self.add_paths([p for p in paths if p])
        dialog.open_multiple(self.window, None, selected)

    def add_paths(self, paths):
        """Files and folders chosen or dropped: folders are searched recursively, in the background."""
        def work():
            found = []
            for path in paths:
                if Path(path).is_dir():
                    found += scan_music_folder(path)[1]
                elif Path(path).suffix.casefold() in AUDIO_EXTENSIONS and Path(path).is_file():
                    found.append(str(Path(path).resolve()))
            return found

        def done(found, error):
            if self.closed:
                return
            if error or not found:
                self.notify(error or "Nenhum arquivo de áudio encontrado.")
                return
            self.store.add_tracks(found)
            self.notify(f"{len(found)} música" + ("s adicionadas" if len(found) != 1 else " adicionada"))
            self.scan()
        background(work, done)

    def on_drop(self, _target, value, _x, _y):
        paths = [file.get_path() for file in value.get_files() if file.get_path()]
        if paths:
            self.add_paths(paths)
        return bool(paths)

    def remove(self, paths):
        for path in paths:
            self.store.remove_track(path)
            if path != self.current_path:
                self.queue.remove_path(path)
        self.music.prune_meta(self.store.tracks())
        self.reload_library()
        toast = Adw.Toast(title="Removida da biblioteca" if len(paths) == 1 else f"{len(paths)} músicas removidas",
                          button_label="Desfazer", timeout=6)
        toast.connect("button-clicked", lambda _t: (self.store.add_tracks(paths), self.scan()))
        self.window.overlay.add_toast(toast)

    def watch_folder(self):
        for monitor in self.monitors:
            monitor.cancel()
        self.monitors = []
        folder = self.store.music_folder()
        if not folder or not self.store.setting("music.watch_folder", True) or not Path(folder).is_dir():
            return
        folders = {folder}
        for position in range(self.library.tracks.get_n_items()):
            parent = os.path.dirname(self.library.tracks.get_item(position).path)
            while parent.startswith(folder) and parent not in folders:
                folders.add(parent)
                parent = os.path.dirname(parent)
        for path in sorted(folders)[:MAX_WATCHED_FOLDERS]:
            try:
                monitor = Gio.File.new_for_path(path).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
            except GLib.Error:
                continue
            monitor.connect("changed", self.on_folder_changed)
            self.monitors.append(monitor)

    def on_folder_changed(self, _monitor, file, _other, event):
        if event not in WATCH_EVENTS:
            return
        name = file.get_basename() or ""
        if name.startswith("."):
            return
        if self.monitor_timer:
            GLib.source_remove(self.monitor_timer)
        self.monitor_timer = GLib.timeout_add_seconds(3, self._folder_settled)

    def _folder_settled(self):
        self.monitor_timer = 0
        self.scan()
        return GLib.SOURCE_REMOVE

    def set_watch(self, active):
        self.store.set_setting("music.watch_folder", bool(active))
        self.watch_folder()

    def set_grayscale(self, active, save=True):
        if active:
            self.add_css_class("bw-covers")
        else:
            self.remove_css_class("bw-covers")
        if save:
            self.store.set_setting("music.grayscale_covers", bool(active))

    # ── sound ───────────────────────────────────────────────────────────────────
    def on_analysis(self, path):
        """A track was measured: show its waveform and, if it just started, level it now."""
        if path == self.current_path:
            self.bar.seekbar.set_levels(self.sound.waveform(path))
            if self.player.position()[0] < 10:
                self.player.set_gain(self.sound.gain_for(path))
        if path == self.next_candidate():
            upcoming = self.upcoming()
            self.player.set_next(upcoming, self.sound.gain_for(upcoming))

    def on_spectrum(self, levels, endtime):
        self.now_view.visualizer.push(levels, endtime)

    def set_visualizer(self, active):
        self.store.set_setting("music.visualizer", bool(active))
        self._update_spectrum()

    def _update_spectrum(self):
        active = self.store.setting("music.visualizer", False)
        self.now_view.visualizer_revealer.set_reveal_child(active)
        showing = active and self.stack.get_visible_child_name() == "now"
        self.player.set_spectrum(showing)
        if not showing:
            self.now_view.visualizer.clear()

    def show_equalizer(self):
        EqualizerDialog(self).present(self.window)

    def equalizer_settings(self):
        return self.sound.equalizer_settings()

    def set_equalizer(self, enabled, bands, preset):
        self.sound.set_equalizer(enabled, bands, preset)

    def apply_sound_settings(self):
        """Preferences changed: re-level the current and next track and re-plan the transition."""
        if self.current_path:
            self.player.set_gain(self.sound.gain_for(self.current_path))
            self.bar.seekbar.set_levels(self.sound.waveform(self.current_path))
        upcoming = self.upcoming()
        self.player.set_next(upcoming, self.sound.gain_for(upcoming))
        self.sound.start_library()

    # ── window, files and preferences ──────────────────────────────────────────
    def present(self):
        self.window.present()

    def show_preferences(self):
        show_preferences(self.window, self)

    def open_files(self, paths):
        """Files opened from a file manager or the command line: play them right away."""
        wanted = [p for p in paths if p and ("://" in p or Path(p).is_file() or Path(p).is_dir())]
        if not wanted:
            return

        def work():
            files = []
            for path in wanted:
                if "://" in path:
                    files.append(path)
                elif Path(path).is_dir():
                    files += scan_music_folder(path)[1]
                elif Path(path).suffix.casefold() in AUDIO_EXTENSIONS:
                    files.append(str(Path(path).resolve()))
            known = {p for p in files if self.library.get(p)}
            return files, read_changes([p for p in files if p not in known and "://" not in p], {})

        def done(result, error):
            if self.closed:
                return
            if error or not result or not result[0]:
                self.notify(error or "Nenhum arquivo de áudio para tocar.")
                return
            files, metas = result
            self.library.add_external(metas)
            self.play_paths(files, 0, shuffle=False)
            self.show_view("now")
        background(work, done)

    def keep_running(self):
        """Closing the window keeps the music going (MPRIS and media keys still work)."""
        return bool(self.player.playing and self.store.setting("music.keep_playing", True))

    # ── page protocol used by app.Window ───────────────────────────────────────
    def notify(self, text):
        self.window.notify(text)

    def on_show(self):
        pass

    def close(self):
        if self.closed:
            return
        self.finish_listen(automatic=False)
        self.save_resume()
        self.save_session()
        self.system.close()
        self.sound.close()
        if self.volume_timer:
            self._save_volume()
        self.closed = True
        self.scan_cancel.set()
        for monitor in self.monitors:
            monitor.cancel()
        for source in (self.timer, self.monitor_timer):
            if source:
                GLib.source_remove(source)
        self.player.close()
