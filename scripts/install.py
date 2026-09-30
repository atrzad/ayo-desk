#!/usr/bin/env python3
"""Install the apps: for this user under ~/.local (make install), or into a prefix for packages.

    python3 scripts/install.py                       # ~/.local, like before
    python3 scripts/install.py --uninstall
    python3 scripts/install.py --prefix /usr --destdir "$pkgdir"          # Arch package
    python3 scripts/install.py --prefix /app --apps music,calculator,calendar   # Flatpak
"""
import argparse
import compileall
import os
from pathlib import Path
import shlex
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
APP_ID = "io.github.ayodesk.Desk"
HOME = Path.home() / ".local"
# key: (name, icon, categories)
NAMES = {"network": ("Rede", "network-wireless", "Settings;"),
         "audio": ("Áudio e microfone", "audio-volume-high", "Settings;"),
         "bluetooth": ("Bluetooth", "bluetooth", "Settings;"),
         "calendar": ("Calendário", f"{APP_ID}.calendar", "Office;Calendar;"),
         "calculator": ("Calculadora", f"{APP_ID}.calculator", "Utility;Calculator;"),
         "music": ("Música", f"{APP_ID}.music", "AudioVideo;Audio;Player;")}
COMMENTS = {"calendar": "Agenda e eventos", "calculator": "Calculadora com histórico",
            "music": "Player de música com letras sincronizadas"}
COMMANDS = {"music": "ayo-musica", "calculator": "ayo-calculadora", "calendar": "ayo-calendario"}
MUSIC_TYPES = ("audio/mpeg;audio/mp3;audio/flac;audio/x-flac;audio/ogg;audio/x-vorbis+ogg;audio/opus;"
               "audio/x-opus+ogg;audio/wav;audio/x-wav;audio/mp4;audio/x-m4a;audio/aac;audio/x-aiff;"
               "audio/x-ms-wma;audio/x-ape;audio/x-matroska;audio/x-mpegurl;audio/mpegurl;")


def desktop_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'


class Layout:
    """Where each part goes. `real` is the path at run time; `staged` adds DESTDIR for packaging."""

    def __init__(self, prefix=None, destdir=""):
        self.user = prefix is None
        self.prefix = HOME if self.user else Path(prefix)
        self.destdir = destdir
        self.app = HOME / "share/ayo-desk/app" if self.user else self.prefix / "lib/ayo-desk"
        self.bin = self.prefix / "bin"
        share = Path(os.environ.get("XDG_DATA_HOME", HOME / "share")) if self.user else self.prefix / "share"
        self.applications = share / "applications"
        self.icons = share / "icons/hicolor/scalable/apps"
        self.metainfo = share / "metainfo"

    def staged(self, path):
        return Path(self.destdir + str(path)) if self.destdir else path


def desktop_entry(layout, key):
    if key is None:
        name, icon, categories, comment = "Desk", APP_ID, "Settings;", "Rede, áudio e Bluetooth"
    else:
        name, icon, categories = NAMES[key]
        comment = COMMENTS.get(key, "Ferramenta do Ayo Desk")
    command = COMMANDS.get(key, "ayo-desk")
    # Menus of a user install may not have ~/.local/bin in PATH; packages always do.
    command = desktop_quote(layout.bin / command) if layout.user else command
    args = "" if key in COMMANDS else f" --standalone --page {key}" if key else ""
    extra = ""
    if key == "music":
        # "Abrir com Ayo Música" in file managers; files go to the running player.
        args += " %F"
        extra = f"MimeType={MUSIC_TYPES}\nKeywords=music;player;lyrics;letra;música;\n"
    return (f"[Desktop Entry]\nVersion=1.0\nType=Application\nName=Ayo {name}\nComment={comment}\n"
            f"Exec={command}{args}\nIcon={icon}\nTerminal=false\nCategories={categories}\n"
            f"StartupNotify=true\n{extra}")


def launcher(layout, arguments=""):
    python = "/usr/bin/python3" if layout.user else "python3"
    return (f"#!/bin/sh\nPYTHONPATH={shlex.quote(str(layout.app))}${{PYTHONPATH:+:$PYTHONPATH}} "
            f'exec {python} -m ayo_desk{arguments} "$@"\n')


def write(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def install(layout, apps):
    library = ROOT / "build" / "libayo.so"
    if not library.is_file():
        raise SystemExit("Execute make antes de instalar.")
    app = layout.staged(layout.app)
    if app.exists():
        shutil.rmtree(app)
    for folder in ("ayo_desk", "data"):
        shutil.copytree(ROOT / folder, app / folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (app / "build").mkdir(parents=True, exist_ok=True)
    shutil.copy2(library, app / "build" / library.name)
    shutil.copy2(ROOT / "README.md", app / "README.md")
    if not layout.user:
        compileall.compile_dir(app / "ayo_desk", quiet=1, ddir=str(layout.app / "ayo_desk"))
    main = apps is None
    if main:
        write(layout.staged(layout.bin / "ayo-desk"), launcher(layout), 0o755)
    for key, command in COMMANDS.items():
        if main or key in apps:
            write(layout.staged(layout.bin / command), launcher(layout, f" --page {key}"), 0o755)
    for key in [None, *NAMES] if main else apps:
        name = f"{APP_ID}.{key}.desktop" if key else f"{APP_ID}.desktop"
        write(layout.staged(layout.applications / name), desktop_entry(layout, key))
    for icon in (ROOT / "data/icons/hicolor/scalable/apps").glob("*.svg"):
        target = layout.staged(layout.icons / icon.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(icon, target)
    metainfo = layout.staged(layout.metainfo / f"{APP_ID}.metainfo.xml")
    metainfo.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / f"data/{APP_ID}.metainfo.xml", metainfo)
    if layout.user:
        for tool in (["update-desktop-database", str(layout.applications)],
                     ["gtk-update-icon-cache", "-qtf", str(layout.icons.parent.parent)]):
            if shutil.which(tool[0]):
                subprocess.run(tool, check=False)
        print(f"Instalado em {layout.app}\nAbra Ayo Desk ou uma das seis ferramentas pelo menu de aplicativos.")


def uninstall(layout):
    for path in [layout.applications / f"{APP_ID}.desktop",
                 *(layout.applications / f"{APP_ID}.{key}.desktop" for key in NAMES),
                 layout.bin / "ayo-desk", *(layout.bin / name for name in COMMANDS.values()),
                 layout.metainfo / f"{APP_ID}.metainfo.xml",
                 *(layout.icons / icon.name for icon in (ROOT / "data/icons/hicolor/scalable/apps").glob("*.svg"))]:
        path.unlink(missing_ok=True)
    if layout.app.exists():
        shutil.rmtree(layout.app)
    print("Aplicativos removidos. Seus eventos, histórico e playlists foram preservados.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--prefix", help="instala para um pacote (ex.: /usr ou /app) em vez de ~/.local")
    parser.add_argument("--destdir", default="", help="raiz temporária do pacote ($pkgdir)")
    parser.add_argument("--apps", help="só estes apps, separados por vírgula (ex.: music,calculator,calendar)")
    args = parser.parse_args()
    layout = Layout(args.prefix, args.destdir)
    apps = [key.strip() for key in args.apps.split(",")] if args.apps else None
    if apps and any(key not in COMMANDS for key in apps):
        parser.error("--apps aceita: " + ", ".join(COMMANDS))
    if args.uninstall:
        uninstall(layout)
    else:
        install(layout, apps)


if __name__ == "__main__":
    main()
