"""Read audio tags with mutagen and infer what is missing from file and folder names.

Works without mutagen (only names are used). Nothing here writes to disk.
"""
import base64
from pathlib import Path
import re
import unicodedata

try:
    import mutagen
    from mutagen.apev2 import APEv2
    from mutagen.asf import ASFTags
    from mutagen.flac import Picture
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4Tags
    from mutagen._vorbis import VComment
except ImportError:  # pragma: no cover - exercised only on systems without python-mutagen
    mutagen = None

TEXT_FIELDS = ("title", "artist", "album", "album_artist", "genre")
NUMBER_FIELDS = ("year", "track_no", "track_total", "disc_no", "disc_total")
MAX_COVER_BYTES = 20 * 1024 * 1024

# yt-dlp and Windows-safe renamers replace reserved characters with full-width look-alikes.
FULLWIDTH = str.maketrans({"：": ":", "？": "?", "／": "/", "＼": "\\", "＊": "*", "＂": '"',
                           "＜": "<", "＞": ">", "｜": "|"})
YOUTUBE_ID = re.compile(r"\s*\[[A-Za-z0-9_-]{11}\]\s*$")
# "01 - Title", "1. Title", "1-03 Title" (disc-track), "03 Title" (zero-padded only).
TRACK_PREFIX = re.compile(r"^(?:(?P<disc>\d{1,2})[-.](?P<dtrack>\d{1,3})\s+|(?P<track>\d{1,3})\s*[-–—._)]\s*"
                          r"|(?P<padded>0\d{1,2})\s+)(?P<rest>\S.*)$")
NOISE = re.compile(r"\s*[(\[](?:official\s+(?:music\s+)?(?:video|audio|lyric\s+video|visualizer)|"
                   r"lyrics?\s+video|lyrics?|visualizer|audio|video\s+oficial|clipe\s+oficial|"
                   r"[áa]udio\s+oficial|letra|legendado|hd|hq|4k|mp3[_ ]?\d+k?|\d{2,3}\s*kbps)[)\]]",
                   re.IGNORECASE)
ALBUM_PREFIX = re.compile(r"^(?:album|álbum|ep|single)\s*[-–—:]\s*", re.IGNORECASE)
DISC_FOLDER = re.compile(r"^(?:cd|dis[ck]|disco)\s*(\d{1,2})$", re.IGNORECASE)
YEAR = re.compile(r"(?<!\d)(1[89]\d\d|2[01]\d\d)(?!\d)")
ALBUM_YEAR = re.compile(r"^(\S.*?)\s*(?:[-–—]\s*)?[(\[]?(1[89]\d\d|2[01]\d\d)[)\]]?$")

ID3_KEYS = {"title": "TIT2", "artist": "TPE1", "album": "TALB", "album_artist": "TPE2",
            "genre": "TCON", "date": "TDRC", "track": "TRCK", "disc": "TPOS"}
VORBIS_KEYS = {"title": ("title",), "artist": ("artist",), "album": ("album",),
               "album_artist": ("albumartist", "album artist", "album_artist"), "genre": ("genre",),
               "date": ("date", "year", "originaldate"), "track": ("tracknumber",),
               "track_total": ("tracktotal", "totaltracks"), "disc": ("discnumber",),
               "disc_total": ("disctotal", "totaldiscs")}
MP4_KEYS = {"title": "©nam", "artist": "©ART", "album": "©alb", "album_artist": "aART",
            "genre": "©gen", "date": "©day"}
APE_KEYS = {"title": ("Title",), "artist": ("Artist",), "album": ("Album",),
            "album_artist": ("Album Artist", "AlbumArtist"), "genre": ("Genre",), "date": ("Year",),
            "track": ("Track",), "disc": ("Disc",)}
ASF_KEYS = {"title": ("Title",), "artist": ("Author",), "album": ("WM/AlbumTitle",),
            "album_artist": ("WM/AlbumArtist",), "genre": ("WM/Genre",), "date": ("WM/Year",),
            "track": ("WM/TrackNumber",), "disc": ("WM/PartOfSet",)}
CODECS = {"MP3": "MP3", "EasyMP3": "MP3", "FLAC": "FLAC", "OggVorbis": "Vorbis", "OggOpus": "Opus",
          "OggFLAC": "FLAC", "WAVE": "WAV", "AIFF": "AIFF", "ASF": "WMA", "MonkeysAudio": "APE",
          "WavPack": "WavPack", "MusepackInfo": "Musepack", "Musepack": "Musepack", "TrueAudio": "TTA"}


def available():
    return mutagen is not None


