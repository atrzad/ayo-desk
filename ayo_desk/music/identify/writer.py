"""Write identified metadata into audio files (mutagen), keeping a backup to undo it later.

Only the fields below and the front cover are touched: audio data, file names, lyrics and any
other tag stay as they were.
"""
import base64

from .. import covers, tags

try:
    import mutagen
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TSRC
    from mutagen.mp4 import MP4Cover, MP4FreeForm, MP4Tags
    from mutagen._vorbis import VComment
except ImportError:  # pragma: no cover - the UI explains how to install python-mutagen
    mutagen = None

FIELDS = ("title", "artist", "album", "album_artist", "date", "track_no", "track_total", "disc_no", "genre", "isrc")
ID3_FRAMES = {"title": TIT2, "artist": TPE1, "album": TALB, "album_artist": TPE2, "date": TDRC, "genre": TCON,
              "isrc": TSRC} if mutagen else {}
VORBIS_KEYS = {"title": "title", "artist": "artist", "album": "album", "album_artist": "albumartist",
               "date": "date", "genre": "genre", "isrc": "isrc", "track_no": "tracknumber",
               "track_total": "tracktotal", "disc_no": "discnumber"}
MP4_KEYS = {"title": "©nam", "artist": "©ART", "album": "©alb", "album_artist": "aART", "date": "©day",
            "genre": "©gen"}
MP4_ISRC = "----:com.apple.iTunes:ISRC"


def available():
    return mutagen is not None


def snapshot(path):
    """Current values of every field we may change (empty when absent) and the cover's cache key."""
    meta, cover = tags.read(path, with_cover=True)
    inferred = set((meta.get("inferred") or "").split(","))
    values = {field: ("" if field in inferred else meta.get(field) or "")
              for field in ("title", "artist", "album", "album_artist", "genre")}
    values["date"] = "" if "year" in inferred else str(meta.get("year") or "")
    for field in ("track_no", "track_total", "disc_no"):
        values[field] = None if field in inferred else meta.get(field)
    values["isrc"] = _read_isrc(path)
    return {"values": values, "cover": covers.store(cover) if cover else ""}


def _read_isrc(path):
    audio = mutagen.File(path) if mutagen else None
    found = None
    if audio is not None and audio.tags is not None:
        if isinstance(audio.tags, ID3):
            found = audio.tags.get("TSRC")
            return str(found.text[0]) if found and found.text else ""
        if isinstance(audio.tags, VComment):
            found = audio.tags.get("isrc")
        elif isinstance(audio.tags, MP4Tags):
            found = audio.tags.get(MP4_ISRC)
            return bytes(found[0]).decode("utf-8", "replace") if found else ""
    return str(found[0]) if found else ""


def _number(value, total):
    if not value:
        return ""
    return f"{int(value)}/{int(total)}" if total else str(int(value))


def write(path, values, cover_key=None, remove_cover=False):
    """Set the given fields (an empty value deletes the tag) and, optionally, the front cover."""
    if mutagen is None:
        raise RuntimeError("Instale o python-mutagen para gravar tags: sudo pacman -S python-mutagen")
    audio = mutagen.File(path)
    if audio is None:
        raise ValueError("Formato de arquivo não suportado para gravar tags.")
    if audio.tags is None:
        audio.add_tags()
    image = None
    if cover_key:
        file = covers.path(cover_key, thumbnail=False)
        if file is None:
            raise ValueError("A capa escolhida não está mais no cache.")
        image = file.read_bytes()
    kind = audio.tags
    if isinstance(kind, ID3):
        _write_id3(audio, values, image, remove_cover)
    elif isinstance(kind, VComment):
        _write_vorbis(audio, values, image, remove_cover)
    elif isinstance(kind, MP4Tags):
        _write_mp4(audio, values, image, remove_cover)
    else:
        raise ValueError("Este tipo de tag ainda não é suportado para gravação.")
    return True


