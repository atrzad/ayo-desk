"""Lyrics: LRC parsing, local sources (sidecar files, embedded tags), LRCLIB, and sync helpers.

A lyrics object is {"lines": [(ms or None, text)], "synced": bool, "source": str, "offset": ms}.
"""
from bisect import bisect_right
from difflib import SequenceMatcher
from pathlib import Path
import re

from . import tags

try:
    import mutagen
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4Tags
    from mutagen._vorbis import VComment
except ImportError:  # pragma: no cover
    mutagen = None

STAMP = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
WORD_STAMP = re.compile(r"<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>")
OFFSET = re.compile(r"^\[offset:\s*([+-]?\d+)\s*\]", re.IGNORECASE | re.MULTILINE)
META_TAG = re.compile(r"^\[[a-z#]+:.*\]\s*$", re.IGNORECASE)
SIDECAR_SUFFIXES = (".lrc", "_private.lrc", ".LRC", ".txt")
LRCLIB = "https://lrclib.net/api"


def _ms(minutes, seconds, fraction):
    fraction = (fraction or "0").ljust(3, "0")[:3]
    return (int(minutes) * 60 + int(seconds)) * 1000 + int(fraction)


def parse(text, source=""):
    """Plain or LRC text → lyrics. Lines with several stamps repeat; metadata tags are dropped."""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").translate(tags.INVISIBLE)
    offset = OFFSET.search(text)
    timed, plain = [], []
    for raw in text.split("\n"):
        stamps = STAMP.findall(raw)
        content = WORD_STAMP.sub("", STAMP.sub("", raw)).strip()
        if stamps:
            timed += [(_ms(*stamp), content) for stamp in stamps]
        elif META_TAG.match(raw.strip()):
            continue
        else:
            plain.append(content)
    if timed and len(timed) >= max(1, len([p for p in plain if p]) // 2):
        timed.sort(key=lambda line: line[0])
        return {"lines": timed, "synced": True, "source": source, "offset": int(offset[1]) if offset else 0}
    lines = [(None, line) for line in plain]
    while lines and not lines[0][1]:
        lines.pop(0)
    while lines and not lines[-1][1]:
        lines.pop()
    return {"lines": lines, "synced": False, "source": source, "offset": 0}


def to_lrc(lyrics, title="", artist=""):
    head = [f"[ti:{title}]" if title else "", f"[ar:{artist}]" if artist else "", "[re:Ayo Música]"]
    body = []
    for ms, text in lyrics["lines"]:
        if ms is None:
            body.append(text)
        else:
            ms = max(0, ms - lyrics.get("offset", 0))  # bake the offset into the times
            body.append(f"[{ms // 60000:02d}:{ms // 1000 % 60:02d}.{ms % 1000 // 10:02d}]{text}")
    return "\n".join([line for line in head if line] + body) + "\n"


def current_index(lyrics, position_seconds):
    """Index of the line being sung at `position_seconds` (−1 before the first line)."""
    if not lyrics or not lyrics.get("synced"):
        return -1
    times = [ms for ms, _text in lyrics["lines"]]
    # LRC convention: a positive offset shows the lyrics earlier.
    return bisect_right(times, position_seconds * 1000 + lyrics.get("offset", 0)) - 1


def text_of(lyrics):
    return "\n".join(text for _ms, text in lyrics["lines"])


# ── local sources ──────────────────────────────────────────────────────────
def sidecars(path):
    """Lyrics files next to the song: "Song.lrc", "Song_private.lrc" (some downloaders), "Song.txt"."""
    file = Path(path)
    found = []
    for suffix in SIDECAR_SUFFIXES:
        candidate = file.with_name(file.stem + suffix)
        if candidate.is_file():
            found.append(candidate)
    return found


def _embedded_texts(path):
    """[(text, synced lines or None)] from ID3 USLT/SYLT, Vorbis LYRICS or MP4 ©lyr."""
    if mutagen is None:
        return []
    try:
        audio = mutagen.File(path)
    except Exception:  # noqa: BLE001 - unreadable tags simply mean no lyrics
        return []
    found = []
    if audio is None or audio.tags is None:
        return found
    if isinstance(audio.tags, ID3):
        for frame in audio.tags.getall("SYLT"):
            lines = [(int(ms), str(text).strip()) for text, ms in frame.text]
            if lines:
                found.append(("", sorted(lines)))
        found += [(str(frame.text), None) for frame in audio.tags.getall("USLT") if str(frame.text).strip()]
    elif isinstance(audio.tags, VComment):
        for key in ("syncedlyrics", "lyrics", "unsyncedlyrics"):
            found += [(value, None) for value in audio.tags.get(key, []) if value.strip()]
    elif isinstance(audio.tags, MP4Tags):
        found += [(value, None) for value in audio.tags.get("©lyr", []) if value.strip()]
    return found


def local(path):
    """Best local lyrics: synced beats plain; files next to the song beat embedded tags."""
    options = []
    for file in sidecars(path):
        try:
            options.append(parse(file.read_text(encoding="utf-8", errors="replace"), f"arquivo:{file.name}"))
        except OSError:
            continue
    for text, lines in _embedded_texts(path):
        if lines is not None:
            options.append({"lines": lines, "synced": True, "source": "embutida", "offset": 0})
        else:
            options.append(parse(text, "embutida"))
    options = [option for option in options if any(text for _ms, text in option["lines"])]
    if not options:
        return None
    return next((option for option in options if option["synced"]), options[0])


# ── LRCLIB ─────────────────────────────────────────────────────────────────
class LrcLib:
    """lrclib.net: free, open lyrics database (synced when available). No key needed."""

    def __init__(self, http):
        self.http = http

    def find(self, artist, title, album="", duration=None):
        """Best match as {"synced": text or "", "plain": text or "", "instrumental": bool} or None."""
        params = {"artist_name": artist, "track_name": title}
        if album:
            params["album_name"] = album
        if duration:
            params["duration"] = int(round(duration))
        exact = self.http.get(f"{LRCLIB}/get", params)
        if exact and not exact.get("statusCode"):
            return self._pick([exact], duration)
        results = self.http.get(f"{LRCLIB}/search", {"track_name": title, "artist_name": artist}) or []
        if not isinstance(results, list):
            return None
        return self._pick(results, duration)

    @staticmethod
    def _pick(results, duration):
        def fits(result):
            return not duration or not result.get("duration") or abs(result["duration"] - duration) <= 5
        results = [r for r in results if fits(r)]
        if not results:
            return None
        chosen = next((r for r in results if r.get("syncedLyrics")), results[0])
        return {"synced": chosen.get("syncedLyrics") or "", "plain": chosen.get("plainLyrics") or "",
                "instrumental": bool(chosen.get("instrumental"))}


# ── automatic sync (voice alignment) ───────────────────────────────────────
def _words(text):
    return re.sub(r"[^\w]+", " ", tags.fold(text)).split()


def align(lyrics, heard_words):
    """Give times to plain lyrics using words heard in the song [(word, start_ms, end_ms)].

    The lyric words and the heard words are aligned as two sequences; each line starts at its first
    matched word, and lines with no match are placed proportionally between their neighbours.
    Returns (synced lyrics, share of lyric words that were matched).
    """
    lines = [(None, text) for _ms, text in lyrics["lines"]]
    lyric_words, owner = [], []
    for number, (_ms, text) in enumerate(lines):
        for word in _words(text):
            lyric_words.append(word)
            owner.append(number)
    heard = [(tags.fold(word).strip(), start) for word, start, _end in heard_words]
    heard = [(re.sub(r"[^\w]+", "", word), start) for word, start in heard]
    heard = [(word, start) for word, start in heard if word]
    if not lyric_words or not heard:
        return None, 0.0
    matcher = SequenceMatcher(None, lyric_words, [word for word, _start in heard], autojunk=False)
    starts = [None] * len(lines)
    matched = 0
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            line = owner[block.a + k]
            matched += 1
            time = heard[block.b + k][1]
            if starts[line] is None or time < starts[line]:
                starts[line] = time
    # Times must grow line by line; a match that goes backwards is treated as noise.
    last = -1
    for number, time in enumerate(starts):
        if time is not None and time <= last:
            starts[number] = None
        elif time is not None:
            last = time
    known = [n for n, time in enumerate(starts) if time is not None]
    if not known:
        return None, 0.0
    text_lines = [n for n, (_ms, text) in enumerate(lines) if text]
    for number in text_lines:
        if starts[number] is not None:
            continue
        before = max((k for k in known if k < number), default=None)
        after = min((k for k in known if k > number), default=None)
        if before is not None and after is not None:
            span = starts[after] - starts[before]
            starts[number] = starts[before] + span * (number - before) // (after - before)
        elif before is not None:
            starts[number] = starts[before] + 2500 * (number - before)
        else:
            starts[number] = max(0, starts[after] - 2500 * (after - number))
    synced = [(starts[n], text) for n, (_ms, text) in enumerate(lines) if text and starts[n] is not None]
    synced.sort(key=lambda line: line[0])
    return ({"lines": synced, "synced": True, "source": "voz", "offset": 0}, matched / len(lyric_words))
