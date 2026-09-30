"""Bottom player bar: current track, transport controls, seek and volume."""
from gi.repository import Gtk, Pango

from ..queue import REPEAT_ALL, REPEAT_OFF, REPEAT_ONE, SHUFFLE_OFF
from .covers import Cover
from .model import duration_text
from .waveform import Waveform

REPEAT_LOOK = {REPEAT_OFF: ("media-playlist-repeat-symbolic", "Repetir: desligado"),
               REPEAT_ALL: ("media-playlist-repeat-symbolic", "Repetir: todas"),
               REPEAT_ONE: ("media-playlist-repeat-song-symbolic", "Repetir: esta música")}


def icon_button(icon, tooltip, callback, *classes):
    button = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    for css in ("flat", *classes):
        button.add_css_class(css)
    button.connect("clicked", lambda _b: callback())
    return button


class PlayerBar(Gtk.Box):
    def __init__(self, controller):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.controller = controller
        self.syncing = False
        self.add_css_class("player-bar")
        layout = Gtk.CenterBox(margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
        self.append(layout)

        now = Gtk.Box(spacing=12)
        self.now = now
        self.cover_button = Gtk.Button(tooltip_text="Expandir")
        self.cover_button.add_css_class("flat")
        self.cover_button.add_css_class("cover-button")
        self.cover = Cover(52)
        self.cover_button.set_child(self.cover)
        self.cover_button.connect("clicked", lambda _b: controller.toggle_expanded())
        now.append(self.cover_button)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, spacing=2)
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=28, width_chars=6)
        self.title.add_css_class("heading")
        self.artist = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=30, width_chars=6)
        self.artist.add_css_class("dim-label")
        self.artist.add_css_class("caption")
        texts.append(self.title)
        texts.append(self.artist)
        now.append(texts)
        self.favorite = Gtk.ToggleButton(icon_name="non-starred-symbolic", tooltip_text="Favorita",
                                         valign=Gtk.Align.CENTER)
        self.favorite.add_css_class("flat")
        self.favorite.connect("toggled", self._favorite_toggled)
        now.append(self.favorite)
        self.expand = icon_button("view-fullscreen-symbolic", "Tela cheia com letra e visualizador (Ctrl+1)",
                                  controller.toggle_expanded)
        now.append(self.expand)
        layout.set_start_widget(now)

        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, width_request=400)
        self.center = center
        controls = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        self.shuffle = Gtk.ToggleButton(icon_name="media-playlist-shuffle-symbolic", tooltip_text="Ordem aleatória",
                                        valign=Gtk.Align.CENTER)
        self.shuffle.add_css_class("flat")
        self.shuffle.connect("toggled", self._shuffle_toggled)
        controls.append(self.shuffle)
        controls.append(icon_button("media-skip-backward-symbolic", "Anterior", controller.previous))
        self.play = icon_button("media-playback-start-symbolic", "Tocar", controller.toggle, "circular", "play-button")
        controls.append(self.play)
        controls.append(icon_button("media-skip-forward-symbolic", "Próxima", controller.next))
        self.repeat = Gtk.ToggleButton(icon_name="media-playlist-repeat-symbolic", tooltip_text="Repetir",
                                       valign=Gtk.Align.CENTER)
        self.repeat.add_css_class("flat")
        self.repeat.connect("clicked", lambda _b: controller.cycle_repeat())
        controls.append(self.repeat)
        center.append(controls)
        seek_row = Gtk.Box(spacing=8)
        self.seek_row = seek_row
        self.elapsed = Gtk.Label(label="0:00", width_chars=5, xalign=1)
        self.remaining = Gtk.Label(label="0:00", width_chars=5, xalign=0)
        for widget in (self.elapsed, self.remaining):
            widget.add_css_class("numeric")
            widget.add_css_class("caption")
        self.seekbar = Waveform()
        self.seekbar.connect("seek", lambda _w, seconds: controller.seek(seconds))
        seek_row.append(self.elapsed)
        seek_row.append(self.seekbar)
        seek_row.append(self.remaining)
        center.append(seek_row)
        layout.set_center_widget(center)

        side = Gtk.Box(spacing=4, halign=Gtk.Align.END)
        self.side = side
        side.append(icon_button("view-list-bullet-symbolic", "Fila", lambda: controller.show_view("queue")))
        self.mute = icon_button("audio-volume-high-symbolic", "Silenciar", controller.toggle_mute)
        side.append(self.mute)
        self.volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.volume.set_size_request(110, -1)
        self.volume.set_draw_value(False)
        self.volume.set_tooltip_text("Volume do player")
        self.volume.connect("value-changed", lambda scale: controller.set_volume(scale.get_value()))
        side.append(self.volume)
        layout.set_end_widget(side)

    def set_expanded(self, expanded):
        self.expand.set_icon_name("go-down-symbolic" if expanded else "view-fullscreen-symbolic")
        self.expand.set_tooltip_text("Recolher (Esc)" if expanded else "Tela cheia com letra e visualizador (Ctrl+1)")
        self.cover_button.set_tooltip_text("Recolher" if expanded else "Expandir")

    def _favorite_toggled(self, button):
        button.set_icon_name("starred-symbolic" if button.get_active() else "non-starred-symbolic")
        if not self.syncing:
            self.controller.set_favorite(self.controller.current_path, button.get_active())

    def _shuffle_toggled(self, button):
        if not self.syncing:
            self.controller.set_shuffle(button.get_active())

    def show_track(self, track, player):
        self.syncing = True
        if track is not None:
            self.title.set_text(track.title)
            self.artist.set_text(f"{track.display_artist} — {track.display_album}")
            self.cover.set_key(track.cover)
            self.favorite.set_active(track.favorite)
        else:
            self.title.set_text(player.title if player.path else "Nada tocando")
            self.artist.set_text(player.artist if player.path else "Escolha uma música na biblioteca")
            self.cover.set_key("")
            self.favorite.set_active(False)
        self.favorite.set_sensitive(track is not None)
        self.syncing = False
        self.show_state(player)

    def show_state(self, player):
        playing = player.playing
        self.play.set_icon_name("media-playback-pause-symbolic" if playing else "media-playback-start-symbolic")
        self.play.set_tooltip_text("Pausar" if playing else "Tocar")

    def show_modes(self, shuffle, repeat):
        self.syncing = True
        self.shuffle.set_active(shuffle != SHUFFLE_OFF)
        icon, tooltip = REPEAT_LOOK[repeat]
        self.repeat.set_icon_name(icon)
        self.repeat.set_tooltip_text(tooltip)
        self.repeat.set_active(repeat != REPEAT_OFF)
        self.syncing = False

    def show_volume(self, percent, muted):
        self.syncing = True
        self.volume.set_value(percent)
        self.syncing = False
        if muted or percent == 0:
            icon = "audio-volume-muted-symbolic"
        elif percent < 34:
            icon = "audio-volume-low-symbolic"
        elif percent < 67:
            icon = "audio-volume-medium-symbolic"
        else:
            icon = "audio-volume-high-symbolic"
        self.mute.set_icon_name(icon)
        self.mute.set_tooltip_text("Ativar som" if muted else "Silenciar")

    def show_position(self, position, duration):
        self.syncing = True
        self.seekbar.set_position(position, duration)
        self.syncing = False
        self.elapsed.set_text(duration_text(position))
        self.remaining.set_text(duration_text(duration))
