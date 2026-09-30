"""Desktop integration: MPRIS bridge, track-change notifications and keyboard shortcuts."""
import os

from gi.repository import Adw, Gdk, Gio, Gtk

from ... import paths
from .. import covers
from ..mpris import Mpris
from ..queue import SHUFFLE_OFF

SHORTCUTS = (
    ("Reprodução", (("Tocar ou pausar", "space"), ("Próxima música", "<Primary>Right"),
                    ("Música anterior", "<Primary>Left"), ("Avançar 5 segundos", "<Shift>Right"),
                    ("Voltar 5 segundos", "<Shift>Left"), ("Aumentar volume", "<Primary>Up"),
                    ("Diminuir volume", "<Primary>Down"), ("Silenciar", "m"), ("Ordem aleatória", "s"),
                    ("Repetir", "r"))),
    ("Tela cheia", (("Expandir com letra e visualizador", "<Primary>1"), ("Tela cheia ou janela", "F11"),
                    ("Recolher", "Escape"))),
    ("Navegação", (("Buscar", "<Primary>f"), ("Fila", "<Primary>2"),
                   ("Músicas", "<Primary>3"), ("Álbuns", "<Primary>4"), ("Artistas", "<Primary>5"),
                   ("Preferências", "<Primary>comma"), ("Atalhos do teclado", "<Primary>question"),
                   ("Fechar a janela (a música continua)", "<Primary>w"), ("Sair do Ayo Música", "<Primary>q"))),
)
VIEWS_BY_KEY = {Gdk.KEY_2: "queue", Gdk.KEY_3: "songs", Gdk.KEY_4: "albums", Gdk.KEY_5: "artists"}


