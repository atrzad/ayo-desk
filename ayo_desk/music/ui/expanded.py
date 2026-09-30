"""Expanded player: the visualizer fills the background, with the cover and synced lyrics on top.

Opened from the player bar (or Ctrl+1); goes full screen by default. Esc goes back to the library.
"""
from pathlib import Path
import time

from gi.repository import Adw, GLib, Graphene, Gtk, Pango

from ...widgets import confirm
from .. import lyrics as lyrics_text
from .covers import Cover
from .lyrics_manager import LyricsManager
from .model import UNKNOWN_ALBUM, UNKNOWN_ARTIST
from .visualizer import Visualizer

TICK_MS = 100
HIDE_CONTROLS_MS = 3000
MANUAL_SCROLL_PAUSE = 4.0
SHIFT_STEP = 500
WIDE = 820


def link(callback):
    button = Gtk.Button(halign=Gtk.Align.CENTER)
    button.add_css_class("flat")
    button.add_css_class("link-button")
    text = Gtk.Label(ellipsize=Pango.EllipsizeMode.END, max_width_chars=40)
    button.set_child(text)
    button.connect("clicked", lambda _b: callback())
    button.text = text
    return button


def offset_text(ms):
    if not ms:
        return "No tempo original"
    seconds = f"{abs(ms) / 1000:.1f}".replace(".", ",")
    return f"{seconds} s mais cedo" if ms > 0 else f"{seconds} s mais tarde"


class LyricsView(Gtk.Box):
    """Lyrics lines. When synced, the line being sung is highlighted and kept in the middle."""

    def __init__(self, seek):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.seek = seek
        self.lyrics = None
        self.labels = []
        self.current = -2
        self.centered = None        # line last scrolled to the middle
        self.manual_until = 0.0
        self.animation = None
        self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, hexpand=True)
        self.scroll.add_css_class("lyrics-scroll")
        self.lines = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=160, margin_bottom=220,
                             margin_start=8, margin_end=8)
        self.lines.add_css_class("lyrics")
        self.scroll.set_child(Adw.Clamp(maximum_size=760, child=self.lines))
        self.append(self.scroll)
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL,
                                          propagation_phase=Gtk.PropagationPhase.CAPTURE)
        wheel.connect("scroll", self._manual_scroll)
        self.scroll.add_controller(wheel)

    def _manual_scroll(self, *_args):
        self.manual_until = time.monotonic() + MANUAL_SCROLL_PAUSE
        if self.animation:
            self.animation.pause()
        return False

    def set_lyrics(self, lyrics):
        same = lyrics is not None and self.lyrics is not None and lyrics["lines"] == self.lyrics["lines"]
        self.lyrics = lyrics
        if same:
            return
        child = self.lines.get_first_child()
        while child:
            following = child.get_next_sibling()
            self.lines.remove(child)
            child = following
        self.labels, self.current, self.centered, self.manual_until = [], -2, None, 0.0
        if lyrics is None:
            return
        synced = lyrics["synced"]
        self.lines.remove_css_class("plain" if synced else "synced")
        self.lines.add_css_class("synced" if synced else "plain")
        for ms, text in lyrics["lines"]:
            label = Gtk.Label(label=text or ("♪" if synced else ""), xalign=0, wrap=True,
                              wrap_mode=Pango.WrapMode.WORD_CHAR)
            label.add_css_class("lyric-line")
            if not text:
                label.add_css_class("lyric-gap")
            if synced and ms is not None:
                click = Gtk.GestureClick()
                click.connect("released", lambda _g, _n, _x, _y, at=ms: self._clicked(at))
                label.add_controller(click)
                label.set_cursor_from_name("pointer")
            self.lines.append(label)
            self.labels.append(label)
        self.scroll.get_vadjustment().set_value(0)

    def _clicked(self, ms):
        self.manual_until = 0.0
        offset = self.lyrics.get("offset", 0) if self.lyrics else 0
        self.seek(max(0.0, (ms - offset) / 1000 + 0.05))

    def update(self, position):
        """Highlight the line sung at `position` (seconds); called several times a second."""
        if not self.lyrics or not self.lyrics["synced"] or not self.labels:
            return
        index = lyrics_text.current_index(self.lyrics, position)
        if index != self.current:
            for number, label in enumerate(self.labels):
                state = "current" if number == index else "past" if number < index else ""
                for css in ("current", "past"):
                    if css == state:
                        label.add_css_class(css)
                    else:
                        label.remove_css_class(css)
            self.current = index
        if self.centered != self.current and time.monotonic() >= self.manual_until:
            self._center(max(0, self.current), animate=self.centered is not None)

    def refresh_offset(self):
        self.current = -2

    def _center(self, index, animate=True):
        label = self.labels[index]
        viewport = self.scroll.get_child()
        if label.get_height() <= 0 or viewport is None:
            return  # not laid out yet; the next update tries again
        ok, point = label.compute_point(viewport, Graphene.Point().init(0, 0))
        if not ok:
            return
        adjustment = self.scroll.get_vadjustment()
        page = adjustment.get_page_size()
        top, bottom = int(page * 0.42), int(page * 0.58)
        if page > 0 and (abs(self.lines.get_margin_top() - top) > 8 or abs(self.lines.get_margin_bottom() - bottom) > 8):
            self.lines.set_margin_top(top)  # room to bring the first and last lines to the middle
            self.lines.set_margin_bottom(bottom)
            return  # laid out again first; the next update centers
        start = adjustment.get_value()
        target = start + point.y + label.get_height() / 2 - adjustment.get_page_size() * 0.42
        target = max(adjustment.get_lower(), min(target, adjustment.get_upper() - adjustment.get_page_size()))
        self.centered = index
        if self.animation:
            self.animation.pause()
        if not animate or abs(target - start) < 1:
            adjustment.set_value(target)
            return
        self.animation = Adw.TimedAnimation.new(self.scroll, start, target, 450,
                                                Adw.CallbackAnimationTarget.new(adjustment.set_value))
        self.animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self.animation.play()


