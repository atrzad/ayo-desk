#!/usr/bin/env python3
"""Exercise real GTK screens and read services, using an isolated local database.

Never connects/disconnects networks, pairs devices, or changes system audio.
"""
import argparse
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time
import traceback
import wave

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

parser = argparse.ArgumentParser()
parser.add_argument("--snapshots", type=Path)
parser.add_argument("--only", choices=("network", "audio", "bluetooth", "calendar", "calculator", "music"))
args = parser.parse_args()
if not args.only:
    failed = 0
    for key in ("network", "audio", "bluetooth", "calendar", "calculator", "music"):
        command = [sys.executable, str(Path(__file__).resolve()), "--only", key]
        if args.snapshots:
            command += ["--snapshots", str(args.snapshots)]
        failed += subprocess.run(command, check=False).returncode != 0
    print(f"Suite smoke: {failed} aplicativo(s) com falha", flush=True)
    sys.exit(bool(failed))
if args.snapshots:
    args.snapshots.mkdir(parents=True, exist_ok=True)
temp = tempfile.TemporaryDirectory(prefix="ayo-smoke-")
# Keep the real session/system buses available, but isolate all app data and never make sound.
os.environ["XDG_DATA_HOME"] = temp.name
os.environ["XDG_CACHE_HOME"] = str(Path(temp.name) / "cache")
os.environ["AYO_MUSIC_SINK"] = "fakesink"
os.environ["AYO_NO_MPRIS"] = "1"  # don't show the test player in the real desktop's media widgets

from ayo_desk.app import Application, PAGES
from ayo_desk.core import Store
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk

failures = []
music_stage = 0
music_deadline = None
music_fixture = None

if args.only == "music":
    original = Path(temp.name) / "Coleção inicial"
    replacement = Path(temp.name) / "Minha música"
    first = original / "Álbum" / "Faixa inicial.WAV"
    second = replacement / "Outro álbum" / "Faixa da nova pasta.wav"
    for track in (first, second):
        track.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(track), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(b"\0\0" * 800)
    cache = Store()
    cache.sync_music_folder(original, [])
    cache.close()
    music_fixture = original, replacement, first, second


def exception_hook(kind, value, tb):
    failures.append(str(value))
    traceback.print_exception(kind, value, tb)


sys.excepthook = exception_hook
app = Application(args.only or "calculator")
application_id = app.get_application_id()
app.set_application_id("io.github.ayodesk.Smoke")
app.set_flags(Gio.ApplicationFlags.HANDLES_COMMAND_LINE | Gio.ApplicationFlags.NON_UNIQUE)
queue = [args.only] if args.only else list(PAGES)


def screenshot(window, name):
    if not args.snapshots or name not in ("calculator", "calendar", "music"):
        return
    snapshot = Gtk.Snapshot.new()
    background = Gdk.RGBA()
    background.parse("#222226" if Adw.StyleManager.get_default().get_dark() else "#fafafb")
    snapshot.append_color(background, Graphene.Rect().init(0, 0, window.get_width(), window.get_height()))
    window.snapshot_child(window.get_child(), snapshot)
    node = snapshot.to_node()
    if node:
        renderer = Gsk.CairoRenderer.new()
        renderer.realize(None)
        try:
            texture = renderer.render_texture(node, None)
            texture.save_to_png(str(args.snapshots / f"{name}.png"))
        finally:
            renderer.unrealize()


