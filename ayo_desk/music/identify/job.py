"""Identify songs one by one in a worker thread and describe what should change in each file."""
import re
import threading
import time

from gi.repository import GLib

from .. import covers, tags
from ..net import NetError, Offline
from . import clean, matching, songrec
from .deezer import Deezer
from .musicbrainz import MusicBrainz

FIELDS = ("title", "artist", "album", "album_artist", "date", "track_no", "track_total", "disc_no", "genre", "isrc")
GENERIC_GENRES = {"", "music", "musica", "other", "outros", "unknown", "desconhecido", "youtube", "genre"}
SONGREC_GAP = 1.5   # seconds between recognitions, to be gentle with the service
COMPILATION = re.compile(r"greatest hits|best of|the very best|\bhits\b|colet[âa]nea|b-?sides|anthology|"
                         r"essentials|collection|compilation|sucessos|as melhores", re.IGNORECASE)


def current_of(info):
    """What the file says today, in the same shape as a proposal."""
    date = str(info.get("year") or "")
    return {"title": info.get("title") or "", "artist": info.get("artist") or "", "album": info.get("album") or "",
            "album_artist": info.get("album_artist") or "", "date": date, "track_no": info.get("track_no"),
            "track_total": info.get("track_total"), "disc_no": info.get("disc_no"), "genre": info.get("genre") or "",
            "isrc": info.get("isrc") or "", "cover": info.get("cover") or ""}


def proposal_of(candidate):
    return {"title": candidate.get("title") or "", "artist": ", ".join(a for a in candidate.get("artists", []) if a),
            "album": candidate.get("album") or "", "album_artist": candidate.get("album_artist") or "",
            "date": candidate.get("date") or (str(candidate["year"]) if candidate.get("year") else ""),
            "track_no": candidate.get("track_no"), "track_total": candidate.get("track_total"),
            "disc_no": candidate.get("disc_no"), "genre": candidate.get("genre") or "",
            "isrc": candidate.get("isrc") or ""}


def changes(current, proposed):
    """Only the fields that really change; never erase a value we have with an empty one."""
    result = {}
    for field in FIELDS:
        new, old = proposed.get(field), current.get(field)
        if new in (None, "", 0):
            continue
        if field == "genre" and tags.fold(old or "") not in GENERIC_GENRES:
            continue  # keep a genre the user already has
        if field == "date" and old and str(new)[:4] == str(old)[:4]:
            continue
        if str(new).strip() != str(old or "").strip():
            result[field] = new
    return result


