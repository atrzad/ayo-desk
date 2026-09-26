"""Bounded background commands; callbacks always return to GTK's main context."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import subprocess
from gi.repository import GLib

POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="ayo")


def background(work, done, executor=POOL):
    future = executor.submit(work)

    def finish(task):
        try:
            value, error = task.result(), None
        except Exception as exc:
            value, error = None, str(exc)

        def deliver():
            done(value, error)
            return GLib.SOURCE_REMOVE
        GLib.idle_add(deliver)
    future.add_done_callback(finish)


def command(argv, timeout=12):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                                env={**os.environ, "LC_ALL": "C"}, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Comando necessário não encontrado: {argv[0]}") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"{argv[0]} terminou com erro {result.returncode}")
    return result.stdout


class Audio:
    @staticmethod
    def snapshot():
        result = {kind: json.loads(command(["pactl", "--format=json", "list", kind]))
                  for kind in ("sinks", "sources", "sink-inputs", "source-outputs", "cards")}
        result["info"] = json.loads(command(["pactl", "--format=json", "info"]))
        return result

    @staticmethod
    def change(action, *args):
        allowed = {"set-sink-volume", "set-source-volume", "set-sink-input-volume", "set-source-output-volume",
                   "set-sink-mute", "set-source-mute", "set-sink-input-mute", "set-source-output-mute",
                   "set-default-sink", "set-default-source", "move-sink-input", "move-source-output", "set-card-profile"}
        if action not in allowed:
            raise ValueError("Ação de áudio inválida")
        return command(["pactl", action, *(str(a) for a in args)])


def volume_percent(device):
    channels = device.get("volume", {}).values()
    values = [float(c.get("value_percent", "0%").strip("%")) for c in channels]
    return round(sum(values) / len(values)) if values else 0
