"""MusicBrainz (open music encyclopedia) and the Cover Art Archive, at most one request per second."""
import re

from .deezer import year_of

BASE = "https://musicbrainz.org/ws/2"
COVERS = "https://coverartarchive.org"
LUCENE = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


def quote(text):
    return '"' + LUCENE.sub(r"\\\1", str(text)) + '"'


def credit(artist_credit):
    return [part.get("name") or part.get("artist", {}).get("name", "") for part in artist_credit or []]


def pick_release(releases):
    """Prefer an official album, then single/EP, earliest first."""
    def rank(release):
        group = release.get("release-group") or {}
        kind = (group.get("primary-type") or "").casefold()
        secondary = [s.casefold() for s in group.get("secondary-types") or []]
        return (release.get("status", "Official") != "Official", "compilation" in secondary or "live" in secondary,
                {"album": 0, "ep": 1, "single": 2}.get(kind, 3), release.get("date") or "9999")
    return sorted(releases or [], key=rank)[0] if releases else {}


class MusicBrainz:
    name = "musicbrainz"
    label = "MusicBrainz"

    def __init__(self, http):
        self.http = http

    def search(self, title, artists=(), duration=None, limit=5):
        query = f"recording:{quote(title)}"
        if artists:
            query += f" AND artist:{quote(artists[0])}"
        if duration:
            ms = int(duration * 1000)
            query += f" AND dur:[{ms - 6000} TO {ms + 6000}]"
        data = self.http.get(f"{BASE}/recording", {"query": query, "fmt": "json", "limit": limit})
        return [self._candidate(item) for item in (data or {}).get("recordings", [])]

    def by_isrc(self, isrc):
        data = self.http.get(f"{BASE}/isrc/{isrc}", {"inc": "artist-credits+releases", "fmt": "json"})
        recordings = (data or {}).get("recordings", [])
        return self._candidate(recordings[0]) if recordings else None

    def _candidate(self, recording):
        release = pick_release(recording.get("releases"))
        group = release.get("release-group") or {}
        media = (release.get("media") or [{}])[0]
        track = (media.get("track") or [{}])[0]
        number = str(track.get("number") or "")
        date = release.get("date") or ""
        return {"source": self.name, "source_id": recording.get("id"), "title": recording.get("title", ""),
                "artists": credit(recording.get("artist-credit")), "album": release.get("title", ""),
                "album_artist": ", ".join(credit(release.get("artist-credit"))) or None,
                "release_id": release.get("id"), "release_group_id": group.get("id"),
                "date": date, "year": year_of(date), "track_no": int(number) if number.isdigit() else None,
                "track_total": media.get("track-count"), "disc_no": media.get("position"),
                "isrc": (recording.get("isrcs") or [""])[0], "genre": "",
                "duration": (recording.get("length") or 0) / 1000 or None,
                "record_type": (group.get("primary-type") or "").casefold(), "score": recording.get("score")}

    def complete(self, candidate):
        candidate = dict(candidate)
        if not candidate.get("cover_url"):
            candidate["cover_url"] = self.cover_url(candidate.get("release_id"), candidate.get("release_group_id"))
        return candidate

    def cover_url(self, release_id, group_id=None):
        """First front cover that exists: the release's, then its release group's."""
        for kind, key in (("release", release_id), ("release-group", group_id)):
            if key:
                data = self.http.get(f"{COVERS}/{kind}/{key}")
                images = (data or {}).get("images", [])
                front = next((image for image in images if image.get("front")), images[0] if images else None)
                if front:
                    thumbs = front.get("thumbnails") or {}
                    return thumbs.get("1200") or thumbs.get("large") or front.get("image", "")
        return ""
