"""How sure are we that a candidate is the same recording as the file? A 0–100 score with reasons."""
from difflib import SequenceMatcher
import re

from .. import tags
from .clean import core_title, versions_of

AUTO = 90       # write without asking
REVIEW = 60     # show for review; below this the song counts as not found


def words(text):
    return re.sub(r"[^\w]+", " ", tags.fold(text).replace("v.s.", "vs")).split()


def title_similarity(ours, theirs):
    a, b = " ".join(words(core_title(ours))), " ".join(words(core_title(theirs)))
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    shorter, longer = sorted((set(a.split()), set(b.split())), key=len)
    if shorter and shorter <= longer and len(shorter) >= max(1, len(longer) - 2):
        ratio = max(ratio, 0.9)
    return ratio


def artist_similarity(ours, theirs):
    """Best match between any of our artists and any credited artist; None when we know none."""
    if not ours:
        return None
    best = 0.0
    for mine in ours:
        mine_words = words(mine)
        for other in theirs:
            other_words = words(other)
            if not mine_words or not other_words:
                continue
            if mine_words == other_words:
                return 1.0
            if set(mine_words) <= set(other_words) or set(other_words) <= set(mine_words):
                best = max(best, 0.92)  # "Seu Pereira" ⊂ "Seu Pereira e Coletivo 401"
            best = max(best, SequenceMatcher(None, " ".join(mine_words), " ".join(other_words)).ratio())
    return best


def duration_points(ours, theirs):
    """1 for the same length, down to −1 when clearly a different recording; None when unknown."""
    if not ours or not theirs:
        return None
    difference = abs(float(ours) - float(theirs))
    for limit, points in ((2, 1.0), (4, 0.85), (8, 0.5), (15, 0.2)):
        if difference <= limit:
            return points
    return -1.0


def score(hint, candidate):
    """(0–100, reasons) for `candidate` (title, artists, duration) against our cleaned `hint`."""
    reasons = []
    title = title_similarity(hint.get("title") or hint.get("core", ""), candidate.get("title", ""))
    artist = artist_similarity(hint.get("artists") or [], candidate.get("artists") or [])
    duration = duration_points(hint.get("duration"), candidate.get("duration"))
    total = 55 * title + 30 * (0.5 if artist is None else artist) + 15 * (0.5 if duration is None else duration)
    if artist is None:
        reasons.append("artista desconhecido no arquivo")
    elif artist < 0.6:
        total -= 25
        reasons.append("artista diferente")
    if duration is not None and duration < 0:
        total = min(total, 45)
        reasons.append("duração muito diferente")
    elif duration is not None and duration >= 0.85:
        reasons.append("mesma duração")
    theirs, ours = versions_of(candidate.get("title", "")), set(hint.get("versions") or ())
    for version in theirs - ours:
        total -= 25
        reasons.append(f"é versão {version}")
    for version in ours - theirs:
        total -= 15
        reasons.append(f"falta a versão {version}")
    ours_album, their_album = hint.get("album") or "", candidate.get("album") or ""
    if ours_album and their_album:
        if title_similarity(ours_album, their_album) >= 0.85:
            total += 5
            reasons.append("mesmo álbum")
        elif title_similarity(their_album, candidate.get("title", "")) >= 0.9:
            total -= 8  # a single, while the file says it belongs to another album
            reasons.append("single, não o álbum da tag")
    if duration is None or duration < 0.85:
        # Writing by itself needs the same length (±4 s); otherwise a person takes a look.
        total = min(total, AUTO - 1)
        if duration is not None and duration >= 0:
            reasons.append("duração um pouco diferente")
    if title >= 0.9:
        reasons.append("mesmo título")
    return max(0, min(100, round(total))), reasons


def best(hint, candidates):
    """Candidates sorted best first, each as (score, reasons, candidate).

    When the file name is ambiguous (no artist tag), every reading of it is tried and the best counts.
    """
    variants = [hint] + [{**hint, "title": r["title"], "core": r["core"], "artists": r["artists"]}
                         for r in hint.get("readings") or []]
    ranked = [(*max((score(variant, candidate) for variant in variants), key=lambda item: item[0]), candidate)
              for candidate in candidates]
    ranked.sort(key=lambda item: (-item[0], -(item[2].get("rank") or 0)))
    return ranked
