from pathlib import Path
import random
import sqlite3
import threading
from gi.repository import Gio, GLib, Gtk
from .music_library import AUDIO_EXTENSIONS, scan_music_folder
from .player import Player
from .tasks import background
from .widgets import Page, button, clear, group, label, row


def clock_text(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class MusicPage(Page):
    def __init__(self, window):
        super().__init__(window, "Música", "Sua biblioteca local, no seu ritmo.")
        self.index = -1
        self.syncing = False
        self.closed = False
        self.scanning = False
        self.scan_cancel = threading.Event()
        self.playlist = window.store.tracks()
        self.folder_button = button("Escolher pasta", self.choose_folder, "suggested-action")
        self.toolbar.append(self.folder_button)
        self.toolbar.append(button("Adicionar arquivos avulsos", self.choose_files, icon="list-add-symbolic"))
        folder_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.folder_section = group(folder_area, "Pasta de músicas")
        self.folder_row = row("Nenhuma pasta selecionada", "Inclui músicas das subpastas.", "folder-music-symbolic")
        self.folder_spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        self.folder_spinner.set_visible(False)
        self.folder_row.add_suffix(self.folder_spinner)
        self.rescan_button = button("Atualizar músicas da pasta", self.scan_folder, icon="view-refresh-symbolic")
        self.folder_row.add_suffix(self.rescan_button)
        self.folder_section.add(self.folder_row)
        self.folder_status = label("Escolha onde o Ayo Música deve buscar suas músicas.", "dim-label")
        folder_area.append(self.folder_status)
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        hero.add_css_class("music-hero")
        icon = Gtk.Image.new_from_icon_name("audio-x-generic-symbolic")
        icon.set_pixel_size(76)
        hero.append(icon)
        self.title = Gtk.Label(label="Nenhuma música selecionada", wrap=True, justify=Gtk.Justification.CENTER)
        self.title.add_css_class("title-1")
        hero.append(self.title)
        self.artist = Gtk.Label(wrap=True)
        self.artist.add_css_class("dim-label")
        hero.append(self.artist)
        self.seekbar = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 1)
        self.seekbar.set_draw_value(False)
        self.seekbar.connect("change-value", self.seek)
        hero.append(self.seekbar)
        self.time = Gtk.Label(label="0:00 / 0:00")
        self.time.add_css_class("numeric")
        hero.append(self.time)
        controls = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        self.shuffle = Gtk.ToggleButton(icon_name="media-playlist-shuffle-symbolic", tooltip_text="Ordem aleatória")
        controls.append(self.shuffle)
        controls.append(button("Anterior", self.previous, icon="media-skip-backward-symbolic"))
        self.play_button = button("Reproduzir ou pausar", self.toggle, "suggested-action", "media-playback-start-symbolic")
        self.play_button.add_css_class("circular")
        controls.append(self.play_button)
        controls.append(button("Próxima", lambda: self.next_track(False), icon="media-skip-forward-symbolic"))
        self.repeat = Gtk.ToggleButton(icon_name="media-playlist-repeat-symbolic", tooltip_text="Repetir biblioteca")
        controls.append(self.repeat)
        hero.append(controls)
        self.body.append(hero)
        volume = Gtk.Box(spacing=12)
        volume.append(Gtk.Image.new_from_icon_name("audio-volume-high-symbolic"))
        slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        slider.set_value(70)
        slider.set_hexpand(True)
        slider.set_tooltip_text("Volume do player")
        volume.append(slider)
        self.body.append(volume)
        self.body.append(folder_area)
        self.filter = Gtk.SearchEntry(placeholder_text="Buscar na biblioteca")
        self.filter.connect("search-changed", lambda _: self.render_playlist())
        self.body.append(self.filter)
        self.library = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.body.append(self.library)
        self.player = Player(self.update, self.notify, lambda: self.next_track(True))
        self.player.volume(70)
        slider.connect("value-changed", lambda s: self.player.volume(s.get_value()))
        if not self.player.available:
            self.body.prepend(label("Faltam codecs de áudio. Instale gst-plugins-base, gst-plugins-good e gst-libav.", "error"))
        self.timer = GLib.timeout_add(500, self.tick)
        self.update()
        self.render_playlist()
        self.update_folder_controls()
        if window.store.music_folder():
            self.scan_folder()

    def update_folder_controls(self):
        folder = self.window.store.music_folder()
        self.folder_row.set_title((Path(folder).name or folder) if folder else "Nenhuma pasta selecionada")
        self.folder_row.set_subtitle(folder or "Inclui músicas das subpastas.")
        self.folder_row.set_tooltip_text(folder)
        self.folder_button.set_label("Trocar pasta" if folder else "Escolher pasta")
        self.folder_button.set_sensitive(not self.scanning)
        self.rescan_button.set_sensitive(bool(folder) and not self.scanning)
        self.folder_spinner.set_visible(self.scanning)
        if self.scanning:
            self.folder_spinner.start()
        else:
            self.folder_spinner.stop()

    def choose_folder(self):
        dialog = Gtk.FileDialog(title="Escolher a pasta de músicas", accept_label="Usar esta pasta")
        folder = self.window.store.music_folder()
        if folder and Path(folder).is_dir():
            dialog.set_initial_folder(Gio.File.new_for_path(folder))
        else:
            music = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_MUSIC)
            if music and Path(music).is_dir():
                dialog.set_initial_folder(Gio.File.new_for_path(music))

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
            self.scan_folder(path)
        dialog.select_folder(self.window, None, selected)

    def scan_folder(self, folder=None):
        folder = folder or self.window.store.music_folder()
        if self.closed or self.scanning or not folder:
            return
        self.scanning = True
        self.scan_cancel.clear()
        self.folder_status.remove_css_class("error")
        self.folder_status.set_text("Buscando músicas na pasta e nas subpastas…")
        self.update_folder_controls()

        def scanned(result, error):
            if self.closed:
                return
            self.scanning = False
            if error:
                self.folder_status.set_text(error)
                self.folder_status.add_css_class("error")
            else:
                root, paths = result
                try:
                    self.window.store.sync_music_folder(root, paths)
                except sqlite3.Error as exc:
                    self.folder_status.set_text(f"Não foi possível salvar a biblioteca: {exc}")
                    self.folder_status.add_css_class("error")
                else:
                    self.reload_playlist()
                    self.folder_status.set_text(f"{len(paths)} arquivos de áudio encontrados • Inclui subpastas")
            self.update_folder_controls()
        background(lambda: scan_music_folder(folder, self.scan_cancel), scanned)

    def reload_playlist(self):
        current = self.player.path
        self.playlist = self.window.store.tracks()
        self.index = self.playlist.index(current) if current in self.playlist else -1
        self.render_playlist()

    def choose_files(self):
        dialog = Gtk.FileDialog(title="Adicionar músicas")
        audio_filter = Gtk.FileFilter(name="Arquivos de áudio")
        audio_filter.add_mime_type("audio/*")
        for extension in sorted(AUDIO_EXTENSIONS):
            audio_filter.add_pattern("*" + extension)
            audio_filter.add_pattern("*" + extension.upper())
        all_files = Gtk.FileFilter(name="Todos os arquivos")
        all_files.add_pattern("*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(audio_filter)
        filters.append(all_files)
        dialog.set_filters(filters)
        dialog.set_default_filter(audio_filter)

        def selected(source, result):
            try:
                files = source.open_multiple_finish(result)
            except GLib.Error as exc:
                if not exc.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                    self.notify(str(exc))
                return
            if self.closed:
                return
            paths = [files.get_item(i).get_path() for i in range(files.get_n_items())]
            self.window.store.add_tracks([p for p in paths if p and Path(p).is_file()])
            self.reload_playlist()
        dialog.open_multiple(self.window, None, selected)

    def render_playlist(self):
        clear(self.library)
        section = group(self.library, f"Biblioteca • {len(self.playlist)} faixas")
        text = self.filter.get_text().casefold()
        shown = 0
        for index, path in enumerate(self.playlist):
            file = Path(path)
            if text not in file.name.casefold():
                continue
            shown += 1
            current = path == self.player.path
            item = row(file.stem, file.parent.name, "media-playback-start-symbolic" if current else "audio-x-generic-symbolic")
            item.set_tooltip_text(path)
            item.add_suffix(button("Reproduzir", lambda i=index: self.play(i), icon="media-playback-start-symbolic"))
            item.add_suffix(button("Remover da biblioteca", lambda p=path: self.remove(p), icon="list-remove-symbolic"))
            section.add(item)
        if not shown:
            section.add(row("Nenhuma música encontrada" if self.playlist else "Sua biblioteca começa aqui",
                            "Escolha uma pasta de músicas ou adicione arquivos pelo botão +."))

    def play(self, index):
        if not 0 <= index < len(self.playlist):
            return
        try:
            self.player.load(self.playlist[index])
            self.index = index
            self.render_playlist()
        except ValueError as exc:
            self.notify(str(exc))

    def toggle(self):
        if self.player.path:
            self.player.toggle()
        elif self.playlist:
            self.play(max(0, self.index))
        else:
            self.choose_folder()

    def next_track(self, automatic):
        if not self.playlist:
            self.player.stop()
            return
        if self.shuffle.get_active() and len(self.playlist) > 1:
            self.play(random.choice([i for i in range(len(self.playlist)) if i != self.index]))
        elif self.index + 1 < len(self.playlist):
            self.play(self.index + 1)
        elif self.repeat.get_active() or not automatic:
            self.play(0)
        else:
            self.player.stop()
            self.index = -1
            self.render_playlist()

    def previous(self):
        position, _duration = self.player.position()
        if position > 3:
            self.player.seek(0)
        else:
            self.play(max(0, self.index - 1))

    def remove(self, path):
        current = self.player.path
        self.window.store.remove_track(path)
        self.playlist = self.window.store.tracks()
        if path == current:
            self.player.stop()
        self.index = self.playlist.index(current) if current in self.playlist else -1
        self.render_playlist()

    def update(self):
        self.title.set_text(self.player.title)
        self.artist.set_text(self.player.artist)
        self.play_button.set_icon_name("media-playback-pause-symbolic" if self.player.playing else "media-playback-start-symbolic")

    def tick(self):
        position, duration = self.player.position()
        self.syncing = True
        self.seekbar.set_range(0, max(1, duration))
        self.seekbar.set_value(position)
        self.seekbar.set_sensitive(duration > 0)
        self.syncing = False
        self.time.set_text(f"{clock_text(position)} / {clock_text(duration)}")
        return GLib.SOURCE_CONTINUE

    def seek(self, _scale, _scroll, value):
        if not self.syncing:
            self.player.seek(value)
        return False

    def close(self):
        self.closed = True
        self.scan_cancel.set()
        GLib.source_remove(self.timer)
        self.player.close()
