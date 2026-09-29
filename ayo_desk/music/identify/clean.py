"""Turn messy tags and file names (YouTube downloads, mostly) into search hints."""
from pathlib import Path
import re

from .. import tags

TOPIC = re.compile(r"\s+-\s+topic$", re.IGNORECASE)
PIPE = re.compile(r"\s*\|\s*")
DOWNLOADER = re.compile(r"\(\s*mp3[_ ]?\d+k?\s*\)|_private$|\s*\[[A-Za-z0-9_-]{11}\]\s*$", re.IGNORECASE)
BRACKETS = re.compile(r"[(\[]([^)\]]*)[)\]]")
TRAILING = re.compile(r"\s+[-–—]\s+([^-–—]+)$")
FEAT = re.compile(r"(?:^|\s)(?:feat\.?|ft\.?|featuring|part\.?|participa[çc][ãa]o(?: especial)?(?: de)?)\s+(.+)$",
                  re.IGNORECASE)
NOISE_WORDS = re.compile(
    r"^(?:official|oficial|music video|lyric video|lyrics?|letra|visuali[sz]er|audio|áudio|video|vídeo|clipe|"
    r"hd|hq|4k|mv|m/v|explicit|clean|legendado|tradução|remaster(?:ed|izado)?(?: \d{4})?|"
    r".*play-?through.*|.*official.*|.*oficial.*|prod\.? .*|produced by .*|\d{4})$", re.IGNORECASE)
ALBUM_IN_NAME = re.compile(r"[(\[]\s*(?:álbum|album|ep|disco)\s*[-–—:]?\s*([^)\]]+?)\s*[)\]]", re.IGNORECASE)
VERSIONS = {
    "live": ("live", "ao vivo", "en vivo", "live session"),
    "remix": ("remix", "rmx", "mix)"),
    "instrumental": ("instrumental",),
    "acoustic": ("acoustic", "acústico", "acustico", "unplugged"),
    "sped": ("sped up", "speed up", "nightcore"),
    "slowed": ("slowed", "reverb"),
    "demo": ("demo",),
    "karaoke": ("karaoke",),
    "cover": ("cover",),
}


def clean(text):
    """Text without invisible characters, yt-dlp substitutions or downloader suffixes."""
    text = str(text or "").translate(tags.INVISIBLE).translate(tags.FULLWIDTH)
    text = DOWNLOADER.sub("", text)
    # Downloaders replace characters that are unsafe in file names with "_":
    text = re.sub(r"(?<=\w)_(?=\w)", "'", text)      # You_re → You're
    text = re.sub(r"(?<=\w)_ ", ": ", text)            # Operation_ Greenbacks → Operation: Greenbacks
    text = re.sub(r"\s+_\s+", " | ", text)             # OTÁRIO _ Seu Pereira → OTÁRIO | Seu Pereira
    return re.sub(r"\s{2,}", " ", text).strip(" _-–—|")


def artist_list(text):
    """"Tyler |  The Creator" → ["Tyler, The Creator"]; "Ruas Mc, MELI, Bradoc" → three artists."""
    text = TOPIC.sub("", clean(text))
    if not text:
        return []
    joined = PIPE.sub(", ", text)
    from ..ui.model import artist_names  # the same splitting rules the library uses
    return [name for name in artist_names(joined) if name]


def versions_of(text):
    """Version markers ("live", "remix"...) found in brackets or after a trailing " - "."""
    found = set()
    parts = [group for group in BRACKETS.findall(text or "")]
    trailing = TRAILING.search(text or "")
    if trailing:
        parts.append(trailing[1])
    for part in parts:
        lowered = tags.fold(part)
        for version, words in VERSIONS.items():
            if any(tags.fold(word).rstrip(")") in lowered for word in words):
                found.add(version)
    return found


def core_title(text):
    """The title without brackets, featured artists, version suffixes and noise: used to compare."""
    text = clean(text)
    text = BRACKETS.sub(" ", text)
    trailing = TRAILING.search(text)
    if trailing and (versions_of(text) or NOISE_WORDS.match(trailing[1].strip())):
        text = text[:trailing.start()]
    text = FEAT.sub("", text)
    return re.sub(r"\s{2,}", " ", text).strip(" _-–—")


def featured(text):
    """Artists credited in the title: "Song (feat. A & B)" → ["A", "B"]."""
    names = []
    for group in [*BRACKETS.findall(text or ""), text or ""]:
        match = FEAT.search(group)
        if match:
            names += [n.strip() for n in re.split(r",|&| e | and | x ", match[1]) if n.strip()]
    return list(dict.fromkeys(names))