class Identifier:
    """Stateless apart from caches; `identify(info)` may be called from any single worker thread."""

    def __init__(self, http, use_deezer=True, use_musicbrainz=True, use_songrec=True, replace_covers=True):
        self.http = http
        self.deezer = Deezer(http) if use_deezer else None
        self.musicbrainz = MusicBrainz(http) if use_musicbrainz else None
        self.use_songrec = use_songrec
        self.replace_covers = replace_covers
        self.last_recognition = 0.0

    def providers(self):
        return {p.name: p for p in (self.deezer, self.musicbrainz) if p is not None}

    # ── searching ──────────────────────────────────────────────────────────
    def _text_candidates(self, hint):
        ranked = []
        if self.deezer is not None:
            for query in hint["queries"]:
                ranked = merge(ranked, matching.best(hint, self.deezer.search(query)))
                if ranked and ranked[0][0] >= matching.AUTO:
                    break
        return ranked

    def _prefer_original(self, ranked):
        """Same song on the album, a single and a "Greatest Hits": prefer the original album.

        Candidates within 3 points of the best are completed (a few extra requests) and ordered by:
        not a compilation, album before EP before single, earliest release.
        """
        if not ranked or self.deezer is None:
            return ranked
        top = ranked[0][0]
        tied = [item for item in ranked[:4] if item[0] >= top - 3 and item[2].get("source") == "deezer"]
        if len(tied) < 2:
            return ranked
        completed = []
        for points, reasons, candidate in tied:
            try:
                completed.append((points, reasons, self.deezer.complete(candidate)))
            except NetError:
                return ranked

        def originality(item):
            candidate = item[2]
            kind = (candidate.get("record_type") or "").casefold()
            compilation = kind == "compile" or bool(COMPILATION.search(candidate.get("album") or ""))
            return (compilation, {"album": 0, "ep": 1, "single": 2}.get(kind, 3), candidate.get("date") or "9999")
        completed.sort(key=originality)
        chosen = completed[0]
        rest = [item for item in ranked if item[2].get("source_id") != chosen[2].get("source_id")]
        return [(top, chosen[1], chosen[2]), *rest]

    def _recognized(self, path, hint):
        """Ask SongRec; confirm and complete through Deezer (by ISRC, else by name)."""
        if not (self.use_songrec and songrec.available()):
            return None
        wait = SONGREC_GAP - (time.monotonic() - self.last_recognition)
        if wait > 0:
            time.sleep(wait)
        self.last_recognition = time.monotonic()
        heard = songrec.recognize(path)
        if heard is None:
            return None
        full = None
        if self.deezer is not None:
            if heard.get("isrc"):
                full = self.deezer.by_isrc(heard["isrc"])
            if full is None and heard.get("artists"):
                heard_hint = {"title": heard["title"], "artists": heard["artists"], "duration": hint.get("duration"),
                              "versions": clean.versions_of(heard["title"])}
                ranked = matching.best(heard_hint, self.deezer.search(f"{heard['artists'][0]} {heard['title']}"))
                if ranked and ranked[0][0] >= 80:
                    full = self.deezer.complete(ranked[0][2])
        candidate = full or heard
        if full is not None and not full.get("cover_url"):
            full["cover_url"] = heard.get("cover_url", "")
        points = matching.duration_points(hint.get("duration"), candidate.get("duration"))
        confidence = 95 if points is not None and points >= 0.85 else 88 if points is None else 75 if points > 0 else 50
        reasons = ["reconhecida pelo som"] + (["mesma duração"] if points and points >= 0.85 else [])
        return confidence, reasons, candidate

    def _musicbrainz(self, hint):
        if self.musicbrainz is None or not hint.get("core"):
            return []
        return matching.best(hint, self.musicbrainz.search(hint["core"], hint.get("artists") or (),
                                                           hint.get("duration")))

    def identify(self, info):
        """Result dict: status (auto/review/missing/unchanged), confidence, source, changes, candidates."""
        hint = clean.hints(info)
        ranked = self._prefer_original(self._text_candidates(hint))
        top = ranked[0] if ranked else None
        if top is None or top[0] < matching.AUTO:
            heard = self._recognized(info["path"], hint)
            if heard is not None:
                same_as_text = top is not None and (
                    (heard[2].get("isrc") and heard[2].get("isrc") == top[2].get("isrc"))
                    or matching.title_similarity(heard[2]["title"], top[2]["title"]) >= 0.9)
                if same_as_text:
                    heard = (max(heard[0], 92), heard[1] + ["confere com a busca por nome"], heard[2])
                if top is None or heard[0] >= top[0]:
                    top = heard
        if top is None or top[0] < matching.REVIEW:
            alternatives = self._musicbrainz(hint)
            if alternatives and (top is None or alternatives[0][0] > top[0]):
                top = alternatives[0]
                ranked = merge(ranked, alternatives)
        candidates = [c for _s, _r, c in ranked[:6]]
        if top is None:
            return self._result(info, "missing", 0, "", {}, [], candidates, "")
        confidence, reasons, candidate = top
        candidate = self._complete(candidate)
        if confidence >= matching.AUTO and (candidate.get("record_type") == "compile"
                                            or COMPILATION.search(candidate.get("album") or "")):
            confidence, reasons = matching.AUTO - 1, [*reasons, "é uma coletânea"]
        return self.build(info, candidate, confidence, reasons, candidates)

    def build(self, info, candidate, confidence, reasons, candidates=()):
        """Result for `info` if we adopt `candidate` (already complete) with this confidence."""
        current = current_of(info)
        proposed = proposal_of(candidate)
        diff = changes(current, proposed)
        cover_key = self._cover(candidate) if self.replace_covers else ""
        if cover_key and cover_key != current["cover"]:
            diff["cover"] = cover_key
        status = ("unchanged" if not diff else "auto" if confidence >= matching.AUTO
                  else "review" if confidence >= matching.REVIEW else "missing")
        return self._result(info, status, confidence, candidate.get("source", ""), diff, reasons,
                            list(candidates) or [candidate], candidate.get("isrc", ""), proposed)

    def _complete(self, candidate):
        provider = self.providers().get(candidate.get("source"))
        if provider is None or ("isrc" in candidate and candidate.get("track_no") and candidate.get("cover_url")):
            return candidate
        try:
            return provider.complete(candidate)
        except NetError:
            return candidate

    def _cover(self, candidate):
        url = candidate.get("cover_url")
        if not url:
            return ""
        try:
            data = self.http.get(url, binary=True)
        except NetError:
            return ""
        return covers.store(data) if data else ""

    @staticmethod
    def _result(info, status, confidence, source, diff, reasons, candidates, isrc, proposed=None):
        return {"path": info["path"], "mtime_ns": info.get("mtime_ns"), "status": status,
                "confidence": confidence, "source": source, "changes": diff, "reasons": reasons,
                "candidates": candidates, "isrc": isrc, "proposed": proposed or {}, "current": current_of(info)}

    def choose(self, info, candidate):
        """The user picked a candidate by hand: adopt it with full confidence."""
        return self.build(info, self._complete(candidate), 100, ["escolhida por você"], [candidate])

    def identify_album(self, infos, album, artist):
        """Find the album on Deezer and match each file to one of its tracks by title and length.

        Returns (album found or None, results). Files that match no track come back as "missing".
        """
        if self.deezer is None or not infos:
            return None, []
        artists = clean.artist_list(artist)
        name = clean.clean(album)
        options = self.deezer.search_albums(" ".join([*artists[:1], name]).strip() or name)

        def album_score(option):
            title = matching.title_similarity(name, option["album"])
            who = matching.artist_similarity(artists, option["artists"])
            size = option.get("track_total") or 0
            closeness = 1 - min(1.0, abs(size - len(infos)) / max(size, len(infos), 1))
            return 60 * title + 25 * (0.5 if who is None else who) + 15 * closeness
        ranked = sorted(options, key=album_score, reverse=True)
        if not ranked or album_score(ranked[0]) < 70:
            return None, []
        chosen = ranked[0]
        tracks = self.deezer.album_tracks(chosen["album_id"])
        taken, results = set(), []
        for info in infos:
            hint = clean.hints(info)
            scored = sorted(((matching.score(hint, track)[0], number, track) for number, track in enumerate(tracks)
                             if number not in taken), key=lambda item: -item[0])
            if not scored or scored[0][0] < matching.REVIEW:
                results.append(self._result(info, "missing", scored[0][0] if scored else 0, "deezer", {},
                                            ["nenhuma faixa do álbum corresponde"], [], ""))
                continue
            points, number, track = scored[0]
            taken.add(number)
            # Knowing the album already confirms a lot: a good title match inside it is enough.
            confidence = min(100, points + 10)
            results.append(self.build(info, self._complete(track), confidence,
                                      [f"faixa {number + 1} de “{chosen['album']}”"], [track]))
        return chosen, results


