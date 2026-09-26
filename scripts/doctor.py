#!/usr/bin/env python3
"""Verifica dependências e serviços sem alterar o sistema."""
import importlib.util
import shutil
import subprocess
import sys

REQUIRED_COMMANDS = {
    "nmcli": "NetworkManager / rede",
    "pactl": "PulseAudio ou PipeWire / áudio",
    "bluetoothctl": "BlueZ / Bluetooth",
    "gst-launch-1.0": "GStreamer / música",
    "dnsmasq": "NetworkManager / compartilhamento RJ45",
}
REQUIRED_MODULES = {"gi": "PyGObject"}
OPTIONAL_MODULES = {"mutagen": "python-mutagen / tags, capas e letras das músicas"}
# Elementos GStreamer usados pelo Ayo Música (gst-plugins-base e gst-plugins-good).
GST_ELEMENTS = ("playbin", "equalizer-10bands", "rgvolume", "rglimiter", "scaletempo", "level", "spectrum")


def service_active(service):
    result = subprocess.run(["systemctl", "is-active", "--quiet", service],
                            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0


def main():
    failed = False
    print("Ayo Desk doctor")
    for command, purpose in REQUIRED_COMMANDS.items():
        found = shutil.which(command)
        print(f"{'OK' if found else 'FALTA':5} {command:18} {purpose}")
        failed |= found is None
    for module, purpose in REQUIRED_MODULES.items():
        found = importlib.util.find_spec(module) is not None
        print(f"{'OK' if found else 'FALTA':5} Python {module:12} {purpose}")
        failed |= not found
    for module, purpose in OPTIONAL_MODULES.items():
        found = importlib.util.find_spec(module) is not None
        print(f"{'OK' if found else 'AVISO':5} Python {module:12} {purpose}")
    missing = [name for name in GST_ELEMENTS if shutil.which("gst-inspect-1.0") and subprocess.run(
        ["gst-inspect-1.0", "--exists", name], check=False).returncode != 0]
    print(f"{'OK' if not missing else 'AVISO':5} {'GStreamer':18} "
          + ("efeitos de áudio disponíveis" if not missing else "faltam: " + ", ".join(missing)))
    for service in ("NetworkManager", "bluetooth"):
        active = service_active(service)
        print(f"{'OK' if active else 'AVISO':5} {service:18} {'ativo' if active else 'não está ativo'}")
    print("Dependências essenciais encontradas." if not failed else "Instale os itens marcados como FALTA.")
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