def fold(text):
    """Case- and accent-insensitive form used for search and sorting ("São" → "sao")."""
    # Compatibility-decompose first so styled letters (𝐌, Ｍ) become plain before case folding.
    text = unicodedata.normalize("NFKD", str(text)).casefold()
    return "".join(char for char in text if not unicodedata.combining(char))


def empty(path):
    return {"path": str(path), "title": "", "artist": "", "album": "", "album_artist": "", "genre": "",
            "year": None, "track_no": None, "track_total": None, "disc_no": None, "disc_total": None,
            "duration": 0.0, "bitrate": 0, "sample_rate": 0, "channels": 0, "bits_per_sample": None,
            "codec": Path(path).suffix.lstrip(".").upper(), "rg_track_gain": None, "rg_track_peak": None,
            "rg_album_gain": None, "rg_album_peak": None, "has_lyrics": 0, "inferred": ""}


def pair(value):
    """"3/12" → (3, 12); "03" → (3, None); garbage → (None, None)."""
    if isinstance(value, tuple):
        first, second = (list(value) + [0, 0])[:2]
        return (int(first) or None, int(second) or None)
    match = re.match(r"\s*(\d+)\s*(?:/\s*(\d+))?", str(value or ""))
    if not match:
        return None, None
    return int(match[1]) or None, int(match[2]) if match[2] and int(match[2]) else None


def clean_name(text):
    text = unicodedata.normalize("NFC", text).translate(FULLWIDTH)
    text = YOUTUBE_ID.sub("", text)
    previous = None
    while previous != text:
        previous, text = text, NOISE.sub("", text).strip()
    return re.sub(r"\s{2,}", " ", text).strip(" _-")


def infer_from_path(path, root=None):
    """Guess title/track/album/disc/artist from the layout: [Artist/]Album[/CD n]/NN - Title.ext."""
    file = Path(path)
    guess = {}
    stem = clean_name(file.stem)
    match = TRACK_PREFIX.match(stem)
    if match:
        if match["disc"]:
            guess["disc_no"], guess["track_no"] = int(match["disc"]), int(match["dtrack"])
        else:
            guess["track_no"] = int(match["track"] or match["padded"])
        stem = match["rest"].strip()
    guess["title"] = stem or file.stem
    folder = file.parent
    if DISC_FOLDER.match(folder.name):
        guess.setdefault("disc_no", int(DISC_FOLDER.match(folder.name)[1]))
        folder = folder.parent
    base = Path(root).resolve() if root else None
    inside_root = base is None or (folder != base and base in folder.parents)
    if inside_root and folder.name:
        album = clean_name(ALBUM_PREFIX.sub("", folder.name))
        # "Salad Days (2014)" → album + year; an album named only "1989" stays as is.
        dated = ALBUM_YEAR.match(album)
        if dated:
            album, guess["year"] = dated[1], int(dated[2])
        guess["album"] = album
        parent = folder.parent
        if base is not None and parent != base and base in parent.parents:
            guess["artist"] = clean_name(parent.name)
    return guess


INVISIBLE = dict.fromkeys(map(ord, "\ufeff\u200b\u200c\u200d\u2060\x00"))


def _join(values):
    parts = [str(v).translate(INVISIBLE).strip() for v in values]
    parts = [part for part in parts if part]
    return ", ".join(dict.fromkeys(parts))


def _first(tags, keys):
    for key in keys:
        try:
            value = tags.get(key)
        except (KeyError, ValueError):
            value = None
        if value:
            return value if isinstance(value, list) else [value]
    return None


def _gain(text):
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", str(text))
    return float(match[0].replace(",", ".")) if match else None


def _read_fields(audio):
    tags, raw = audio.tags, {}
    if tags is None:
        return raw
    if isinstance(tags, ID3):
        for field, key in ID3_KEYS.items():
            frame = tags.get(key)
            if frame is None:
                continue
            raw[field] = _join(frame.genres) if key == "TCON" else _join(str(t) for t in frame.text)
        for frame in tags.getall("TXXX"):
            raw[frame.desc.casefold()] = _join(frame.text)
        raw["lyrics"] = bool(tags.getall("USLT") or tags.getall("SYLT"))
    elif isinstance(tags, VComment):
        for field, keys in VORBIS_KEYS.items():
            value = _first(tags, keys)
            if value:
                raw[field] = _join(value)
        for key in ("replaygain_track_gain", "replaygain_track_peak", "replaygain_album_gain", "replaygain_album_peak"):
            value = _first(tags, (key,))
            if value:
                raw[key] = value[0]
        raw["lyrics"] = bool(_first(tags, ("lyrics", "unsyncedlyrics")))
    elif isinstance(tags, MP4Tags):
        for field, key in MP4_KEYS.items():
            if key in tags:
                raw[field] = _join(tags[key])
        if "trkn" in tags:
            raw["track"] = tags["trkn"][0]
        if "disk" in tags:
            raw["disc"] = tags["disk"][0]
        for key, value in tags.items():
            if key.casefold().startswith("----:com.apple.itunes:replaygain_"):
                raw[key.split(":")[-1].casefold()] = bytes(value[0]).decode("utf-8", "replace")
        raw["lyrics"] = "©lyr" in tags
    elif isinstance(tags, (APEv2, ASFTags)):
        keys = APE_KEYS if isinstance(tags, APEv2) else ASF_KEYS
        for field, names in keys.items():
            value = _first(tags, names)
            if value:
                raw[field] = _join(str(v) for v in value)
        raw["lyrics"] = bool(_first(tags, ("Lyrics", "WM/Lyrics")))
    return raw


