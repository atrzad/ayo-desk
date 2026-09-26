"""Content-addressed cover cache shared by the UI, MPRIS and notifications.

Safe to call from worker threads: GdkPixbuf objects are never shared between threads.
"""
import hashlib
import os
from pathlib import Path
import tempfile

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib

THUMB_SIZE = 384
FOLDER_NAMES = ("cover", "folder", "front", "album", "albumart", "capa")
FOLDER_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
SIGNATURES = ((b"\x89PNG", ".png"), (b"\xff\xd8", ".jpg"), (b"GIF8", ".gif"), (b"BM", ".bmp"))


def cache_dir():
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "ayo-desk" / "covers"


def extension(data):
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return next((ext for magic, ext in SIGNATURES if data.startswith(magic)), ".img")


def _write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(handle, "wb") as output:
            output.write(data)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def store(data):
    """Cache image bytes, returning a stable key, or '' if the image cannot be decoded."""
    key = hashlib.sha1(data).hexdigest()[:24]
    folder = cache_dir()
    full = folder / (key + extension(data))
    thumb = folder / f"{key}-{THUMB_SIZE}.png"
    if full.is_file() and thumb.is_file():
        return key
    try:
        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        pixbuf = loader.get_pixbuf()
    except GLib.Error:
        return ""
    if pixbuf is None:
        return ""
    if not full.is_file():
        _write_atomic(full, data)
    if not thumb.is_file():
        width, height = pixbuf.get_width(), pixbuf.get_height()
        scale = min(1.0, THUMB_SIZE / max(width, height))
        small = pixbuf.scale_simple(max(1, round(width * scale)), max(1, round(height * scale)),
                                    GdkPixbuf.InterpType.BILINEAR)
        ok, buffer = small.save_to_bufferv("png", [], [])
        if ok:
            _write_atomic(thumb, buffer)
    return key


def folder_cover(directory):
    """A cover.jpg/folder.png/... next to the tracks, matched case-insensitively."""
    try:
        entries = {entry.name.casefold(): entry for entry in os.scandir(directory) if entry.is_file()}
    except OSError:
        return None
    for name in FOLDER_NAMES:
        for ext in FOLDER_EXTENSIONS:
            entry = entries.get(name + ext)
            if entry:
                return Path(entry.path)
    return None


def path(key, thumbnail=True):
    """Local file for a cached cover, or None."""
    if not key:
        return None
    folder = cache_dir()
    if thumbnail:
        thumb = folder / f"{key}-{THUMB_SIZE}.png"
        return thumb if thumb.is_file() else None
    for candidate in folder.glob(key + ".*"):
        return candidate
    return None


def prune(keep):
    """Delete cached covers no longer referenced by any track."""
    folder = cache_dir()
    if not folder.is_dir():
        return 0
    removed = 0
    for entry in folder.iterdir():
        key = entry.name.split(".", 1)[0].split("-", 1)[0]
        if key and not entry.name.startswith(".tmp-") and key not in keep:
            entry.unlink(missing_ok=True)
            removed += 1
    return removed