def merge(ranked, more):
    """Combine ranked lists, keeping the best score per candidate."""
    best = {}
    for item in [*ranked, *more]:
        key = (item[2].get("source"), item[2].get("source_id"))
        if key not in best or item[0] > best[key][0]:
            best[key] = item
    return sorted(best.values(), key=lambda item: -item[0])


class IdentifyJob(threading.Thread):
    """Runs `identifier.identify` over `infos` in the background; results come back on the GTK loop.

    Pauses by itself while offline and retries the same song when the connection returns.
    """

    def __init__(self, identifier, infos, result, progress=None, finished=None, retry_delay=30):
        super().__init__(daemon=True, name="ayo-identify")
        self.retry_delay = retry_delay
        self.identifier = identifier
        self.infos = list(infos)
        self.result, self.progress, self.finished = result, progress, finished
        self.cancel = threading.Event()
        self.resume = threading.Event()
        self.resume.set()
        self.offline = False

    def pause(self, paused=True):
        (self.resume.clear if paused else self.resume.set)()

    @property
    def paused(self):
        return not self.resume.is_set()

    def stop(self):
        self.cancel.set()
        self.resume.set()

    def run(self):
        total = len(self.infos)
        for number, info in enumerate(self.infos, start=1):
            while True:
                self.resume.wait()
                if self.cancel.is_set():
                    break
                try:
                    outcome = self.identifier.identify(info)
                    self.offline = False
                    break
                except Offline:
                    self.offline = True
                    self._post(self.progress, number - 1, total, True)
                    self.cancel.wait(self.retry_delay)
                except Exception as exc:  # noqa: BLE001 - one bad file must not stop the whole library
                    outcome = {"path": info["path"], "mtime_ns": info.get("mtime_ns"), "status": "error",
                               "confidence": 0, "source": "", "changes": {}, "reasons": [str(exc)],
                               "candidates": [], "isrc": "", "proposed": {}}
                    break
            if self.cancel.is_set():
                break
            self._post(self.result, outcome)
            self._post(self.progress, number, total, False)
        self._post(self.finished, not self.cancel.is_set())

    @staticmethod
    def _post(callback, *args):
        if callback is not None:
            GLib.idle_add(lambda: callback(*args) and False)