def _mime(data):
    return "image/png" if covers.extension(data) == ".png" else "image/jpeg"


def _write_id3(audio, values, image, remove_cover):
    frames = audio.tags
    for field, frame in ID3_FRAMES.items():
        if field in values:
            frames.delall(frame.__name__)
            if values[field] not in (None, ""):
                frames.add(frame(encoding=3, text=[str(values[field])]))
    for field, total, frame in (("track_no", "track_total", TRCK), ("disc_no", None, TPOS)):
        if field in values or (total and total in values):
            # An explicit None (restoring a file that had no number) deletes; otherwise keep the old number.
            number = values[field] if field in values else _existing_number(frames.get(frame.__name__))
            text = _number(number, values.get(total) if total else None)
            frames.delall(frame.__name__)
            if text:
                frames.add(frame(encoding=3, text=[text]))
    if image is not None or remove_cover:
        frames.delall("APIC")
        if image is not None:
            frames.add(APIC(encoding=3, mime=_mime(image), type=3, desc="", data=image))
    version = frames.version[1] if frames.version else 4
    audio.save(v2_version=3 if version == 3 else 4)


def _existing_number(frame):
    if frame is None or not frame.text:
        return None
    first = str(frame.text[0]).split("/")[0]
    return int(first) if first.isdigit() else None


def _write_vorbis(audio, values, image, remove_cover):
    for field, key in VORBIS_KEYS.items():
        if field in values:
            if values[field] in (None, ""):
                if key in audio.tags:
                    del audio.tags[key]
            else:
                audio.tags[key] = [str(values[field])]
    if image is not None or remove_cover:
        picture = None
        if image is not None:
            picture = Picture()
            picture.type, picture.mime, picture.data = 3, _mime(image), image
        if isinstance(audio, FLAC):
            audio.clear_pictures()
            if picture is not None:
                audio.add_picture(picture)
        else:
            if "metadata_block_picture" in audio.tags:
                del audio.tags["metadata_block_picture"]
            if picture is not None:
                audio.tags["metadata_block_picture"] = [base64.b64encode(picture.write()).decode("ascii")]
    audio.save()


def _write_mp4(audio, values, image, remove_cover):
    atoms = audio.tags
    for field, key in MP4_KEYS.items():
        if field in values:
            if values[field] in (None, ""):
                atoms.pop(key, None)
            else:
                atoms[key] = [str(values[field])]
    if "track_no" in values or "track_total" in values:
        old = (atoms.get("trkn") or [(0, 0)])[0]
        number, total = values.get("track_no", old[0]) or 0, values.get("track_total", old[1]) or 0
        if number:
            atoms["trkn"] = [(int(number), int(total))]
        else:
            atoms.pop("trkn", None)
    if "disc_no" in values:
        if values["disc_no"]:
            atoms["disk"] = [(int(values["disc_no"]), 0)]
        else:
            atoms.pop("disk", None)
    if "isrc" in values:
        if values["isrc"]:
            atoms[MP4_ISRC] = [MP4FreeForm(str(values["isrc"]).encode("utf-8"))]
        else:
            atoms.pop(MP4_ISRC, None)
    if image is not None or remove_cover:
        atoms.pop("covr", None)
        if image is not None:
            kind = MP4Cover.FORMAT_PNG if _mime(image) == "image/png" else MP4Cover.FORMAT_JPEG
            atoms["covr"] = [MP4Cover(image, imageformat=kind)]
    audio.save()


def apply(path, change):
    """Back up, write `change` (fields + optional "cover" key) and return the backup to store."""
    backup = snapshot(path)
    values = {field: value for field, value in change.items() if field in FIELDS}
    write(path, values, cover_key=change.get("cover") or None)
    return backup


def restore(path, backup):
    """Put back exactly what `snapshot` saw (fields that were empty are removed again)."""
    write(path, dict(backup["values"]), cover_key=backup.get("cover") or None,
          remove_cover=not backup.get("cover"))
