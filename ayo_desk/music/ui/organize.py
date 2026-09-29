"""Organize library: identify songs, apply or review corrections, undo them."""
import datetime as dt
from pathlib import Path

from gi.repository import Adw, GLib, Gtk, Pango

from ...tasks import background
from .. import covers
from ..identify import songrec, writer
from ..identify.clean import TOPIC, hints
from ..identify.deezer import Deezer
from ..identify.job import IdentifyJob, Identifier
from ..identify.musicbrainz import MusicBrainz
from ..net import Cache, Http, NetError
from .covers import Cover
from .model import UNKNOWN_ALBUM, UNKNOWN_ARTIST

FIELD_NAMES = {"title": "Título", "artist": "Artista", "album": "Álbum", "album_artist": "Artista do álbum",
               "date": "Data", "track_no": "Faixa", "track_total": "Total de faixas", "disc_no": "Disco",
               "genre": "Gênero", "isrc": "ISRC", "cover": "Capa"}
SOURCES = {"deezer": "Deezer", "musicbrainz": "MusicBrainz", "shazam": "Shazam"}


def video_cover(key):
    """True for 16:9 video thumbnails (what YouTube downloads embed instead of album art)."""
    size = covers.dimensions(key) if key else None
    if not size or not size[1]:
        return False
    return abs(size[0] / size[1] - 1) > 0.05


