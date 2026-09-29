"""Deezer's public API (no account or key): search, ISRC lookup, track and album details."""

BASE = "https://api.deezer.com"


def _ok(data):
    return isinstance(data, dict) and not data.get("error")


def year_of(date):
    return int(date[:4]) if date and date[:4].isdigit() and date[:4] != "0000" else None


class Deezer:
    name = "deezer"
    label = "Deezer"

    def __init__(self, http):
        self.http = http
        self.albums = {}

    def search(self, query, limit=10):
        data = self.http.get(f"{BASE}/search", {"q": query, "limit": limit})
        if not _ok(data):
            return []
        return [self._lite(item) for item in data.get("data", []) if item.get("type", "track") == "track"]

    def _lite(self, item):
        album = item.get("album") or {}
        return {"source": self.name, "source_id": item["id"], "title": item.get("title", ""),
                "artists": [item.get("artist", {}).get("name", "")], "album": album.get("title", ""),
                "album_id": album.get("id"), "duration": item.get("duration"), "rank": item.get("rank", 0),
                "cover_url": album.get("cover_xl") or album.get("cover_big") or ""}

    def by_isrc(self, isrc):
        data = self.http.get(f"{BASE}/track/isrc:{isrc}")
        return self._full(data) if _ok(data) and data.get("id") else None

    def album(self, album_id):
        if album_id not in self.albums:
            data = self.http.get(f"{BASE}/album/{album_id}")
            self.albums[album_id] = data if _ok(data) else {}
        return self.albums[album_id]

    def complete(self, lite):
        """Full metadata for a search result: track position, date, ISRC, album artist, genre, cover."""
        data = self.http.get(f"{BASE}/track/{lite['source_id']}")
        return self._full(data) if _ok(data) else dict(lite)

    def _full(self, track):
        album_ref = track.get("album") or {}
        album = self.album(album_ref.get("id")) if album_ref.get("id") else {}
        contributors = [c.get("name") for c in track.get("contributors", []) if c.get("name")]
        main = track.get("artist", {}).get("name", "")
        artists = list(dict.fromkeys([main, *contributors] if main else contributors))
        date = track.get("release_date") or album.get("release_date") or album_ref.get("release_date") or ""
        genres = [g.get("name") for g in (album.get("genres") or {}).get("data", []) if g.get("name")]
        return {"source": self.name, "source_id": track.get("id"), "title": track.get("title", ""),
                "artists": artists, "album": album.get("title") or album_ref.get("title", ""),
                "album_id": album_ref.get("id"), "album_artist": (album.get("artist") or {}).get("name") or main,
                "date": date, "year": year_of(date), "track_no": track.get("track_position") or None,
                "track_total": album.get("nb_tracks") or None, "disc_no": track.get("disk_number") or None,
                "genre": genres[0] if genres else "", "isrc": track.get("isrc") or "",
                "duration": track.get("duration"), "record_type": album.get("record_type", ""),
                "cover_url": album.get("cover_xl") or album_ref.get("cover_xl") or ""}

    def search_albums(self, query, limit=8):
        data = self.http.get(f"{BASE}/search/album", {"q": query, "limit": limit})
        if not _ok(data):
            return []
        return [{"source": self.name, "album_id": item["id"], "album": item.get("title", ""),
                 "artists": [item.get("artist", {}).get("name", "")], "track_total": item.get("nb_tracks"),
                 "record_type": item.get("record_type", ""), "cover_url": item.get("cover_xl", "")}
                for item in data.get("data", [])]

    def album_tracks(self, album_id):
        """Every track of an album, fully described (for "Identificar álbum")."""
        album = self.album(album_id)
        tracks = (album.get("tracks") or {}).get("data", [])
        result = []
        for position, item in enumerate(tracks, start=1):
            date = album.get("release_date", "")
            genres = [g.get("name") for g in (album.get("genres") or {}).get("data", []) if g.get("name")]
            result.append({"source": self.name, "source_id": item.get("id"), "title": item.get("title", ""),
                           "artists": [item.get("artist", {}).get("name", "")], "album": album.get("title", ""),
                           "album_id": album_id, "album_artist": (album.get("artist") or {}).get("name", ""),
                           "date": date, "year": year_of(date), "track_no": position,
                           "track_total": album.get("nb_tracks"), "disc_no": item.get("disk_number") or None,
                           "genre": genres[0] if genres else "", "isrc": item.get("isrc", ""),
                           "duration": item.get("duration"), "record_type": album.get("record_type", ""),
                           "cover_url": album.get("cover_xl", "")})
        return result
