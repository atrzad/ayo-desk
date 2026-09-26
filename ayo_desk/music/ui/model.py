"""GObject items for list views and the in-memory library grouping (albums, artists, genres, folders)."""
from collections import Counter
from pathlib import Path
import re

from gi.repository import Gio, GObject

from .. import tags

UNKNOWN_ARTIST = "Artista desconhecido"
UNKNOWN_ALBUM = "Álbum desconhecido"
VARIOUS = "Vários artistas"
TRACK_FIELDS = ("path", "title", "artist", "album", "album_artist", "genre", "year", "track_no", "track_total",
                "disc_no", "disc_total", "duration", "bitrate", "sample_rate", "channels", "bits_per_sample",
                "codec", "cover", "plays", "skips", "last_played", "rating", "favorite", "added_at", "inferred",
                "search", "has_lyrics", "size", "rg_track_gain", "rg_album_gain")


def duration_text(seconds):
    seconds = max(0, int(seconds or 0))
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def long_duration(seconds):
    minutes = int((seconds or 0) // 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"


class Track(GObject.Object):
    __gtype_name__ = "AyoTrack"

    def __init__(self, data):
        super().__init__()
        self.update(data)
        self.album_key = None
        self.shown_artist = None  # artist borrowed from the album when the file has none

    def update(self, data):
        for field in TRACK_FIELDS:
            setattr(self, field, data.get(field))
        self.title = self.title or Path(self.path).stem
        self.plays = self.plays or 0
        self.rating = self.rating or 0
        self.favorite = bool(self.favorite)
        self.duration = self.duration or 0.0

    @property
    def display_artist(self):
        return self.artist or self.shown_artist or UNKNOWN_ARTIST

    @property
    def display_album(self):
        return self.album or UNKNOWN_ALBUM

    @property
    def folder(self):
        return str(Path(self.path).parent)

    def quality(self):
        parts = [self.codec or Path(self.path).suffix.lstrip(".").upper()]
        if self.bitrate:
            parts.append(f"{round(self.bitrate / 1000)} kbps")
        if self.sample_rate:
            parts.append(f"{self.sample_rate / 1000:g} kHz")
        if self.bits_per_sample:
            parts.append(f"{self.bits_per_sample} bits")
        if self.channels:
            parts.append({1: "Mono", 2: "Estéreo"}.get(self.channels, f"{self.channels} canais"))
        return " · ".join(parts)


class Group(GObject.Object):
    """An album, artist, genre or folder: a name plus its tracks."""
    __gtype_name__ = "AyoGroup"

    def __init__(self, kind, key, name):
        super().__init__()
        self.kind, self.key, self.name = kind, key, name
        self.tracks = []
        self.subtitle = ""
        self.cover = ""
        self.year = None
        self.artist = ""
        self.added_at = ""
        self.albums = []
        self.appears_on = []

    @property
    def duration(self):
        return sum(track.duration for track in self.tracks)

    def paths(self):
        return [track.path for track in self.tracks]


# Artist names that legitimately contain a comma.
KEEP_TOGETHER = {"tyler, the creator", "earth, wind & fire", "crosby, stills, nash & young",
                 "emerson, lake & palmer", "peter, paul and mary", "blood, sweat & tears"}
SPLIT_ARTISTS = re.compile(r"\s*,\s+|\s+(?:feat\.?|ft\.|featuring)\s+", re.IGNORECASE)


def artist_names(name):
    """"Sotam, Carla Sol" → ["Sotam", "Carla Sol"]: each collaborator gets the track."""
    if not name or name.casefold() in KEEP_TOGETHER:
        return [name]
    return [part for part in SPLIT_ARTISTS.split(name) if part] or [name]


def album_folder(path):
    folder = Path(path).parent
    return str(folder.parent if tags.DISC_FOLDER.match(folder.name) else folder)


def album_key(track):
    """Tagged albums group by album artist; untagged ones by folder, so features don't split them."""
    album = tags.fold(track.album or "")
    if track.album_artist:
        return ("artist", tags.fold(track.album_artist), album)
    return ("folder", album_folder(track.path), album)


def track_order(track):
    return (track.disc_no or 1, track.track_no or 10_000, tags.fold(track.title), track.path)


class Library:
    """Holds every track and derived groups. Views bind to the Gio.ListStores below."""

    def __init__(self):
        self.tracks = Gio.ListStore(item_type=Track)
        self.albums = Gio.ListStore(item_type=Group)
        self.artists = Gio.ListStore(item_type=Group)
        self.genres = Gio.ListStore(item_type=Group)
        self.folders = Gio.ListStore(item_type=Group)
        self.by_path = {}
        self.album_by_key = {}
        self.artist_by_name = {}
        self.position = {}
        self.listeners = []
        self.root = None

    def load(self, rows, root=None):
        self.root = root
        existing = self.by_path
        items = []
        for row in rows:
            track = existing.get(row["path"])
            if track is None:
                track = Track(row)
            else:
                track.update(row)
            items.append(track)
        self.by_path = {track.path: track for track in items}
        self.position = {track.path: n for n, track in enumerate(items)}
        self._group(items)
        self.tracks.splice(0, self.tracks.get_n_items(), items)
        for callback in list(self.listeners):
            callback()

    def _group(self, items):
        albums = {}
        for track in items:
            track.album_key = album_key(track)
            albums.setdefault(track.album_key, []).append(track)
        album_items = []
        for key, members in albums.items():
            members.sort(key=track_order)
            named = Counter(t.artist for t in members if t.artist)
            main = members[0].album_artist or (named.most_common(1)[0][0] if named else "")
            for track in members:
                track.shown_artist = None if track.artist else main or None
            group = Group("album", key, members[0].display_album)
            group.tracks = members
            if members[0].album_artist:
                group.artist = members[0].album_artist
            elif len(named) > 2 and named.most_common(1)[0][1] * 2 < len(members):
                group.artist = VARIOUS
            else:
                group.artist = main or UNKNOWN_ARTIST
            group.year = min((t.year for t in members if t.year), default=None)
            group.cover = next((t.cover for t in members if t.cover), "")
            group.added_at = max((t.added_at or "" for t in members), default="")
            group.subtitle = group.artist + (f" · {group.year}" if group.year else "")
            album_items.append(group)
        album_items.sort(key=lambda g: (tags.fold(g.name), tags.fold(g.artist)))
        self.album_by_key = {group.key: group for group in album_items}

        artists = {}

        def artist(name):
            return artists.setdefault(tags.fold(name), Group("artist", tags.fold(name), name))
        for track in items:
            for name in artist_names(track.display_artist):
                artist(name).tracks.append(track)
        for group in album_items:
            for name in artist_names(group.artist):
                if name != VARIOUS:
                    artist(name).albums.append(group)
        for group in album_items:  # "appears on": albums where the artist only has some tracks
            for name in {n for t in group.tracks for n in artist_names(t.display_artist)}:
                if group not in artist(name).albums:
                    artist(name).appears_on.append(group)
        for item in artists.values():
            item.tracks.sort(key=lambda t: (t.album_key, track_order(t)))
            item.cover = next((a.cover for a in item.albums + item.appears_on if a.cover), "")
            count = len(item.albums)
            item.subtitle = (f"{count} álbum · " if count == 1 else f"{count} álbuns · " if count else "") + \
                f"{len(item.tracks)} música" + ("s" if len(item.tracks) != 1 else "")
        artist_items = sorted(artists.values(), key=lambda g: (g.name == UNKNOWN_ARTIST, tags.fold(g.name)))
        self.artist_by_name = {group.key: group for group in artist_items}

        genres = {}
        for track in items:
            for name in [g.strip() for g in (track.genre or "").replace(";", ",").split(",") if g.strip()] or ["Sem gênero"]:
                genres.setdefault(tags.fold(name), Group("genre", tags.fold(name), name)).tracks.append(track)
        for genre in genres.values():
            genre.tracks.sort(key=lambda t: (tags.fold(t.display_artist), t.album_key, track_order(t)))
            genre.subtitle = f"{len(genre.tracks)} música" + ("s" if len(genre.tracks) != 1 else "")
            genre.cover = next((t.cover for t in genre.tracks if t.cover), "")
        genre_items = sorted(genres.values(), key=lambda g: (g.name == "Sem gênero", tags.fold(g.name)))

        folders = {}
        for track in items:
            folders.setdefault(track.folder, Group("folder", track.folder, "")).tracks.append(track)
        root = Path(self.root) if self.root else None
        for path, folder in folders.items():
            relative = Path(path)
            if root and (relative == root or root in relative.parents):
                relative = relative.relative_to(root)
            folder.name = str(relative) if str(relative) != "." else "Pasta principal"
            folder.tracks.sort(key=lambda t: tags.fold(Path(t.path).name))
            folder.subtitle = f"{len(folder.tracks)} música" + ("s" if len(folder.tracks) != 1 else "")
            folder.cover = next((t.cover for t in folder.tracks if t.cover), "")
        folder_items = sorted(folders.values(), key=lambda g: tags.fold(g.name))

        for store, groups in ((self.albums, album_items), (self.artists, artist_items),
                              (self.genres, genre_items), (self.folders, folder_items)):
            store.splice(0, store.get_n_items(), groups)

    def get(self, path):
        return self.by_path.get(path)

    def touch(self, *paths):
        """Ask every bound view to redraw these tracks (now playing marker, rating...)."""
        for path in paths:
            position = self.position.get(path)
            if position is not None:
                self.tracks.items_changed(position, 1, 1)