class Integration:
    def __init__(self, page):
        self.page = page
        self.mpris = None
        if not os.environ.get("AYO_NO_MPRIS") and paths.LINUX:  # MPRIS is a Linux desktop standard
            self.mpris = Mpris.on_session_bus(self)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        page.window.add_controller(keys)
        self.keys = keys

    # ── MPRIS target protocol ──────────────────────────────────────────────
    def mpris_state(self):
        page = self.page
        player = page.player
        status = "Stopped" if not player.path else "Playing" if player.playing else "Paused"
        track = page.library.get(page.current_path) if page.current_path else None
        metadata = None
        if player.path:
            metadata = {"path": player.path, "title": player.title, "artist": player.artist}
            if track is not None:
                art = covers.path(track.cover, thumbnail=False) if track.cover else None
                metadata.update(title=track.title, artist=track.display_artist, album=track.display_album,
                                album_artist=track.album_artist, genre=track.genre, length=track.duration,
                                track=track.track_no, disc=track.disc_no, plays=track.plays, rating=track.rating,
                                art=str(art) if art else None)
        return {"status": status, "loop": page.queue.repeat, "shuffle": page.queue.shuffle != SHUFFLE_OFF,
                "volume": page.volume_value / 100, "rate": player.rate, "position": player.position()[0],
                "metadata": metadata, "can_next": len(page.queue) > 0, "can_previous": len(page.queue) > 0,
                "can_play": bool(player.path or len(page.queue) or page.library.tracks.get_n_items()),
                "can_seek": bool(player.path)}

    def mpris_action(self, name, *args):
        page = self.page
        actions = {
            "Raise": page.present,
            "Quit": page.window.quit_app,
            "Play": lambda: page.player.set_playing(True) if page.player.path else page.toggle(),
            "Pause": lambda: page.player.set_playing(False),
            "PlayPause": page.toggle,
            "Stop": page.stop_playback,
            "Next": page.next,
            "Previous": page.previous,
            "Seek": lambda offset: page.seek(max(0, page.player.position()[0] + offset)),
            "SetPosition": page.seek,
            "OpenUri": lambda uri: page.open_files([Gio.File.new_for_uri(uri).get_path() or uri]),
            "LoopStatus": page.set_repeat,
            "Shuffle": lambda active: page.set_shuffle(bool(active)),
            "Volume": lambda value: page.set_volume(value * 100),
            "Rate": page.set_rate,
        }
        action = actions.get(name)
        if action:
            action(*args)

    def changed(self, *names):
        if self.mpris:
            self.mpris.changed(*names)

    def seeked(self, seconds):
        if self.mpris:
            self.mpris.seeked(seconds)

    # ── notifications ──────────────────────────────────────────────────────
    def notify_track(self, track):
        page = self.page
        app = page.window.get_application()
        if app is None or track is None or not page.store.setting("music.notify", True):
            return
        if page.window.is_active() and page.window.get_visible():
            return  # the window already shows it
        notification = Gio.Notification.new(track.title)
        notification.set_body(f"{track.display_artist} — {track.display_album}")
        art = covers.path(track.cover) if track.cover else None
        notification.set_icon(Gio.FileIcon.new(Gio.File.new_for_path(str(art))) if art
                              else Gio.ThemedIcon.new("multimedia-audio-player"))
        notification.set_priority(Gio.NotificationPriority.LOW)
        app.send_notification("ayo-musica-tocando", notification)

    # ── keyboard ───────────────────────────────────────────────────────────
    def on_key(self, _controller, keyval, _keycode, state):
        page = self.page
        focus = page.window.get_focus()
        typing = isinstance(focus, (Gtk.Text, Gtk.Editable, Gtk.TextView))
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if ctrl:
            handlers = {Gdk.KEY_Right: page.next, Gdk.KEY_Left: page.previous,
                        Gdk.KEY_Up: lambda: page.set_volume(min(100, page.volume_value + 5)),
                        Gdk.KEY_Down: lambda: page.set_volume(max(0, page.volume_value - 5)),
                        Gdk.KEY_comma: page.show_preferences, Gdk.KEY_question: self.show_shortcuts,
                        Gdk.KEY_w: page.window.close, Gdk.KEY_W: page.window.close}
            if keyval == Gdk.KEY_1:
                page.toggle_expanded()
                return True
            if keyval in VIEWS_BY_KEY:
                page.show_view(VIEWS_BY_KEY[keyval])
                return True
            if keyval in handlers and not (typing and keyval in (Gdk.KEY_Left, Gdk.KEY_Right)):
                handlers[keyval]()
                return True
            return False
        if keyval == Gdk.KEY_F11:
            page.toggle_fullscreen()
            return True
        if keyval == Gdk.KEY_Escape and page.is_expanded() and not typing and not self._popup_open():
            page.collapse()
            return True
        if typing:
            return False
        if shift and keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            delta = 5 if keyval == Gdk.KEY_Right else -5
            page.seek(max(0, page.player.position()[0] + delta))
            return True
        if keyval == Gdk.KEY_space and not isinstance(focus, (Gtk.Button, Gtk.CheckButton, Gtk.Switch)):
            page.toggle()
            return True
        handlers = {Gdk.KEY_m: page.toggle_mute, Gdk.KEY_s: lambda: page.set_shuffle(page.queue.shuffle == SHUFFLE_OFF),
                    Gdk.KEY_r: page.cycle_repeat, Gdk.KEY_question: self.show_shortcuts}
        if keyval in handlers and not state & (Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK):
            handlers[keyval]()
            return True
        return False

    def _popup_open(self):
        """Esc first closes an open menu or dialog, not the expanded player."""
        return self.page.expanded.options.get_active() or self.page.window.get_visible_dialog() is not None

    def show_shortcuts(self):
        dialog = Adw.ShortcutsDialog()
        for title, items in SHORTCUTS:
            section = Adw.ShortcutsSection.new(title)
            for name, accelerator in items:
                section.add(Adw.ShortcutsItem.new(name, accelerator))
            dialog.add(section)
        dialog.present(self.page.window)
        return dialog

    def close(self):
        if self.mpris:
            self.mpris.close()
            self.mpris = None
        app = self.page.window.get_application()
        if app is not None:
            app.withdraw_notification("ayo-musica-tocando")
