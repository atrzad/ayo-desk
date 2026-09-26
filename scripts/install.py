#!/usr/bin/env python3
"""Install relocatable application files and seven launchers under ~/.local."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BASE = Path.home() / ".local"
DEST = BASE / "share/ayo-desk/app"
APPLICATIONS = Path(os.environ.get("XDG_DATA_HOME", BASE / "share")) / "applications"
NAMES = {"network": ("Rede", "network-wireless", "Settings;"),
         "audio": ("Áudio e microfone", "audio-volume-high", "Settings;"),
         "bluetooth": ("Bluetooth", "bluetooth", "Settings;"),
         "calendar": ("Calendário", "x-office-calendar", "Office;Calendar;"),
         "calculator": ("Calculadora", "accessories-calculator", "Utility;Calculator;"),
         "music": ("Música", "multimedia-audio-player", "AudioVideo;Player;")}
COMMANDS = {"music": "ayo-musica", "calculator": "ayo-calculadora", "calendar": "ayo-calendario"}
MUSIC_TYPES = ("audio/mpeg;audio/mp3;audio/flac;audio/x-flac;audio/ogg;audio/x-vorbis+ogg;audio/opus;"
               "audio/x-opus+ogg;audio/wav;audio/x-wav;audio/mp4;audio/x-m4a;audio/aac;audio/x-aiff;"
               "audio/x-ms-wma;audio/x-ape;audio/x-matroska;audio/x-mpegurl;audio/mpegurl;")


def desktop_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    paths = [APPLICATIONS / "io.github.ayodesk.Desk.desktop"]
    paths += [APPLICATIONS / f"io.github.ayodesk.Desk.{key}.desktop" for key in NAMES]
    if args.uninstall:
        for path in paths:
            path.unlink(missing_ok=True)
        (BASE / "bin/ayo-desk").unlink(missing_ok=True)
        for name in COMMANDS.values():
            (BASE / "bin" / name).unlink(missing_ok=True)
        if DEST.exists():
            shutil.rmtree(DEST)
        print("Aplicativos removidos. Seus eventos, histórico e playlists foram preservados.")
        return
    if not (ROOT / "build/libayo.so").is_file():
        raise SystemExit("Execute make antes de instalar.")
    DEST.mkdir(parents=True, exist_ok=True)
    for folder in ("ayo_desk", "data"):
        shutil.copytree(ROOT / folder, DEST / folder, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (DEST / "build").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "build/libayo.so", DEST / "build/libayo.so")
    shutil.copy2(ROOT / "README.md", DEST / "README.md")
    executable = BASE / "bin/ayo-desk"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("#!/bin/sh\ncd " + shlex.quote(str(DEST)) + ' || exit 1\nexec /usr/bin/python3 -m ayo_desk "$@"\n')
    executable.chmod(0o755)
    for key, command in COMMANDS.items():
        launcher = BASE / "bin" / command
        launcher.write_text("#!/bin/sh\nexec " + shlex.quote(str(executable)) + f' --page {key} "$@"\n')
        launcher.chmod(0o755)
    APPLICATIONS.mkdir(parents=True, exist_ok=True)
    for path, key in zip(paths, [None, *NAMES]):
        name, icon, categories = NAMES[key] if key else ("Desk", "preferences-system", "Settings;")
        args_text = f" --standalone --page {key}" if key else ""
        command = executable
        if key in COMMANDS:
            command = BASE / "bin" / COMMANDS[key]
            args_text = ""
        extra = ""
        if key == "music":
            # Lets "Abrir com Ayo Música" work in file managers; files go to the running player.
            args_text += " %F"
            extra = f"MimeType={MUSIC_TYPES}\n"
        path.write_text(f"[Desktop Entry]\nVersion=1.0\nType=Application\nName=Ayo {name}\n"
                        f"Comment=Ferramentas nativas para Arch Linux\nExec={desktop_quote(command)}{args_text}\n"
                        f"Icon={icon}\nTerminal=false\nCategories={categories}\nStartupNotify=true\n{extra}")
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(APPLICATIONS)], check=True)
    print(f"Instalado em {DEST}\nAbra Ayo Desk ou uma das seis ferramentas pelo menu de aplicativos.")


if __name__ == "__main__":
    main()