def _cover(audio):
    tags = audio.tags
    pictures = []
    if tags is None:
        pass
    elif isinstance(tags, ID3):
        pictures = [(frame.type, frame.data) for frame in tags.getall("APIC")]
    elif isinstance(tags, VComment):
        for encoded in _first(tags, ("metadata_block_picture",)) or []:
            try:
                picture = Picture(base64.b64decode(encoded))
                pictures.append((picture.type, picture.data))
            except (ValueError, TypeError, mutagen.MutagenError):
                continue
    elif isinstance(tags, MP4Tags):
        pictures = [(3, bytes(cover)) for cover in tags.get("covr", [])]
    elif isinstance(tags, APEv2) and "Cover Art (Front)" in tags:
        pictures = [(3, bytes(tags["Cover Art (Front)"].value).split(b"\0", 1)[-1])]
    pictures += [(picture.type, picture.data) for picture in getattr(audio, "pictures", [])]
    pictures = [(kind, data) for kind, data in pictures if data and len(data) <= MAX_COVER_BYTES]
    # Prefer the front cover (type 3), then whatever comes first.
    pictures.sort(key=lambda item: item[0] != 3)
    return pictures[0][1] if pictures else None


def read(path, root=None, with_cover=False):
    """Return (metadata, cover_bytes). Tags win; names fill only what is missing."""
    meta = empty(path)
    cover = None
    if mutagen is not None:
        try:
            audio = mutagen.File(path)
        except (mutagen.MutagenError, OSError, ValueError, EOFError):
            audio = None
        if audio is not None:
            info = audio.info
            meta["duration"] = float(getattr(info, "length", 0) or 0)
            meta["bitrate"] = int(getattr(info, "bitrate", 0) or 0)
            meta["sample_rate"] = int(getattr(info, "sample_rate", 0) or 0)
            meta["channels"] = int(getattr(info, "channels", 0) or 0)
            meta["bits_per_sample"] = getattr(info, "bits_per_sample", None) or None
            codec = CODECS.get(type(audio).__name__, meta["codec"])
            if type(audio).__name__ == "MP4":
                codec = "ALAC" if "alac" in str(getattr(info, "codec", "")) else "AAC"
            meta["codec"] = codec
            raw = _read_fields(audio)
            for field in TEXT_FIELDS:
                meta[field] = raw.get(field, "")
            meta["year"] = int(YEAR.search(raw["date"])[1]) if YEAR.search(raw.get("date", "")) else None
            meta["track_no"], meta["track_total"] = pair(raw.get("track"))
            meta["disc_no"], meta["disc_total"] = pair(raw.get("disc"))
            meta["track_total"] = meta["track_total"] or pair(raw.get("track_total"))[0]
            meta["disc_total"] = meta["disc_total"] or pair(raw.get("disc_total"))[0]
            for kind in ("track", "album"):
                meta[f"rg_{kind}_gain"] = _gain(raw.get(f"replaygain_{kind}_gain", "")) \
                    if raw.get(f"replaygain_{kind}_gain") else None
                meta[f"rg_{kind}_peak"] = _gain(raw.get(f"replaygain_{kind}_peak", "")) \
                    if raw.get(f"replaygain_{kind}_peak") else None
            meta["has_lyrics"] = int(bool(raw.get("lyrics")))
            if with_cover:
                cover = _cover(audio)
    file = Path(path)
    if any(file.with_name(file.stem + ext).is_file() for ext in (".lrc", ".LRC", "_private.lrc")):
        meta["has_lyrics"] = 1
    inferred = []
    for field, value in infer_from_path(path, root).items():
        if not meta.get(field):
            meta[field] = value
            inferred.append(field)
    meta["inferred"] = ",".join(inferred)
    return meta, cover


def search_text(meta):
    parts = (meta.get("title"), meta.get("artist"), meta.get("album"), meta.get("album_artist"),
             meta.get("genre"), meta.get("year"), Path(meta["path"]).name)
    return fold(" ".join(str(p) for p in parts if p))
