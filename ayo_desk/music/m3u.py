"""Import and export playlists as M3U/M3U8 (and read PLS), the formats every player understands."""
import configparser
import os
from pathlib import Path
from urllib.parse import unquote, urlparse


def _decode(data):
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _resolve(entry, base):
    entry = entry.strip()
    if not entry:
        return None
    if entry.startswith("file://"):
        return str(Path(unquote(urlparse(entry).path)))
    if "://" in entry:
        return entry  # a stream URL
    candidate = entry
    if not os.path.isabs(candidate) and "\\" in candidate and not (base / candidate).exists():
        candidate = candidate.replace("\\", "/")  # playlists written on Windows
    path = Path(candidate)
    if not path.is_absolute():
        path = base / path
    return os.path.normpath(path)


def read(path):
    """Return the entries (absolute paths or URLs) listed in an M3U, M3U8 or PLS file, in order."""
    path = Path(path)
    text = _decode(path.read_bytes())
    base = path.parent
    if path.suffix.casefold() == ".pls" or text.lstrip().lower().startswith("[playlist]"):
        parser = configparser.RawConfigParser(strict=False, interpolation=None)
        parser.optionxform = str.lower
        parser.read_string(text)
        section = parser["playlist"] if parser.has_section("playlist") else {}
        numbered = sorted((int(key[4:]), value) for key, value in section.items()
                          if key.startswith("file") and key[4:].isdigit())
        entries = [_resolve(value, base) for _n, value in numbered]
    else:
        entries = [_resolve(line, base) for line in text.splitlines() if line.strip() and not line.startswith("#")]
    return [entry for entry in entries if entry]


def write(path, tracks, relative=True):
    """Write an extended M3U8. `tracks` are dicts with path, and optionally duration/artist/title.

    Paths are relative to the playlist when possible, so the pair can be moved together.
    """
    path = Path(path)
    lines = ["#EXTM3U"]
    for track in tracks:
        location = str(track["path"])
        duration = int(round(track.get("duration") or -1))
        label = " - ".join(part for part in (track.get("artist"), track.get("title")) if part)
        lines.append(f"#EXTINF:{duration},{label or Path(location).stem}")
        if relative and "://" not in location:
            try:
                location = os.path.relpath(location, path.parent)
            except ValueError:  # different drives/mounts
                pass
        lines.append(location)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return len(tracks)
