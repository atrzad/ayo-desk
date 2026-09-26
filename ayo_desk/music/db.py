"""Music metadata, statistics and library queries on top of core.Store's connection."""
import datetime as dt

from . import tags

META_COLUMNS = ("path", "mtime_ns", "size", "title", "artist", "album", "album_artist", "genre", "year",
                "track_no", "track_total", "disc_no", "disc_total", "duration", "bitrate", "sample_rate",
                "channels", "bits_per_sample", "codec", "cover", "rg_track_gain", "rg_track_peak",
                "rg_album_gain", "rg_album_peak", "has_lyrics", "inferred", "search")


def now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


class MusicDB:
    def __init__(self, store):
        self.store = store
        self.db = store.db

    def signatures(self):
        return {row[0]: (row[1], row[2]) for row in self.db.execute("SELECT path, mtime_ns, size FROM music_meta")}

    def save_meta(self, metas):
        """Insert or refresh metadata; `added_at` is kept from the first time a file was seen."""
        columns = ", ".join(META_COLUMNS)
        marks = ", ".join("?" for _ in META_COLUMNS)
        updates = ", ".join(f"{c}=excluded.{c}" for c in META_COLUMNS if c != "path")
        stamp = now()
        with self.db:
            self.db.executemany(
                f"INSERT INTO music_meta({columns}, added_at) VALUES({marks}, ?) "
                f"ON CONFLICT(path) DO UPDATE SET {updates}",
                [tuple(meta.get(c) for c in META_COLUMNS) + (stamp,) for meta in metas])

    def prune_meta(self, keep):
        """Forget metadata of files that left the library; statistics are kept for their return."""
        keep = set(keep)
        stale = [(path,) for path in self.signatures() if path not in keep]
        with self.db:
            self.db.executemany("DELETE FROM music_meta WHERE path=?", stale)
        return len(stale)

    def cover_keys(self):
        return {row[0] for row in self.db.execute("SELECT DISTINCT cover FROM music_meta WHERE cover != ''")}

    def library(self):
        """Every library track as a dict: metadata (or a name-based guess) plus statistics."""
        paths = self.store.tracks()
        metas = {row["path"]: dict(row) for row in self.db.execute(
            "SELECT m.*, COALESCE(s.plays,0) AS plays, COALESCE(s.skips,0) AS skips, s.last_played, "
            "COALESCE(s.rating,0) AS rating, COALESCE(s.favorite,0) AS favorite "
            "FROM music_meta m LEFT JOIN music_stats s USING(path)")}
        result = []
        for path in paths:
            meta = metas.get(path)
            if meta is None:
                meta = tags.empty(path)
                for field, value in tags.infer_from_path(path).items():
                    meta[field] = value
                meta.update(search=tags.search_text(meta), cover="", plays=0, skips=0, last_played=None,
                            rating=0, favorite=0, added_at=None, pending=True)
            result.append(meta)
        return result

    def _stats(self, path):
        self.db.execute("INSERT OR IGNORE INTO music_stats(path) VALUES(?)", (path,))

    def set_rating(self, path, rating):
        rating = int(rating)
        if not 0 <= rating <= 5:
            raise ValueError("A nota vai de 0 a 5.")
        with self.db:
            self._stats(path)
            self.db.execute("UPDATE music_stats SET rating=? WHERE path=?", (rating, path))

    def set_favorite(self, path, favorite):
        with self.db:
            self._stats(path)
            self.db.execute("UPDATE music_stats SET favorite=? WHERE path=?", (int(bool(favorite)), path))

    def record_play(self, path, seconds, when=None):
        when = when or now()
        with self.db:
            self._stats(path)
            self.db.execute("UPDATE music_stats SET plays=plays+1, last_played=? WHERE path=?", (when, path))
            self.db.execute("INSERT INTO music_plays(path, played_at, seconds) VALUES(?,?,?)",
                            (path, when, float(seconds)))

    def record_skip(self, path):
        with self.db:
            self._stats(path)
            self.db.execute("UPDATE music_stats SET skips=skips+1 WHERE path=?", (path,))

    def resume_position(self, path):
        row = self.db.execute("SELECT resume_at FROM music_stats WHERE path=?", (path,)).fetchone()
        return row[0] if row else 0.0

    def set_resume_position(self, path, seconds):
        with self.db:
            self._stats(path)
            self.db.execute("UPDATE music_stats SET resume_at=? WHERE path=?", (max(0.0, float(seconds)), path))
