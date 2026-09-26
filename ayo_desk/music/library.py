"""Discover local music without following directory symlinks or blocking GTK."""
from concurrent.futures import CancelledError
import os
from pathlib import Path
import stat

AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".mp2", ".flac", ".ogg", ".oga", ".opus", ".wav", ".wave",
    ".m4a", ".aac", ".wma", ".aiff", ".aif", ".alac", ".ape", ".mka",
})


def scan_music_folder(folder, cancel=None):
    def check_cancelled():
        if cancel is not None and cancel.is_set():
            raise CancelledError("Busca cancelada")

    def unreadable(error):
        raise error

    try:
        check_cancelled()
        root = Path(folder).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Escolha uma pasta para a biblioteca de músicas.")
        found = set()
        for directory, subdirs, files in os.walk(root, followlinks=False, onerror=unreadable):
            check_cancelled()
            subdirs[:] = sorted(name for name in subdirs
                                if not name.startswith(".") and not Path(directory, name).is_symlink())
            for name in files:
                check_cancelled()
                file = Path(directory, name)
                if name.startswith(".") or file.suffix.casefold() not in AUDIO_EXTENSIONS:
                    continue
                try:
                    target = file.resolve(strict=True)
                    if stat.S_ISREG(target.stat().st_mode):
                        found.add(str(target))
                except FileNotFoundError:
                    # Files may be moved while scanning; broken links are not tracks.
                    continue
        return str(root), sorted(found, key=lambda path: (path.casefold(), path))
    except OSError as exc:
        raise ValueError(f"Não foi possível ler a pasta de músicas. Verifique se ela está acessível.\n{exc}") from exc


def read_changes(paths, known, root=None, cancel=None, progress=None):
    """Read tags only for files that are new or changed since `known` {path: (mtime_ns, size)}.

    Returns a list of metadata dicts (with a `cover` cache key). Runs in a worker thread.
    """
    from . import covers, tags

    changed, folder_covers = [], {}
    total = len(paths)
    for number, path in enumerate(paths, start=1):
        if cancel is not None and cancel.is_set():
            raise CancelledError("Leitura cancelada")
        try:
            info = os.stat(path)
        except OSError:
            continue
        signature = (info.st_mtime_ns, info.st_size)
        if known.get(path) == tuple(signature):
            continue
        meta, cover = tags.read(path, root=root, with_cover=True)
        meta["mtime_ns"], meta["size"] = signature
        meta["search"] = tags.search_text(meta)
        key = ""
        if cover:
            key = covers.store(cover)
        if not key:
            directory = os.path.dirname(path)
            if directory not in folder_covers:
                image = covers.folder_cover(directory)
                try:
                    folder_covers[directory] = covers.store(image.read_bytes()) if image else ""
                except OSError:
                    folder_covers[directory] = ""
            key = folder_covers[directory]
        meta["cover"] = key
        changed.append(meta)
        if progress and (number % 25 == 0 or number == total):
            progress(number, total)
    return changed