def inspect():
    global music_stage, music_deadline
    window = app.window
    key = window.stack.get_visible_child_name()
    page = window.pages[key]
    try:
        if key in ("calendar", "calculator", "music"):
            assert window.standalone, "A ferramenta precisa abrir como aplicativo independente"
            assert not hasattr(window, "navigation"), "Aplicativo independente não deve ter a barra da central"
            assert window.get_title() == f"Ayo {PAGES[key][0]}"
            assert window.header_title.get_title() == window.get_title()
            assert application_id != "io.github.ayodesk.Desk", "Aplicativo precisa de uma identidade própria"
        else:
            assert set(window.rows) == {"network", "audio", "bluetooth"}, "A central deve conter apenas controles do sistema"
        assert set(window.pages) == {key}, "O aplicativo não deve inicializar outras ferramentas"
        if key == "calculator":
            page.entry.set_text("sqrt(144) + 2^3")
            page.evaluate()
            assert page.result.get_text() == "20"
            page.key("C")
            page.key("1")
            page.key("+")
            page.key("2")
            page.key("=")
            assert page.result.get_text() == "3"
        elif key == "calendar":
            window.store.save_event(page.selected_day(), "14:30", "Revisar os aplicativos", "Evento de teste")
            page.refresh()
            page.edit()
            for popup in list(app.get_windows()):
                if popup != window:
                    popup.close()
        elif key == "network":
            assert page.network.client is not None, page.network.error
            assert page.network.client.get_nm_running(), "NetworkManager não está ativo"
        elif key == "audio":
            assert page.signature is not None, "Não foi possível ler o servidor de áudio"
        elif key == "bluetooth":
            assert page.bluetooth.error is None, page.bluetooth.error
        elif key == "music":
            if music_deadline is None:
                music_deadline = time.monotonic() + 8
            if page.scanning:
                assert time.monotonic() < music_deadline, "A busca da pasta não terminou"
                GLib.timeout_add(100, inspect)
                return GLib.SOURCE_REMOVE
            original, replacement, first, second = music_fixture
            if music_stage == 0:
                assert [page.library.tracks.get_item(n).path for n in range(page.library.tracks.get_n_items())] \
                    == [str(first)], "A pasta salva deve ser lida automaticamente ao abrir"
                assert page.library.get(str(first)).title == "Faixa inicial"
                assert page.library.albums.get_n_items() == 1
                assert page.status_detail.get_tooltip_text() == str(original)
                from unittest.mock import patch

                class SelectedFolder:
                    def __init__(self, **_kwargs):
                        pass

                    def set_initial_folder(self, folder):
                        assert folder.get_path() == str(original)

                    def select_folder(self, parent, _cancel, callback):
                        assert parent is window
                        callback(self, None)

                    def select_folder_finish(self, _result):
                        return Gio.File.new_for_path(str(replacement))

                music_stage = 1
                with patch("ayo_desk.music.ui.window.Gtk.FileDialog", SelectedFolder):
                    page.choose_folder()
                GLib.timeout_add(100, inspect)
                return GLib.SOURCE_REMOVE
            paths = [page.library.tracks.get_item(n).path for n in range(page.library.tracks.get_n_items())]
            assert paths == [str(second)], "Trocar de pasta deve atualizar a biblioteca"
            assert window.store.music_folder() == str(replacement)
            assert page.rescan_button.get_sensitive()
            assert first.is_file(), "A troca de pasta não pode apagar arquivos"
            page.play_all()
            assert page.current_path == str(second), "Tocar tudo deve começar a reprodução"
            assert page.bar.title.get_text() == "Faixa da nova pasta"
            for view in ("now", "queue", "songs", "albums", "artists", "genres", "folders"):
                page.show_view(view)
                assert page.stack.get_visible_child_name() == view
            page.open_album_of(page.library.get(str(second)))
            for text, expected in (("NOVA pasta", 1), ("não existe nada assim", 0), ("", 1)):
                page.search_entry.set_text(text)
                page.on_search(page.search_entry)  # the entry itself waits a moment before searching
                assert page.songs.table.count() == expected, (text, page.songs.table.count())
            page.enqueue([str(first), str(second)])
            assert page.queue.items() == [str(second), str(first), str(second)]
            page.move_in_queue(2, 1)
            assert page.queue.items() == [str(second), str(second), str(first)]
            page.show_view("queue")
            page.set_sleep(15)
            assert "15 min" in page.sleep_text()
            page.set_sleep(None)
            page.set_rate(1.25)
            assert page.player.rate == 1.25 and window.store.setting("music.rate") == 1.25
            page.set_rate(1.0)
            playlist = page.music.create_playlist("Teste", [str(second)])
            page.refresh_playlists()
            page.show_playlist(playlist)
            assert page.stack.get_visible_child_name() == "playlist"
            assert page.playlist_view.table.count() == 1
            page.add_to_playlist(playlist, [str(first)])
            page.add_to_playlist(playlist, [str(first)])  # duplicates are skipped
            assert page.music.playlist_paths(playlist) == [str(second), str(first)]
            page.playlist_view.reorder([1], 0)
            assert page.music.playlist_paths(playlist) == [str(first), str(second)]
            assert page.playlist_view.table.count() == 2
            assert page.rows[f"playlist:{playlist}"].label.get_text() == "Teste"
            page.playlist_view.table.play_from(1)
            assert page.current_path == str(second)
            page.remove_from_playlist(playlist, [0])
            assert page.music.playlist_paths(playlist) == [str(second)]
            assert page.keep_running(), "Fechar a janela tocando deve manter a música"
            window.close()
            assert not window.get_visible() and not window.closed
            window.present()
        print(f"PASS UI: {key} ({application_id})", flush=True)
    except Exception:
        exception_hook(*sys.exc_info())
    def capture_and_continue():
        screenshot(window, key)
        GLib.idle_add(next_page)
        return GLib.SOURCE_REMOVE
    window.queue_draw()
    GLib.timeout_add(300, capture_and_continue)
    return GLib.SOURCE_REMOVE


def next_page():
    if queue:
        try:
            app.window.show_page(queue.pop(0))
            app.window.present()
        except Exception:
            exception_hook(*sys.exc_info())
            GLib.idle_add(next_page)
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(1800, inspect)
    else:
        app.window.quit_app()
        app.quit()
    return GLib.SOURCE_REMOVE


def activated(_app):
    Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
    app.window.stack.set_transition_type(Gtk.StackTransitionType.NONE)
    GLib.timeout_add(300, next_page)


app.connect_after("activate", activated)
app.run(["ayo-smoke"])
temp.cleanup()
print(f"UI smoke: {len(failures)} failure(s)")
sys.exit(bool(failures))
