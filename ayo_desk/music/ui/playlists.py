"""Playlist page and the small dialogs used to name, rename and delete playlists."""
from gi.repository import Adw, Gio, Gtk

from .. import tags
from .model import Track, album_key
from .tracks import TrackTable
from .views import Header, count_text, play_buttons

PLAYLIST_COLUMNS = ("index", "title", "artist", "album", "duration", "plays", "rating")


def ask_name(parent, heading, action, initial="", confirm="Criar"):
    """Adw.AlertDialog with a text entry; `action(name)` runs only for a non-empty name."""
    dialog = Adw.AlertDialog(heading=heading)
    entry = Gtk.Entry(text=initial, placeholder_text="Nome da playlist", activates_default=True)
    dialog.set_extra_child(entry)
    dialog.add_response("cancel", "Cancelar")
    dialog.add_response("ok", confirm)
    dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("ok")
    dialog.set_close_response("cancel")
    dialog.set_response_enabled("ok", bool(initial.strip()))
    entry.connect("changed", lambda e: dialog.set_response_enabled("ok", bool(e.get_text().strip())))
    dialog.connect("response", lambda _d, response: response == "ok" and action(entry.get_text()))
    dialog.present(parent)
    entry.grab_focus()
    return dialog


def placeholder_track(path):
    """A playlist entry whose file is not in the library (moved, removed or never scanned)."""
    data = tags.empty(path)
    data.update(tags.infer_from_path(path))
    track = Track(data)
    track.album_key = album_key(track)
    track.missing = True
    return track


class PlaylistView(Gtk.Stack):
    def __init__(self, controller):
        super().__init__(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.controller = controller
        self.playlist_id = None
        self.paths = []
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.header = Header(140, "media-playlist-consecutive-symbolic")
        self.header.kind.set_text("PLAYLIST")
        self.header.subtitle.set_visible(False)
        self.header.actions.append(play_buttons(lambda: self.table.play_from(0),
                                                lambda: self.table.play_from(0, shuffle=True)))
        menu = Gio.Menu()
        menu.append("Renomear…", "music.playlist-rename")
        menu.append("Exportar como M3U…", "music.playlist-export")
        danger = Gio.Menu()
        danger.append("Excluir playlist", "music.playlist-delete")
        menu.append_section(None, danger)
        more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, valign=Gtk.Align.CENTER,
                              tooltip_text="Mais opções")
        more.add_css_class("circular")
        self.header.actions.append(more)
        box.append(self.header)
        self.empty = Adw.StatusPage(icon_name="media-playlist-consecutive-symbolic", title="Playlist vazia",
                                    description="Clique com o botão direito numa música e escolha "
                                                "“Adicionar à playlist”, ou arraste músicas até o nome dela "
                                                "na barra lateral.")
        self.empty.set_vexpand(True)
        self.table = TrackTable(controller, None, PLAYLIST_COLUMNS, sortable=False, reorder=self.reorder)
        self.table.set_margin_start(12)
        self.table.set_margin_end(12)
        self.content = Gtk.Stack()
        self.content.add_named(self.table, "tracks")
        self.content.add_named(self.empty, "empty")
        box.append(self.content)
        self.add_named(box, "playlist")

    def show(self, playlist_id):
        self.playlist_id = playlist_id
        self.table.playlist_id = playlist_id
        self.reload()

    def reload(self):
        if self.playlist_id is None:
            return
        try:
            playlist = self.controller.music.playlist(self.playlist_id)
        except ValueError:
            self.playlist_id = None
            return
        self.paths = self.controller.music.playlist_paths(self.playlist_id)
        tracks = [self.controller.library.get(path) or placeholder_track(path) for path in self.paths]
        self.header.title.set_text(playlist["name"])
        missing = sum(1 for track in tracks if getattr(track, "missing", False))
        meta = count_text(tracks)
        if missing:
            meta += f" · {missing} fora da biblioteca"
        self.header.meta.set_text(meta)
        self.header.cover.set_key(next((t.cover for t in tracks if t.cover), ""))
        self.table.set_tracks(tracks)
        self.content.set_visible_child_name("tracks" if tracks else "empty")

    def reorder(self, positions, target):
        self.controller.music.move_in_playlist(self.playlist_id, positions, target)
        self.reload()
        self.controller.refresh_playlists()
