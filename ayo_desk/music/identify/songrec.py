"""Recognise a song by its sound with SongRec, an open-source Shazam client (only a fingerprint is sent)."""
import json
import re
import shutil
import subprocess


def available():
    return shutil.which("songrec") is not None


def bigger_cover(url):
    """Apple's CDN serves any size: ask for 1000×1000 instead of the 400×400 thumbnail."""
    return re.sub(r"/\d+x\d+(cc|bb)?\.(jpg|png)$", r"/1000x1000\1.\2", url or "")


def parse(output):
    """Shazam's JSON → candidate dict, or None when nothing matched."""
    try:
        data = json.loads(output)
    except (TypeError, ValueError):
        return None
    track = data.get("track") if isinstance(data, dict) else None
    if not track or not data.get("matches", [True]):
        return None
    metadata = {}
    for section in track.get("sections") or []:
        if section.get("type") == "SONG":
            metadata = {item.get("title", ""): item.get("text", "") for item in section.get("metadata") or []}
    images = track.get("images") or {}
    released = metadata.get("Released", "")
    return {"source": "shazam", "source_id": track.get("key"), "title": track.get("title", ""),
            "artists": [track.get("subtitle", "")] if track.get("subtitle") else [],
            "album": metadata.get("Album", ""), "year": int(released[:4]) if released[:4].isdigit() else None,
            "date": released, "isrc": track.get("isrc", ""), "genre": (track.get("genres") or {}).get("primary", ""),
            "cover_url": bigger_cover(images.get("coverarthq") or images.get("coverart") or ""),
            "label": metadata.get("Label", ""), "duration": None}


def recognize(path, timeout=90, run=subprocess.run):
    """Candidate for the song in `path`, or None if SongRec is missing, fails or finds nothing."""
    if not available():
        return None
    try:
        result = run(["songrec", "audio-file-to-recognized-song", str(path)], capture_output=True, text=True,
                     timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse(result.stdout) if result.returncode == 0 else None