class Organizer:
    """Owns the identification job; the page and the view call into it."""

    def __init__(self, page):
        self.page = page
        self.store = page.store
        self.music = page.music
        self.job = None
        self.batch = None
        self.rescan_timer = 0
        self.progress = (0, 0)
        self.stats = {"auto": 0, "review": 0, "missing": 0, "unchanged": 0, "error": 0}
        self._http = None
        if self.store.setting("music.identify_since") is None:
            self.store.set_setting("music.identify_since", dt.datetime.now().astimezone().isoformat(timespec="seconds"))
        self.view = OrganizeView(self)

    # ── settings and helpers ───────────────────────────────────────────────
    def setting(self, key, default=True):
        return self.store.setting(f"music.identify_{key}", default)

    def http(self):
        if self._http is None:
            self._http = Http(Cache())
        return self._http

    def identifier(self):
        return Identifier(self.http(), use_deezer=True, use_musicbrainz=self.setting("musicbrainz"),
                          use_songrec=self.setting("songrec"), replace_covers=self.setting("covers"))

    def infos(self, paths):
        signatures = self.music.signatures()
        return [info for info in (self.info(path, signatures) for path in paths) if info]

    def info(self, path, signatures=None):
        track = self.page.library.get(path)
        if track is None:
            return None
        signatures = signatures if signatures is not None else {path: self.music.signatures().get(path)}
        signature = signatures.get(path) or (None, None)
        return {"path": path, "title": track.title if "title" not in (track.inferred or "") else "",
                "artist": track.artist or "", "album": track.album if "album" not in (track.inferred or "") else "",
                "album_artist": track.album_artist or "", "year": track.year, "track_no": track.track_no,
                "track_total": track.track_total, "disc_no": track.disc_no, "genre": track.genre or "",
                "duration": track.duration, "cover": track.cover or "", "mtime_ns": signature[0], "isrc": ""}

    def problems(self):
        """Library songs whose metadata looks incomplete or wrong, grouped by problem."""
        found = {"artist": [], "album": [], "cover": [], "numbers": []}
        video = {}
        library = self.page.library
        for position in range(library.tracks.get_n_items()):
            track = library.tracks.get_item(position)
            if not track.artist or TOPIC.search(track.artist) or "|" in track.artist:
                found["artist"].append(track.path)
            if not track.album or track.album == track.artist or track.display_album == UNKNOWN_ALBUM:
                found["album"].append(track.path)
            if track.cover not in video:
                video[track.cover] = not track.cover or video_cover(track.cover)
            if video[track.cover]:
                found["cover"].append(track.path)
            if not track.year or not track.track_no:
                found["numbers"].append(track.path)
        return found

    # ── running ────────────────────────────────────────────────────────────
    @property
    def running(self):
        return self.job is not None and self.job.is_alive()

    def start(self, paths, confirm=True):
        paths = [p for p in dict.fromkeys(paths) if self.page.library.get(p) is not None]
        if not paths:
            self.page.notify("Nenhuma música para identificar.")
            return
        if self.running:
            self.page.notify("Já existe uma identificação em andamento.")
            return
        if confirm and len(paths) > 1:
            auto = self.setting("auto") and writer.available()
            body = (f"{len(paths)} músicas serão procuradas pelo nome e, se preciso, pelo som. "
                    + ("As correções com certeza alta são gravadas nos arquivos na hora (com backup para desfazer); "
                       "as outras ficam para você revisar." if auto else
                       "Nada é gravado sem a sua revisão."))
            from ...widgets import confirm as ask
            ask(self.page.window, "Identificar músicas?", body, lambda: self.start(paths, confirm=False), "Identificar")
            return
        self.batch = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.stats = {key: 0 for key in self.stats}
        infos = self.infos(paths)
        self.job = IdentifyJob(self.identifier(), infos, self.on_result, self.on_progress, self.on_finished)
        self.progress = (0, len(infos))
        self.job.start()
        self.view.refresh()

    def pause(self):
        if self.running:
            self.job.pause(not self.job.paused)
            self.view.refresh()

    def stop(self):
        if self.running:
            self.job.stop()

    def on_progress(self, done, total, offline):
        self.progress = (done, total)
        self.view.show_progress(done, total, offline)

    def on_result(self, result):
        if self.page.closed:
            return
        self.stats[result["status"]] = self.stats.get(result["status"], 0) + 1
        self.music.save_identify(result)
        if result["status"] == "auto" and self.setting("auto") and writer.available():
            self.apply(result["path"])
        else:
            self.view.refresh_lists()
        self.page.update_organize_badge()

    def on_finished(self, completed):
        stats = self.stats
        self.view.refresh()
        if completed and sum(stats.values()) > 1:
            self.page.notify(f"Identificação concluída: {stats.get('auto', 0)} corrigidas, "
                             f"{stats.get('review', 0)} para revisar, {stats.get('missing', 0)} não encontradas.")

    def identify_new(self, paths):
        """Songs that just appeared in the folder (not the existing library) are identified by themselves.

        Files we wrote ourselves come back from the rescan too; they are skipped because they already
        have a result.
        """
        if not self.setting("new") or self.running or not paths:
            return
        since = self.store.setting("music.identify_since") or ""
        known = self.music.identified_paths()
        fresh = []
        for path in paths:
            track = self.page.library.get(path)
            if track is not None and path not in known and (track.added_at or "") >= since:
                fresh.append(path)
        if fresh:
            self.start(fresh, confirm=False)

    # ── applying and undoing ───────────────────────────────────────────────
    def apply(self, path, change=None):
        result = self.music.identify_result(path)
        change = change or (result or {}).get("changes") or {}
        if not change:
            return
        if not writer.available():
            self.page.notify("Para gravar nos arquivos, instale: sudo pacman -S python-mutagen")
            return
        batch = self.batch or dt.datetime.now().strftime("%Y%m%d-%H%M%S")

        def done(backup, error):
            if self.page.closed:
                return
            if error:
                self.music.set_identify_status(path, "error")
                self.page.notify(f"Não foi possível gravar em {Path(path).name}: {error}")
            else:
                self.music.add_backup(batch, path, backup, change)
                self.music.set_identify_status(path, "applied")
                self._rescan_soon()
            self.view.refresh_lists()
            self.page.update_organize_badge()
        background(lambda: writer.apply(path, change), done)

    def reject(self, path):
        self.music.set_identify_status(path, "rejected")
        self.view.refresh_lists()
        self.page.update_organize_badge()

    def undo(self, backups):
        backups = list(backups)
        if not backups:
            return

        def work():
            failed = []
            for item in backups:
                try:
                    writer.restore(item["path"], item["backup"])
                except Exception as exc:  # noqa: BLE001
                    failed.append((item, str(exc)))
            return failed

        def done(failed, error):
            failed = failed or []
            broken = {item["id"] for item, _message in failed}
            for item in backups:
                if item["id"] not in broken:
                    self.music.mark_restored(item["id"])
                    self.music.set_identify_status(item["path"], "rejected")
            if failed or error:
                self.page.notify(f"Não foi possível desfazer {len(failed) or 'algumas'} alteração(ões).")
            else:
                self.page.notify("Alteração desfeita" if len(backups) == 1 else f"{len(backups)} alterações desfeitas")
            self._rescan_soon()
            self.view.refresh_lists()
        background(work, done)

    def _rescan_soon(self):
        """Many files are written in a row: re-read them once, a moment after the last one."""
        if self.rescan_timer:
            GLib.source_remove(self.rescan_timer)

        def rescan():
            self.rescan_timer = 0
            self.page.scan()
            return GLib.SOURCE_REMOVE
        self.rescan_timer = GLib.timeout_add(1500, rescan)

    # ── manual choices ─────────────────────────────────────────────────────
    def search(self, query, done):
        """Deezer (and MusicBrainz) candidates for a manual search; `done(candidates, error)`."""
        http = self.http()
        use_brainz = self.setting("musicbrainz")

        def work():
            found = Deezer(http).search(query, limit=12)
            if use_brainz:
                try:
                    found += MusicBrainz(http).search(query, limit=5)
                except NetError:
                    pass
            return found
        background(work, done)

    def choose(self, path, candidate):
        info = self.info(path)
        if info is None:
            return
        identifier = self.identifier()

        def done(result, error):
            if error or result is None:
                self.page.notify(error or "Não foi possível usar esta opção.")
                return
            self.music.save_identify(result)
            if result["changes"]:
                self.apply(path)
            else:
                self.view.refresh_lists()
        background(lambda: identifier.choose(info, candidate), done)

    def identify_album(self, group):
        infos = self.infos([track.path for track in group.tracks])
        identifier = self.identifier()
        self.page.notify(f"Procurando “{group.name}”…")

        def done(outcome, error):
            if self.page.closed:
                return
            album, results = outcome if outcome else (None, [])
            if error or album is None:
                self.page.notify(error or f"Não encontrei o álbum “{group.name}”. Tente “Identificar” nas músicas.")
                return
            self.batch = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            for result in results:
                self.on_result(result)
            matched = sum(1 for r in results if r["status"] in ("auto", "unchanged"))
            self.page.notify(f"“{album['album']}”: {matched} de {len(results)} faixas identificadas.")
        background(lambda: identifier.identify_album(infos, group.name if group.name != UNKNOWN_ALBUM else "",
                                                     group.artist if group.artist != UNKNOWN_ARTIST else ""), done)

    def close(self):
        self.stop()
        if self.rescan_timer:
            GLib.source_remove(self.rescan_timer)


