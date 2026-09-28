"""Track properties and player preferences."""
import datetime as dt

from gi.repository import Adw, Gio, Gtk

from .model import duration_text

FIELD_NAMES = {"title": "Título", "artist": "Artista", "album": "Álbum", "album_artist": "Artista do álbum",
               "genre": "Gênero", "year": "Ano", "track_no": "Faixa", "disc_no": "Disco"}


def size_text(size):
    size = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}".replace(".", ",")
        size /= 1024


def when_text(stamp):
    if not stamp:
        return "Nunca"
    try:
        return dt.datetime.fromisoformat(stamp).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return stamp


def info_row(title, value, copyable=False):
    row = Adw.ActionRow(title=title, subtitle=str(value) if value not in (None, "") else "—", use_markup=False)
    row.add_css_class("property")
    row.set_subtitle_selectable(copyable)
    return row


def show_properties(parent, track):
    inferred = set((track.inferred or "").split(","))
    page = Adw.PreferencesPage()
    music = Adw.PreferencesGroup(title="Música")
    if inferred & set(FIELD_NAMES):
        music.set_description("Os campos marcados com • vieram do nome do arquivo ou da pasta, "
                              "porque o arquivo não tem essas tags.")
    values = {"title": track.title, "artist": track.artist or track.display_artist, "album": track.display_album,
              "album_artist": track.album_artist, "genre": track.genre, "year": track.year,
              "track_no": f"{track.track_no}" + (f" de {track.track_total}" if track.track_total else "")
              if track.track_no else "",
              "disc_no": f"{track.disc_no}" + (f" de {track.disc_total}" if track.disc_total else "")
              if track.disc_no else ""}
    for field, name in FIELD_NAMES.items():
        music.add(info_row(name + (" •" if field in inferred else ""), values[field], copyable=True))
    page.add(music)
    audio = Adw.PreferencesGroup(title="Arquivo")
    audio.add(info_row("Qualidade", track.quality()))
    audio.add(info_row("Duração", duration_text(track.duration)))
    audio.add(info_row("Tamanho", size_text(track.size)))
    if track.rg_track_gain is not None:
        audio.add(info_row("ReplayGain", f"{track.rg_track_gain:+.2f} dB (faixa)"
                           + (f", {track.rg_album_gain:+.2f} dB (álbum)" if track.rg_album_gain is not None else "")))
    location = info_row("Local", track.path, copyable=True)
    open_folder = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text="Abrir pasta", valign=Gtk.Align.CENTER)
    open_folder.add_css_class("flat")
    open_folder.connect("clicked", lambda _b: open_containing_folder(parent, track.path))
    location.add_suffix(open_folder)
    audio.add(location)
    page.add(audio)
    stats = Adw.PreferencesGroup(title="Estatísticas")
    stats.add(info_row("Reproduções", track.plays or 0))
    stats.add(info_row("Pulos", track.skips or 0))
    stats.add(info_row("Última vez", when_text(track.last_played)))
    stats.add(info_row("Adicionada em", when_text(track.added_at)))
    page.add(stats)
    dialog = Adw.Dialog(title="Propriedades", content_width=520, content_height=720)
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())
    toolbar.set_content(page)
    dialog.set_child(toolbar)
    dialog.present(parent)
    return dialog


def open_containing_folder(parent, path):
    launcher = Gtk.FileLauncher(file=Gio.File.new_for_path(path))
    launcher.open_containing_folder(parent, None, lambda source, result: _finish(source, result))


def _finish(source, result):
    try:
        source.open_containing_folder_finish(result)
    except Exception:  # noqa: BLE001 - no file manager is not an error worth a dialog
        pass


def show_preferences(parent, controller):
    store = controller.store
    dialog = Adw.PreferencesDialog(title="Preferências")
    page = Adw.PreferencesPage(title="Geral", icon_name="preferences-system-symbolic")

    looks = Adw.PreferencesGroup(title="Aparência")
    grayscale = Adw.SwitchRow(title="Capas em preto e branco",
                              subtitle="Deixa as capas dos álbuns no mesmo tom do tema.")
    grayscale.set_active(store.setting("music.grayscale_covers", False))
    grayscale.connect("notify::active", lambda row, _p: controller.set_grayscale(row.get_active()))
    looks.add(grayscale)
    page.add(looks)

    playback = Adw.PreferencesGroup(title="Reprodução")
    for key, title, subtitle, default in (
            ("music.keep_playing", "Continuar tocando ao fechar a janela",
             "A música segue e pode ser controlada pelas teclas de mídia. Ctrl+Q encerra de vez.", True),
            ("music.notify", "Avisar quando a música mudar",
             "Mostra uma notificação com a capa quando a janela não está em foco.", True),
            ("music.resume_long", "Retomar faixas longas de onde parou",
             "Para faixas com mais de 20 minutos, como audiolivros, podcasts e sets.", True)):
        row = Adw.SwitchRow(title=title, subtitle=subtitle)
        row.set_active(store.setting(key, default))
        row.connect("notify::active", lambda widget, _p, k=key: store.set_setting(k, widget.get_active()))
        playback.add(row)
    page.add(playback)

    library = Adw.PreferencesGroup(title="Biblioteca")
    folder = store.music_folder()
    folder_row = Adw.ActionRow(title="Pasta de músicas", subtitle=folder or "Nenhuma pasta escolhida",
                               use_markup=False)
    change = Gtk.Button(label="Trocar" if folder else "Escolher", valign=Gtk.Align.CENTER)
    change.connect("clicked", lambda _b: (dialog.close(), controller.choose_folder()))
    folder_row.add_suffix(change)
    library.add(folder_row)
    watch = Adw.SwitchRow(title="Acompanhar mudanças na pasta",
                          subtitle="Músicas novas, removidas ou editadas aparecem sozinhas.")
    watch.set_active(store.setting("music.watch_folder", True))
    watch.connect("notify::active", lambda row, _p: controller.set_watch(row.get_active()))
    library.add(watch)
    page.add(library)
    dialog.add(page)
    dialog.add(sound_page(controller))
    dialog.present(parent)
    return dialog