def display_title(text):
    """Title as shown and written: noise removed, real parentheses such as "(Epilogue)" kept."""
    text = clean(text)
    kept = [group for group in BRACKETS.findall(text) if not NOISE_WORDS.match(group.strip())]
    base = BRACKETS.sub("", text).strip()
    trailing = TRAILING.search(base)
    if trailing and NOISE_WORDS.match(trailing[1].strip()):
        base = base[:trailing.start()].strip()
    return re.sub(r"\s{2,}", " ", " ".join([base, *(f"({g.strip()})" for g in kept)])).strip()


def from_filename(path):
    """(artist, title) guessed from "Artist - Title (Official Video)(MP3_160K).mp3" style names."""
    stem = clean(Path(path).stem)
    match = tags.TRACK_PREFIX.match(stem)
    if match:
        stem = match["rest"]
    for separator in (" - ", " – ", " — "):
        if separator in stem:
            artist, title = stem.split(separator, 1)
            return artist.strip(), title.strip()
    return None, stem


def hints(info):
    """Everything we know about a file, cleaned. `info` has path, title, artist, album, album_artist, duration."""
    path = info["path"]
    file_artist, file_title = from_filename(path)
    title = clean(info.get("title")) or file_title
    artists = artist_list(info.get("artist") or "") or artist_list(file_artist or "")
    album = TOPIC.sub("", clean(info.get("album")))
    album_artist = TOPIC.sub("", clean(info.get("album_artist")))
    named_album = ALBUM_IN_NAME.search(f"{title} {clean(Path(path).stem)}")
    if not album and named_album:  # "MENSAGEM FAVORITA - DJ ARANA (ÁLBUM - ROCK PESADO 2)"
        album = named_album[1].strip()
    title = ALBUM_IN_NAME.sub("", title).strip()
    # YouTube downloads often put the channel/artist name where the album should be.
    if album and (tags.fold(album) in {tags.fold(a) for a in artists} or tags.fold(album) == tags.fold(album_artist)):
        album = ""
    guests = [g for g in featured(title) if tags.fold(g) not in {tags.fold(a) for a in artists}]
    stem = clean(Path(path).stem)
    numbered = tags.TRACK_PREFIX.match(stem)
    if numbered:
        stem = numbered["rest"]
    queries = []
    core = core_title(title)
    for artist in artists:  # "Deftones – Rx Queen" when the artist is already known
        rest = core[len(artist):]
        if tags.fold(core).startswith(tags.fold(artist)) and rest[:3].strip() in ("-", "–", "—", "|") and rest.strip():
            core = rest.strip().lstrip("-–—|").strip()
    split_core = core  # keeps the " | " separators for the alternative readings below
    core = core.replace(" | ", " ")
    if artists and core:
        queries.append(f"{artists[0]} {core}")
    if core and (not artists or file_artist):
        queries.append(f"{file_artist} {core}" if file_artist and not artists else core)
    if core and album and not artists:
        queries.append(f"{album} {core}")
    stem_query = core_title(stem).replace(" | ", " ")
    if stem_query and stem_query not in (core, ""):
        queries.append(stem_query)
    readings = []
    if not artist_list(info.get("artist") or ""):
        readings = _readings(split_core if not file_artist else f"{file_artist} | {split_core}")
    return {"path": path, "title": title, "core": core, "artists": artists, "guests": guests, "album": album,
            "duration": float(info.get("duration") or 0) or None, "versions": versions_of(title) | versions_of(stem),
            "queries": _unique(queries), "readings": readings}


def _readings(text):
    """Without an artist tag a name is ambiguous: "Título - Artista", "Artista - Título",
    "Artista | Artista | Título". Every plausible (title, artists) split is kept and scored."""
    parts = [part.strip() for part in re.split(r"\s+[|–—-]\s+", text) if part.strip()]
    if len(parts) < 2:
        return []
    options = [(parts[-1], parts[:-1]), (parts[0], parts[1:])]
    if len(parts) > 2:
        options.append((parts[1], [parts[0], *parts[2:]]))
    readings = []
    for title, artists in options:
        names = [name for artist in artists for name in artist_list(artist)]
        if core_title(title) and names:
            readings.append({"title": title, "core": core_title(title), "artists": names})
    return readings


def _unique(queries):
    """Drop queries that only differ in punctuation or case from an earlier one."""
    seen, result = set(), []
    for query in queries:
        key = " ".join(re.sub(r"[^\w]+", " ", tags.fold(query)).split())
        if key and key not in seen:
            seen.add(key)
            result.append(query)
    return result[:3]
