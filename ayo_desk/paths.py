"""Where the apps keep their data and cache on each system (Linux/XDG, Flatpak, Windows)."""
import os
from pathlib import Path
import sys

APP = "ayo-desk"
WINDOWS = sys.platform == "win32"
LINUX = sys.platform.startswith("linux")
NATIVE_LIBRARY = "ayo.dll" if WINDOWS else "libayo.dylib" if sys.platform == "darwin" else "libayo.so"


def _local_appdata():
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def data_dir():
    """Library database, settings, voice models: ~/.local/share/ayo-desk or %LOCALAPPDATA%\\ayo-desk."""
    if os.environ.get("XDG_DATA_HOME"):
        return Path(os.environ["XDG_DATA_HOME"]) / APP
    return _local_appdata() / APP if WINDOWS else Path.home() / ".local" / "share" / APP


def cache_dir():
    """Covers and web answers, safe to delete: ~/.cache/ayo-desk or %LOCALAPPDATA%\\ayo-desk\\cache."""
    if os.environ.get("XDG_CACHE_HOME"):
        return Path(os.environ["XDG_CACHE_HOME"]) / APP
    return _local_appdata() / APP / "cache" if WINDOWS else Path.home() / ".cache" / APP