def _switch(store, key, default, title, subtitle, changed):
    row = Adw.SwitchRow(title=title, subtitle=subtitle)
    row.set_active(store.setting(key, default))

    def toggled(widget, _pspec):
        store.set_setting(key, widget.get_active())
        changed()
    row.connect("notify::active", toggled)
    return row


def sound_page(controller):
    from .sound import LEVELING_MODES
    store = controller.store
    changed = controller.apply_sound_settings
    page = Adw.PreferencesPage(title="Som", icon_name="audio-speakers-symbolic")

    leveling = Adw.PreferencesGroup(title="Nivelamento de volume",
                                    description="Deixa as músicas no mesmo volume, como o ReplayGain. Usa as tags "
                                                "do arquivo quando existem e mede o resto em segundo plano.")
    mode = Adw.ComboRow(title="Modo", model=Gtk.StringList.new([name for _key, name in LEVELING_MODES]))
    mode.set_subtitle("Automático usa o volume do álbum quando ele toca em ordem.")
    keys = [key for key, _name in LEVELING_MODES]
    current = store.setting("music.leveling", "auto")
    mode.set_selected(keys.index(current) if current in keys else 0)

    def mode_changed(row, _pspec):
        store.set_setting("music.leveling", keys[row.get_selected()])
        changed()
    mode.connect("notify::selected", mode_changed)
    leveling.add(mode)
    preamp = Adw.SpinRow.new_with_range(-6, 12, 1)
    preamp.set_title("Pré-amplificação (dB)")
    preamp.set_subtitle("Aumente se tudo ficar baixo demais; os picos são protegidos contra distorção.")
    preamp.set_value(store.setting("music.preamp", 0.0))

    def preamp_changed(row, _pspec):
        store.set_setting("music.preamp", row.get_value())
        changed()
    preamp.connect("notify::value", preamp_changed)
    leveling.add(preamp)
    measured, total = controller.sound.progress()
    leveling.add(_switch(store, "music.analyze_library", True, "Medir a biblioteca em segundo plano",
                         f"{measured} de {total} músicas medidas.", changed))
    page.add(leveling)

    transitions = Adw.PreferencesGroup(title="Transições")
    crossfade = Adw.SpinRow.new_with_range(0, 12, 1)
    crossfade.set_title("Crossfade (segundos)")
    crossfade.set_subtitle("0 desliga: as músicas emendam sem intervalo (gapless).")
    crossfade.set_value(store.setting("music.crossfade", 0))

    def crossfade_changed(row, _pspec):
        store.set_setting("music.crossfade", int(row.get_value()))
        changed()
    crossfade.connect("notify::value", crossfade_changed)
    transitions.add(crossfade)
    transitions.add(_switch(store, "music.crossfade_skip_albums", True, "Não misturar faixas do mesmo álbum",
                            "Álbuns tocados em ordem mantêm as transições originais.", changed))
    transitions.add(_switch(store, "music.smooth_pause", True, "Pausar suavemente",
                            "O som diminui aos poucos ao pausar e volta aos poucos ao tocar.", changed))
    page.add(transitions)

    display = Adw.PreferencesGroup(title="Exibição")
    display.add(_switch(store, "music.waveform", True, "Barra de progresso em forma de onda",
                        "Mostra o desenho da música na barra do player.", changed))
    equalizer = Adw.ButtonRow(title="Abrir o equalizador")
    equalizer.connect("activated", lambda _r: controller.show_equalizer())
    display.add(equalizer)
    page.add(display)
    return page


def show_about(parent, version):
    about = Adw.AboutDialog(application_name="Ayo Música", application_icon="multimedia-audio-player",
                            version=version, developer_name="Ayo", license_type=Gtk.License.MIT_X11,
                            comments="Player de música local para Arch Linux, feito em Python, GTK4 e GStreamer.",
                            website="https://github.com/atrzad/ayo-desk")
    about.present(parent)
    return about
