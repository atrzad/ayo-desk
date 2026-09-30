"""Finds the best lyrics for the current song and keeps them in sync (local → cache → LRCLIB → voice)."""
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import threading

from gi.repository import GLib

from ...tasks import background
from .. import lyrics, voice
from ..net import Cache, Http, NetError

MISSING_RETRY_DAYS = 7
HEAVY = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ayo-voz")  # voice work never blocks the rest


class LyricsManager:
    """`changed()` is called on the GTK loop whenever `lyrics`, `status` or `progress` change."""

    def __init__(self, page, changed):
        self.page = page
        self.changed = changed
        self.path = None
        self.lyrics = None          # {"lines", "synced", "source", "offset"}
        self.plain = None           # plain lyrics kept as the text to sync by voice
        self.status = ""
        self.progress = None        # 0..1 while syncing by voice or downloading the model
        self.busy = False
        self.cancel = threading.Event()
        self._http = None

    def setting(self, key, default=True):
        return self.page.store.setting(f"music.lyrics_{key}", default)

    def http(self):
        if self._http is None:
            self._http = Http(Cache())
        return self._http

    # ── loading ────────────────────────────────────────────────────────────
    def load(self, path, force=False):
        if path == self.path and not force:
            return
        self.cancel.set()           # stop work for the previous song
        self.cancel = threading.Event()
        self.path, self.lyrics, self.plain, self.progress = path, None, None, None
        self.status = "Procurando a letra…" if path else ""
        self.changed()
        if not path or "://" in path:
            self.status = ""
            self.changed()
            return
        background(lambda: lyrics.local(path), lambda found, error: self._local(path, found))

    def _still(self, path):
        return path == self.path and not self.page.closed

    def _offset(self, path):
        row = self.page.music.lyrics_row(path)
        return row["offset_ms"] if row else 0

    def _show(self, path, found, status=""):
        if not self._still(path):
            return
        found = dict(found)
        found["offset"] = self._offset(path) or found.get("offset", 0)
        self.lyrics = found
        self.status = status
        self.progress = None
        self.changed()

    def _local(self, path, found):
        if not self._still(path):
            return
        if found and found["synced"]:
            self._show(path, found)
            return
        if found:
            self.plain = found
        row = self.page.music.lyrics_row(path)
        if row and row["text"] and row["synced"]:
            self._show(path, lyrics.parse(row["text"], row["source"]))
            return
        if row and row["text"] and not self.plain:
            self.plain = lyrics.parse(row["text"], row["source"])
        recently_missing = row and row["source"] == "nenhuma" and _age_days(row["updated"]) < MISSING_RETRY_DAYS
        if self.setting("online") and not recently_missing:
            self._online(path)
        else:
            self._no_synced(path)

    def _online(self, path):
        track = self.page.library.get(path)
        if track is None or not track.title:
            self._no_synced(path)
            return
        artist = track.artist or track.display_artist
        http = self.http()
        query = (artist, track.title, track.album or "", track.duration or None)

        def done(found, error):
            if not self._still(path):
                return
            if error is None and found:
                if found["synced"]:
                    self.page.music.save_lyrics(path, "lrclib", True, found["synced"])
                    self._show(path, lyrics.parse(found["synced"], "lrclib"))
                    return
                if found["plain"] and not self.plain:
                    self.plain = lyrics.parse(found["plain"], "lrclib")
                    self.page.music.save_lyrics(path, "lrclib", False, found["plain"])
            elif error is None and not self.plain:
                self.page.music.save_lyrics(path, "nenhuma", False, "")
            self._no_synced(path, offline=error is not None)

        def work():
            try:
                return lyrics.LrcLib(http).find(*query)
            except NetError as exc:
                raise RuntimeError(str(exc)) from exc
        background(work, done)

    def _no_synced(self, path, offline=False):
        """Only plain lyrics (or none): sync them by voice when possible, else show them as they are."""
        if not self._still(path):
            return
        if self.plain is None:
            self.lyrics = None
            self.status = "Sem conexão para buscar a letra." if offline else "Nenhuma letra encontrada."
            self.changed()
            return
        self.lyrics = dict(self.plain)
        model = self.setting("model", "base")
        if voice.available(model) and self.setting("voice"):
            self.sync_by_voice()
            return
        if not voice.binary():
            self.status = "Letra sem tempos. Para sincronizar pela voz, instale o whisper-cpp."
        elif not voice.model_path(model):
            self.status = "Letra sem tempos. Baixe o modelo de voz para sincronizar."
        else:
            self.status = "Letra sem tempos."
        self.changed()

    # ── voice sync ─────────────────────────────────────────────────────────
    def voice_state(self):
        """"missing" (no whisper-cpp), "model" (model not downloaded) or "ready"."""
        if not voice.binary():
            return "missing"
        return "ready" if voice.model_path(self.setting("model", "base")) else "model"

    def sync_by_voice(self):
        path, plain = self.path, self.plain
        if not path or plain is None or self.busy:
            return
        model = self.setting("model", "base")
        if self.voice_state() == "model":
            self.download_model(then=self.sync_by_voice)
            return
        if self.voice_state() != "ready":
            return
        self.busy = True
        self.status = "Sincronizando a letra pela voz… (pode levar um minuto)"
        self.progress = -1
        self.changed()
        cancel = self.cancel
        language = voice.guess_language(lyrics.text_of(plain))

        def work():
            words = voice.transcribe(path, model, language, cancel)
            return lyrics.align(plain, words)

        def done(result, error):
            self.busy = False
            if not self._still(path):
                return
            synced, share = result if result else (None, 0)
            if error or synced is None or share < 0.2:
                self.progress = None
                self.status = ("Não deu para sincronizar pela voz."
                               + (f" {error}" if error and "Cancel" not in error else ""))
                self.changed()
                return
            self.page.music.save_lyrics(path, "voz", True, lyrics.to_lrc(synced), share)
            self._show(path, synced, f"Sincronizada pela voz ({round(share * 100)}% das palavras reconhecidas).")
        background(work, done, HEAVY)

    def download_model(self, then=None):
        model = self.setting("model", "base")
        cancel = self.cancel
        self.busy = True
        self.status = f"Baixando o modelo de voz ({voice.MODELS[model][2]})…"
        self.progress = 0.0
        self.changed()

        def progress(done_bytes, total):
            GLib.idle_add(lambda: self._download_progress(done_bytes / total if total else 0) and False)

        def done(_path, error):
            self.busy = False
            if error:
                self.progress = None
                self.status = f"Não foi possível baixar o modelo: {error}"
                self.changed()
                return
            self.progress = None
            self.status = "Modelo de voz pronto."
            self.changed()
            if then:
                then()
        background(lambda: voice.download(model, progress, cancel), done, HEAVY)

    def _download_progress(self, fraction):
        self.progress = fraction
        self.changed()

    # ── adjustments ────────────────────────────────────────────────────────
    def shift(self, delta_ms):
        """Move the lyrics earlier (+) or later (−) for this song; remembered."""
        if not self.lyrics or not self.path:
            return
        self.lyrics["offset"] = self.lyrics.get("offset", 0) + delta_ms
        self.page.music.set_lyrics_offset(self.path, self.lyrics["offset"])
        self.changed()

    def search_again(self):
        if self.path:
            self.page.music.save_lyrics(self.path, "", False, "")
            self.load(self.path, force=True)

    def close(self):
        self.cancel.set()


def _age_days(stamp):
    try:
        return (dt.datetime.now().astimezone() - dt.datetime.fromisoformat(stamp)).days
    except (TypeError, ValueError):
        return 999