# ── the view ─────────────────────────────────────────────────────────────────
def small_label(text, *classes, wrap=False):
    label = Gtk.Label(label=text, xalign=0, ellipsize=Pango.EllipsizeMode.END if not wrap else Pango.EllipsizeMode.NONE,
                      wrap=wrap)
    for css in classes:
        label.add_css_class(css)
    return label


def describe(values):
    parts = [values.get("album") or ""]
    if values.get("date"):
        parts.append(str(values["date"])[:4])
    if values.get("track_no"):
        parts.append(f"faixa {values['track_no']}")
    return " · ".join(p for p in parts if p)


class OrganizeView(Gtk.Box):
    def __init__(self, organizer):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.organizer = organizer
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.append(scroll)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=18, margin_bottom=24,
                      margin_start=24, margin_end=24)
        scroll.set_child(Adw.Clamp(maximum_size=980, child=box))

        title = Gtk.Label(label="Organizar biblioteca", xalign=0)
        title.add_css_class("title-1")
        box.append(title)
        intro = small_label("Reconhece suas músicas pelo nome, pelas tags ou pelo som e completa título, artista, "
                            "álbum, ano, número da faixa e a capa oficial.", "dim-label", wrap=True)
        box.append(intro)
        self.banner = Adw.Banner(revealed=False)
        box.append(self.banner)

        self.problems = Adw.PreferencesGroup(title="O que precisa de atenção")
        self.problem_rows = {}
        for key, text in (("artist", "Sem artista ou com artista estranho"), ("album", "Sem álbum"),
                          ("cover", "Capa de vídeo ou sem capa"), ("numbers", "Sem ano ou número da faixa")):
            row = Adw.ActionRow(title=text)
            count = Gtk.Label(label="…")
            count.add_css_class("numeric")
            row.add_suffix(count)
            row.count = count
            self.problem_rows[key] = row
            self.problems.add(row)
        box.append(self.problems)

        actions = Adw.WrapBox(child_spacing=8, line_spacing=8)
        self.start_problems = Gtk.Button(label="Identificar as com problemas")
        self.start_problems.add_css_class("suggested-action")
        self.start_problems.add_css_class("pill")
        self.start_problems.connect("clicked", lambda _b: self.organizer.start(self.problem_paths))
        self.start_all = Gtk.Button(label="Identificar todas")
        self.start_all.add_css_class("pill")
        self.start_all.connect("clicked", lambda _b: self.organizer.start(self._all_paths()))
        actions.append(self.start_problems)
        actions.append(self.start_all)
        box.append(actions)

        self.progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, visible=False)
        self.progress = Gtk.ProgressBar(show_text=False)
        progress_row = Gtk.Box(spacing=8)
        self.progress_text = small_label("", "dim-label")
        self.progress_text.set_hexpand(True)
        self.pause_button = Gtk.Button(icon_name="media-playback-pause-symbolic", tooltip_text="Pausar")
        self.pause_button.add_css_class("flat")
        self.pause_button.connect("clicked", lambda _b: self.organizer.pause())
        stop = Gtk.Button(icon_name="media-playback-stop-symbolic", tooltip_text="Parar")
        stop.add_css_class("flat")
        stop.connect("clicked", lambda _b: self.organizer.stop())
        for widget in (self.progress_text, self.pause_button, stop):
            progress_row.append(widget)
        self.progress_box.append(self.progress)
        self.progress_box.append(progress_row)
        box.append(self.progress_box)

        self.stack = Adw.ViewStack()
        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        box.append(switcher)
        self.lists = {}
        for key, title, icon in (("review", "Para revisar", "dialog-question-symbolic"),
                                 ("applied", "Aplicadas", "object-select-symbolic"),
                                 ("missing", "Não encontradas", "edit-find-symbolic")):
            page_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            header = Gtk.Box(spacing=8)
            header.caption = small_label("", "dim-label")
            header.caption.set_hexpand(True)
            header.append(header.caption)
            listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            listbox.add_css_class("boxed-list")
            page_box.append(header)
            page_box.append(listbox)
            page = self.stack.add_titled_with_icon(page_box, key, title, icon)
            self.lists[key] = (listbox, header, page)
        self.accept_all = Gtk.Button(label="Aceitar todas acima de 80%")
        self.accept_all.connect("clicked", lambda _b: self._accept_confident())
        self.lists["review"][1].append(self.accept_all)
        self.undo_batch = Gtk.Button(label="Desfazer a última identificação")
        self.undo_batch.connect("clicked", lambda _b: self._undo_last_batch())
        self.lists["applied"][1].append(self.undo_batch)
        box.append(self.stack)
        self.problem_paths = []
        self.connect("map", lambda _w: self.refresh())

    # ── refreshing ─────────────────────────────────────────────────────────
    def _all_paths(self):
        library = self.organizer.page.library
        return [library.tracks.get_item(n).path for n in range(library.tracks.get_n_items())]

    def refresh(self):
        if not self.get_mapped():
            return
        messages = []
        if not writer.available():
            messages.append("Para gravar as correções nos arquivos, instale o python-mutagen.")
        if self.organizer.setting("songrec") and not songrec.available():
            messages.append("Instale o songrec para reconhecer também pelo som.")
        self.banner.set_title(" ".join(messages))
        self.banner.set_revealed(bool(messages))

        def counted(found, error):
            if error or found is None or not self.get_mapped():
                return
            self.problem_paths = list(dict.fromkeys(p for paths in found.values() for p in paths))
            for key, row in self.problem_rows.items():
                row.count.set_text(str(len(found[key])))
            self.start_problems.set_label(f"Identificar as com problemas ({len(self.problem_paths)})")
            self.start_problems.set_sensitive(not self.organizer.running and bool(self.problem_paths))
        background(self.organizer.problems, counted)  # reads cover headers: keep it off the UI thread
        running = self.organizer.running
        self.start_problems.set_sensitive(not running and bool(self.problem_paths))
        self.start_all.set_sensitive(not running)
        self.progress_box.set_visible(running)
        if running:
            self.show_progress(*self.organizer.progress, self.organizer.job.offline)
        self.refresh_lists()

    def show_progress(self, done, total, offline=False):
        self.progress_box.set_visible(self.organizer.running)
        self.progress.set_fraction(done / total if total else 0)
        stats = self.organizer.stats
        text = (f"{done} de {total} · {stats.get('auto', 0)} corrigidas · {stats.get('review', 0)} para revisar · "
                f"{stats.get('missing', 0)} não encontradas")
        if offline:
            text = "Sem internet — continua sozinho quando a conexão voltar. " + text
        elif self.organizer.running and self.organizer.job.paused:
            text = "Pausado · " + text
        self.progress_text.set_text(text)
        paused = self.organizer.running and self.organizer.job.paused
        self.pause_button.set_icon_name("media-playback-start-symbolic" if paused else "media-playback-pause-symbolic")
        self.pause_button.set_tooltip_text("Continuar" if paused else "Pausar")
        if not self.organizer.running:
            self.start_all.set_sensitive(True)
            self.start_problems.set_sensitive(bool(self.problem_paths))

    def refresh_lists(self):
        if not self.get_mapped():
            return
        music = self.organizer.music
        review = music.identify_results("review", "auto", limit=400)
        missing = music.identify_results("missing", "error", limit=400)
        applied = music.backups(limit=400)
        for key, items, builder in (("review", review, self._review_row), ("missing", missing, self._missing_row),
                                    ("applied", applied, self._applied_row)):
            listbox, header, page = self.lists[key]
            while child := listbox.get_first_child():
                listbox.remove(child)
            for item in items:
                listbox.append(builder(item))
            page.set_badge_number(len(items) if key == "review" else 0)
            header.caption.set_text(f"{len(items)} música" + ("s" if len(items) != 1 else "")
                                    if items else "Nada por aqui.")
        self.accept_all.set_visible(bool(review))
        self.accept_all.set_sensitive(any(item["confidence"] >= 80 for item in review))
        self.undo_batch.set_visible(bool(applied))

    # ── rows ───────────────────────────────────────────────────────────────
    def _track_line(self, path):
        track = self.organizer.page.library.get(path)
        return (track.title, track.display_artist) if track else (Path(path).stem, "")

    def _review_row(self, item):
        change, current = item["changes"], item["current"]
        row = Gtk.Box(spacing=12, margin_top=10, margin_bottom=10, margin_start=12, margin_end=12)
        covers_box = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
        old = Cover(44)
        old.set_key(current.get("cover", ""))
        covers_box.append(old)
        if change.get("cover"):
            covers_box.append(Gtk.Image(icon_name="go-next-symbolic"))
            new = Cover(44)
            new.set_key(change["cover"])
            covers_box.append(new)
        row.append(covers_box)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        title = change.get("title") or current.get("title") or self._track_line(item["path"])[0]
        artist = change.get("artist") or current.get("artist") or ""
        texts.append(small_label(f"{title} — {artist}" if artist else title, "heading"))
        merged = {**current, **change}
        if describe(merged):
            texts.append(small_label(describe(merged), "dim-label"))
        for field, value in change.items():
            if field == "cover":
                continue
            before = current.get(field) or "—"
            texts.append(small_label(f"{FIELD_NAMES.get(field, field)}: {before} → {value}", "caption"))
        confidence = small_label(f"{item['confidence']}% · {SOURCES.get(item['source'], item['source'] or '?')}"
                                 + (f" · {', '.join(item['reasons'][:2])}" if item["reasons"] else ""),
                                 "caption", "dim-label")
        texts.append(confidence)
        row.append(texts)
        buttons = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
        accept = Gtk.Button(icon_name="object-select-symbolic", tooltip_text="Aplicar")
        accept.add_css_class("suggested-action")
        accept.add_css_class("circular")
        accept.connect("clicked", lambda _b: self.organizer.apply(item["path"]))
        reject = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Ignorar")
        reject.add_css_class("flat")
        reject.connect("clicked", lambda _b: self.organizer.reject(item["path"]))
        other = Gtk.Button(icon_name="edit-find-symbolic", tooltip_text="Outras opções / buscar")
        other.add_css_class("flat")
        other.connect("clicked", lambda _b: self.search_dialog(item))
        for widget in (accept, other, reject):
            buttons.append(widget)
        row.append(buttons)
        return row

    def _missing_row(self, item):
        title, artist = self._track_line(item["path"])
        row = Adw.ActionRow(title=title, subtitle=(f"{artist} · " if artist else "") + Path(item["path"]).name,
                            use_markup=False)
        row.set_subtitle_lines(2)
        search = Gtk.Button(label="Buscar…", valign=Gtk.Align.CENTER)
        search.connect("clicked", lambda _b: self.search_dialog(item))
        ignore = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Ignorar", valign=Gtk.Align.CENTER)
        ignore.add_css_class("flat")
        ignore.connect("clicked", lambda _b: self.organizer.reject(item["path"]))
        row.add_suffix(search)
        row.add_suffix(ignore)
        return row

    def _applied_row(self, item):
        title, artist = self._track_line(item["path"])
        when = item["created"][11:16] if item.get("created") else ""
        fields = ", ".join(FIELD_NAMES.get(f, f).lower() for f in item["applied"])
        row = Adw.ActionRow(title=f"{title} — {artist}" if artist else title,
                            subtitle=f"{when} · mudou {fields}", use_markup=False)
        undo = Gtk.Button(label="Desfazer", valign=Gtk.Align.CENTER)
        undo.connect("clicked", lambda _b: self.organizer.undo([item]))
        row.add_suffix(undo)
        return row

    # ── actions ────────────────────────────────────────────────────────────
    def _accept_confident(self):
        for item in self.organizer.music.identify_results("review", "auto"):
            if item["confidence"] >= 80 and item["changes"]:
                self.organizer.apply(item["path"])

    def _undo_last_batch(self):
        applied = self.organizer.music.backups(limit=1)
        if applied:
            batch = applied[0]["batch"]
            from ...widgets import confirm
            items = self.organizer.music.backups(batch=batch, limit=5000)
            confirm(self.organizer.page.window, "Desfazer a última identificação?",
                    f"{len(items)} música(s) voltam a ter as tags e a capa de antes.",
                    lambda: self.organizer.undo(items), "Desfazer", destructive=True)

    def search_dialog(self, item):
        SearchDialog(self.organizer, item).present(self.organizer.page.window)