class ExpandedView(Gtk.Overlay):
    def __init__(self, controller):
        super().__init__(hexpand=True, vexpand=True, overflow=Gtk.Overflow.HIDDEN)
        self.controller = controller
        self.track = None
        self.active = False
        self.timer = 0
        self.hide_timer = 0
        self.size = (0, 0)
        self.add_css_class("expanded-player")
        self.manager = LyricsManager(controller, self._lyrics_changed)

        self.visualizer = Visualizer(controller.player.running_time, height=(40, 240))
        self.visualizer.set_vexpand(True)
        self.visualizer.add_css_class("expanded-visualizer")
        self.set_child(self.visualizer)

        self.body = Gtk.Box(spacing=56, margin_top=48, margin_bottom=24, margin_start=48, margin_end=48)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER,
                       halign=Gtk.Align.CENTER)
        self.info = info
        self.cover = Cover(400, "audio-x-generic-symbolic")
        self.cover.add_css_class("large-cover")
        self.cover.set_halign(Gtk.Align.CENTER)
        info.append(self.cover)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER)
        self.texts = texts
        info.append(texts)
        self.title = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, margin_top=16, max_width_chars=30,
                               wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.title.add_css_class("title-1")
        texts.append(self.title)
        self.artist = link(lambda: self.track and controller.open_artist(self.track.display_artist))
        self.artist.text.add_css_class("title-4")
        texts.append(self.artist)
        self.album = link(lambda: self.track and controller.open_album_of(self.track))
        texts.append(self.album)
        self.quality = Gtk.Label(justify=Gtk.Justification.CENTER, wrap=True)
        for css in ("dim-label", "caption", "numeric"):
            self.quality.add_css_class(css)
        texts.append(self.quality)
        self.status = Gtk.Label(justify=Gtk.Justification.CENTER, wrap=True, max_width_chars=44, margin_top=10,
                                visible=False)
        self.status.add_css_class("dim-label")
        self.status.add_css_class("caption")
        texts.append(self.status)
        self.progress = Gtk.ProgressBar(visible=False, margin_start=40, margin_end=40)
        texts.append(self.progress)
        self.action = Gtk.Button(halign=Gtk.Align.CENTER, visible=False, margin_top=4)
        self.action.add_css_class("pill")
        self.action.connect("clicked", lambda _b: self._run_action())
        texts.append(self.action)
        self.body.append(info)
        self.lyrics_view = LyricsView(controller.seek)
        self.lyrics_view.set_visible(False)
        self.body.append(self.lyrics_view)
        self.add_overlay(self.body)

        controls = Gtk.Box(spacing=6, margin_top=10, margin_end=10, margin_start=10)
        self.lyrics_toggle = Gtk.ToggleButton(label="Letra", tooltip_text="Mostrar a letra")
        self.lyrics_toggle.add_css_class("pill")
        self.lyrics_toggle.add_css_class("small-pill")
        self.lyrics_toggle.set_active(controller.store.setting("music.expanded_lyrics", True))
        self.lyrics_toggle.connect("toggled", self._lyrics_toggled)
        controls.append(self.lyrics_toggle)
        self.options = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text="Letra e visualizador",
                                      popover=self._build_options())
        self.options.add_css_class("flat")
        controls.append(self.options)
        self.fullscreen_button = Gtk.Button(icon_name="view-fullscreen-symbolic", tooltip_text="Tela cheia (F11)")
        self.fullscreen_button.add_css_class("flat")
        self.fullscreen_button.connect("clicked", lambda _b: controller.toggle_fullscreen())
        controls.append(self.fullscreen_button)
        collapse = Gtk.Button(icon_name="go-down-symbolic", tooltip_text="Recolher (Esc)")
        collapse.add_css_class("flat")
        collapse.connect("clicked", lambda _b: controller.collapse())
        controls.append(collapse)
        self.controls = Gtk.Revealer(child=controls, reveal_child=True, halign=Gtk.Align.END,
                                     valign=Gtk.Align.START, transition_type=Gtk.RevealerTransitionType.CROSSFADE)
        self.add_overlay(self.controls)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda *_: self.wake())
        self.add_controller(motion)
        self.connect("map", lambda _w: self._mapped())
        self.connect("unmap", lambda _w: self._unmapped())
        self.show_track(None)

    # ── options popover ────────────────────────────────────────────────────
    def _build_options(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8, margin_bottom=8,
                      margin_start=8, margin_end=8)
        heading = Gtk.Label(label="Tempo da letra", xalign=0, margin_start=6)
        heading.add_css_class("heading")
        box.append(heading)
        shift = Gtk.Box(spacing=6)
        earlier = Gtk.Button(label="Mais cedo", tooltip_text="A letra aparece 0,5 s antes")
        earlier.connect("clicked", lambda _b: self.manager.shift(SHIFT_STEP))
        later = Gtk.Button(label="Mais tarde", tooltip_text="A letra aparece 0,5 s depois")
        later.connect("clicked", lambda _b: self.manager.shift(-SHIFT_STEP))
        self.offset_label = Gtk.Label(hexpand=True, width_chars=16)
        self.offset_label.add_css_class("numeric")
        self.offset_label.add_css_class("caption")
        shift.append(earlier)
        shift.append(self.offset_label)
        shift.append(later)
        box.append(shift)
        self.reset_offset = Gtk.Button(label="Voltar ao tempo original")
        self.reset_offset.add_css_class("flat")
        self.reset_offset.connect("clicked", lambda _b: self.manager.shift(-self._offset()))
        box.append(self.reset_offset)
        box.append(Gtk.Separator(margin_top=4, margin_bottom=4))
        self.voice_button = Gtk.Button(label="Sincronizar pela voz")
        self.voice_button.add_css_class("flat")
        self.voice_button.connect("clicked", lambda _b: (self.options.popdown(), self.manager.sync_by_voice()))
        box.append(self.voice_button)
        again = Gtk.Button(label="Buscar a letra de novo")
        again.add_css_class("flat")
        again.connect("clicked", lambda _b: (self.options.popdown(), self.manager.search_again()))
        box.append(again)
        self.save_button = Gtk.Button(label="Salvar como .lrc ao lado da música")
        self.save_button.add_css_class("flat")
        self.save_button.connect("clicked", lambda _b: (self.options.popdown(), self.save_lrc()))
        box.append(self.save_button)
        box.append(Gtk.Separator(margin_top=4, margin_bottom=4))
        background = Gtk.Box(spacing=12, margin_start=6)
        background.append(Gtk.Label(label="Visualizador ao fundo", xalign=0, hexpand=True))
        self.background_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.background_switch.set_active(self.controller.store.setting("music.expanded_visualizer", True))
        self.background_switch.connect("notify::active", self._background_toggled)
        background.append(self.background_switch)
        box.append(background)
        popover = Gtk.Popover(child=box)
        popover.connect("show", lambda _p: self._refresh_options())
        return popover

    def _offset(self):
        return (self.manager.lyrics or {}).get("offset", 0)

    def _refresh_options(self):
        lyrics = self.manager.lyrics
        synced = bool(lyrics and lyrics["synced"])
        self.offset_label.set_text(offset_text(self._offset()) if synced else "Letra sem tempos")
        self.reset_offset.set_sensitive(synced and self._offset() != 0)
        self.voice_button.set_sensitive(self.manager.plain is not None and not self.manager.busy
                                        and self.manager.voice_state() != "missing")
        self.voice_button.set_tooltip_text("Instale o whisper-cpp para usar" if self.manager.voice_state() == "missing"
                                           else "Ouve a música e dá tempo a cada linha da letra")
        self.save_button.set_sensitive(synced and bool(self.manager.path))

    def _background_toggled(self, switch, _pspec):
        self.controller.store.set_setting("music.expanded_visualizer", switch.get_active())
        self.controller._update_spectrum()

    def wants_visualizer(self):
        return self.controller.store.setting("music.expanded_visualizer", True)

    def _lyrics_toggled(self, button):
        self.controller.store.set_setting("music.expanded_lyrics", button.get_active())
        self._layout()

    # ── state ──────────────────────────────────────────────────────────────
    def show_track(self, track, player=None):
        self.track = track
        if track is None:
            self.cover.set_key("")
            playing = player is not None and player.path
            self.title.set_text(player.title if playing else "Nada tocando")
            self.artist.text.set_text((player.artist or "") if playing else "")
            self.artist.set_visible(bool(playing and player.artist))
            self.album.set_visible(False)
            self.quality.set_text("")
        else:
            self.cover.set_key(track.cover, full=True)
            self.title.set_text(track.title)
            self.artist.set_visible(True)
            self.artist.text.set_text(track.display_artist)
            self.artist.set_sensitive(track.display_artist != UNKNOWN_ARTIST)
            self.album.text.set_text(track.display_album + (f" · {track.year}" if track.year else ""))
            self.album.set_visible(True)
            self.album.set_sensitive(track.display_album != UNKNOWN_ALBUM)
            self.quality.set_text(track.quality())
        if self.active:
            self.manager.load(self.controller.current_path)

    def enter(self):
        self.active = True
        self.manager.load(self.controller.current_path)
        self.wake()

    def leave(self):
        self.active = False
        self.manager.close()
        self.set_cursor(None)

    def _lyrics_changed(self):
        self.lyrics_view.set_lyrics(self.manager.lyrics)
        self.lyrics_view.refresh_offset()
        manager = self.manager
        status = manager.status
        if not status and manager.lyrics:
            source = manager.lyrics.get("source", "")
            where = ("LRCLIB" if source == "lrclib" else "arquivo ao lado da música" if source.startswith("arquivo")
                     else "tags da música" if source == "embutida" else "sincronizada pela voz" if source == "voz"
                     else source)
            status = f"Letra: {where}" if where else ""
        self.status.set_text(status)
        self.status.set_visible(bool(status))
        self.progress.set_visible(manager.progress is not None)
        if manager.progress is not None and manager.progress >= 0:
            self.progress.set_fraction(manager.progress)
        state = manager.voice_state()
        plain_only = manager.plain is not None and not (manager.lyrics and manager.lyrics["synced"])
        if plain_only and not manager.busy and state == "ready":
            self.action.set_label("Sincronizar pela voz")
            self.action.set_visible(True)
        elif plain_only and not manager.busy and state == "model":
            self.action.set_label("Baixar o modelo de voz e sincronizar")
            self.action.set_visible(True)
        else:
            self.action.set_visible(False)
        self._layout()

    def _run_action(self):
        self.action.set_visible(False)
        self.manager.sync_by_voice()

    def _layout(self):
        showing = bool(self.manager.lyrics) and self.lyrics_toggle.get_active()
        self.lyrics_view.set_visible(showing)
        self.lyrics_toggle.set_sensitive(bool(self.manager.lyrics))
        self.info.set_hexpand(not showing)
        self._fit(force=True)

    def save_lrc(self):
        path, lyrics = self.manager.path, self.manager.lyrics
        if not path or not lyrics or not lyrics["synced"]:
            return
        target = Path(path).with_name(Path(path).stem + ".lrc")
        track = self.track
        text = lyrics_text.to_lrc(lyrics, track.title if track else "", track.display_artist if track else "")

        def write():
            try:
                target.write_text(text, encoding="utf-8")
            except OSError as exc:
                self.controller.notify(f"Não foi possível salvar a letra: {exc}")
                return
            self.controller.notify(f"Letra salva em {target.name}")
        if target.exists():
            confirm(self.controller.window, "Substituir a letra?", f"Já existe {target.name} ao lado da música.",
                    write, "Substituir", destructive=True)
        else:
            write()

    # ── running while visible ──────────────────────────────────────────────
    def _mapped(self):
        if not self.timer:
            self.timer = GLib.timeout_add(TICK_MS, self._tick)
        self._fit(force=True)

    def _unmapped(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = 0

    def _tick(self):
        player = self.controller.player
        if player.path:
            self.lyrics_view.update(player.position()[0])
        if self.manager.progress == -1:
            self.progress.pulse()
        self._fit()
        return GLib.SOURCE_CONTINUE

    def _fit(self, force=False):
        """Lay out for the space available: side by side when wide, stacked when narrow."""
        size = (self.get_width(), self.get_height())
        if (size == self.size and not force) or size[0] <= 0:
            return
        self.size = size
        width, height = size
        wide = width >= WIDE
        lyrics = self.lyrics_view.get_visible()
        self.body.set_orientation(Gtk.Orientation.HORIZONTAL if wide else Gtk.Orientation.VERTICAL)
        margin = 48 if wide else 16
        for side in ("start", "end"):
            getattr(self.body, f"set_margin_{side}")(margin)
        self.body.set_spacing(56 if wide else 12)
        # Narrow with lyrics: a small cover beside the title on top, the lyrics below.
        row = not wide and lyrics
        if wide:
            cover = min(height * (0.52 if lyrics else 0.58), width * (0.34 if lyrics else 0.5), 480)
        elif row:
            cover = min(height * 0.16, 96)
        else:
            cover = min(height * 0.45, width * 0.7, 360)
        self.cover.set_property("size", int(max(56 if row else 96, cover)))
        self.info.set_orientation(Gtk.Orientation.HORIZONTAL if row else Gtk.Orientation.VERTICAL)
        self.info.set_spacing(14 if row else 6)
        self.info.set_valign(Gtk.Align.START if row else Gtk.Align.CENTER)
        self.info.set_halign(Gtk.Align.FILL if row else Gtk.Align.CENTER)
        self.body.set_margin_top(52 if row else 48)
        align = Gtk.Align.START if row else Gtk.Align.CENTER
        self.title.set_margin_top(0 if row else 16)
        for label in (self.title, self.quality, self.status):
            label.set_justify(Gtk.Justification.LEFT if row else Gtk.Justification.CENTER)
            label.set_xalign(0 if row else 0.5)
            label.set_halign(align)
        for widget in (self.artist, self.album, self.action):
            widget.set_halign(align)
        self.album.set_visible(bool(self.track) and not row)
        self.quality.set_visible(not row)
        if row:
            self.title.remove_css_class("title-1")
            self.title.add_css_class("title-3")
        else:
            self.title.remove_css_class("title-3")
            self.title.add_css_class("title-1")
        if wide:
            self.remove_css_class("compact")
        else:
            self.add_css_class("compact")

    # ── controls hide themselves in full screen ────────────────────────────
    def wake(self):
        self.controls.set_reveal_child(True)
        self.set_cursor(None)
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
        self.hide_timer = GLib.timeout_add(HIDE_CONTROLS_MS, self._sleep)

    def _sleep(self):
        self.hide_timer = 0
        if self.controller.window.is_fullscreen() and not self.options.get_active():
            self.controls.set_reveal_child(False)
            self.set_cursor_from_name("none")
        return GLib.SOURCE_REMOVE

    def set_fullscreen_look(self, fullscreen):
        self.fullscreen_button.set_icon_name("view-restore-symbolic" if fullscreen else "view-fullscreen-symbolic")
        self.fullscreen_button.set_tooltip_text("Sair da tela cheia (F11)" if fullscreen else "Tela cheia (F11)")
        self.wake()
