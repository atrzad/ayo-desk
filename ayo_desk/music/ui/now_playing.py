"""Now playing: large cover, track details and audio quality."""
from gi.repository import Adw, Gtk, Pango

from .covers import Cover
from .model import UNKNOWN_ALBUM, UNKNOWN_ARTIST


def link(callback):
    button = Gtk.Button(halign=Gtk.Align.CENTER)
    button.add_css_class("flat")
    button.add_css_class("link-button")
    text = Gtk.Label(ellipsize=Pango.EllipsizeMode.END, max_width_chars=48)
    button.set_child(text)
    button.connect("clicked", lambda _b: callback())
    button.text = text
    return button


class NowPlaying(Gtk.Stack):
    def __init__(self, controller):
        super().__init__(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.controller = controller
        self.track = None
        empty = Adw.StatusPage(icon_name="multimedia-player-symbolic", title="Nada tocando",
                               description="Escolha uma música, um álbum ou toque tudo em ordem aleatória.")
        shuffle = Gtk.Button(label="Tocar tudo em ordem aleatória", halign=Gtk.Align.CENTER)
        shuffle.add_css_class("pill")
        shuffle.add_css_class("suggested-action")
        shuffle.connect("clicked", lambda _b: controller.play_all(shuffle=True))
        empty.set_child(shuffle)
        self.add_named(empty, "empty")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, valign=Gtk.Align.CENTER,
                      margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
        box.add_css_class("now-playing")
        self.cover = Cover(340, "audio-x-generic-symbolic")
        self.cover.add_css_class("large-cover")
        box.append(self.cover)
        self.title = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, margin_top=14,
                               wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.title.add_css_class("title-1")
        box.append(self.title)
        self.artist = link(lambda: self.track and controller.open_artist(self.track.display_artist))
        self.artist.text.add_css_class("title-4")
        box.append(self.artist)
        self.album = link(lambda: self.track and controller.open_album_of(self.track))
        box.append(self.album)
        self.quality = Gtk.Label(justify=Gtk.Justification.CENTER, wrap=True)
        self.quality.add_css_class("dim-label")
        self.quality.add_css_class("caption")
        self.quality.add_css_class("numeric")
        box.append(self.quality)
        self.note = Gtk.Label(justify=Gtk.Justification.CENTER, wrap=True, visible=False)
        self.note.add_css_class("dim-label")
        self.note.add_css_class("caption")
        box.append(self.note)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(box)
        self.add_named(scroll, "track")
        self.set_visible_child_name("empty")

    def show_track(self, track, player):
        self.track = track
        if track is None and not player.path:
            self.set_visible_child_name("empty")
            return
        self.set_visible_child_name("track")
        if track is None:
            self.cover.set_key("")
            self.title.set_text(player.title)
            self.artist.text.set_text(player.artist or "")
            self.album.set_visible(False)
            self.quality.set_text("")
            self.note.set_visible(False)
            return
        self.cover.set_key(track.cover, full=True)
        self.title.set_text(track.title)
        self.artist.text.set_text(track.display_artist)
        self.artist.set_sensitive(track.display_artist != UNKNOWN_ARTIST)
        album = track.display_album + (f" · {track.year}" if track.year else "")
        self.album.text.set_text(album)
        self.album.set_visible(True)
        self.album.set_sensitive(track.display_album != UNKNOWN_ALBUM)
        self.quality.set_text(track.quality())
        guessed = [name for name in (track.inferred or "").split(",") if name in ("title", "artist", "album")]
        self.note.set_visible(bool(guessed))
        self.note.set_text("Informações lidas do nome do arquivo e da pasta — sem tags no arquivo.")