class SearchDialog(Adw.Dialog):
    """Pick the right song by hand: the stored candidates first, then any manual search."""

    def __init__(self, organizer, item):
        super().__init__(title="Escolher a música certa", content_width=560, content_height=620)
        self.organizer = organizer
        self.item = item
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12,
                      margin_start=12, margin_end=12)
        info = organizer.info(item["path"]) or {"path": item["path"], "title": "", "artist": ""}
        hint = hints(info)
        self.entry = Gtk.SearchEntry(text=(hint["queries"] or [""])[0], placeholder_text="Artista e título")
        self.entry.connect("activate", lambda _e: self.search())
        box.append(self.entry)
        box.append(small_label(Path(item["path"]).name, "dim-label", "caption"))
        self.status = small_label("", "dim-label")
        box.append(self.status)
        self.results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.results.add_css_class("boxed-list")
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.results)
        box.append(scroll)
        toolbar.set_content(box)
        self.set_child(toolbar)
        self.duration = info.get("duration")
        self.show(item.get("candidates") or [])
        if not item.get("candidates"):
            self.search()

    def search(self):
        query = self.entry.get_text().strip()
        if not query:
            return
        self.status.set_text("Procurando…")
        self.organizer.search(query, lambda found, error: self.show(found or [], error))

    def show(self, candidates, error=None):
        while child := self.results.get_first_child():
            self.results.remove(child)
        self.status.set_text(error or ("" if candidates else "Nenhum resultado. Tente outras palavras."))
        for candidate in candidates:
            artists = ", ".join(a for a in candidate.get("artists", []) if a)
            length = candidate.get("duration")
            extra = []
            if candidate.get("album"):
                extra.append(candidate["album"])
            if length:
                extra.append(f"{int(length) // 60}:{int(length) % 60:02d}")
                if self.duration:
                    extra.append(f"({int(length - self.duration):+d} s)")
            extra.append(SOURCES.get(candidate.get("source"), candidate.get("source", "")))
            row = Adw.ActionRow(title=f"{candidate.get('title', '')} — {artists}", subtitle=" · ".join(extra),
                                use_markup=False, activatable=True)
            use = Gtk.Button(label="Usar", valign=Gtk.Align.CENTER)
            use.add_css_class("suggested-action")
            use.connect("clicked", lambda _b, c=candidate: self.use(c))
            row.add_suffix(use)
            self.results.append(row)

    def use(self, candidate):
        self.organizer.choose(self.item["path"], candidate)
        self.close()
