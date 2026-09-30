"""Hear the words sung in a song with whisper.cpp (runs locally), to sync plain lyrics automatically.

Needs the `whisper-cpp` package (whisper-cli) and a model file, downloaded once on request.
"""
from concurrent.futures import CancelledError
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst

from . import tags
from .net import USER_AGENT

BINARIES = ("whisper-cli", "whisper-cpp", "whisper-main")
MODELS = {"base": ("ggml-base.bin", 147_951_465, "Rápido (148 MB)"),
          "small": ("ggml-small.bin", 487_601_967, "Mais preciso (488 MB)")}
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{}"
PORTUGUESE = {"que", "nao", "eu", "voce", "pra", "meu", "minha", "uma", "com", "sem", "tudo", "mais", "quando",
              "gente", "ta", "tem", "isso", "ela", "ele", "vida", "amor", "mim", "teu", "tua", "nos", "sou"}
ENGLISH = {"the", "and", "you", "my", "to", "it", "is", "in", "me", "your", "that", "we", "be", "on", "love",
           "all", "what", "i'm", "don't", "can't", "just", "like", "know", "with", "for", "this"}
SPANISH = {"que", "el", "la", "yo", "tu", "mi", "con", "por", "para", "pero", "como", "todo", "amor", "quiero"}


def binary():
    return next((shutil.which(name) for name in BINARIES if shutil.which(name)), None)


def model_dir():
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "ayo-desk" / "whisper"


def model_path(name="base"):
    file = model_dir() / MODELS[name][0]
    return file if file.is_file() and file.stat().st_size > MODELS[name][1] * 0.9 else None


def available(name="base"):
    return bool(binary() and model_path(name))


def download(name, progress=None, cancel=None, opener=urllib.request.urlopen):
    """Fetch the model file (resumable download into a .part file); returns its path."""
    filename, size, _label = MODELS[name]
    target = model_dir() / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    request = urllib.request.Request(MODEL_URL.format(filename), headers={"User-Agent": USER_AGENT})
    with opener(request, timeout=30) as response, open(partial, "wb") as output:
        total = int(response.headers.get("Content-Length") or size)
        done = 0
        while True:
            if cancel is not None and cancel.is_set():
                raise CancelledError("Download cancelado")
            chunk = response.read(1 << 18)
            if not chunk:
                break
            output.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if partial.stat().st_size < size * 0.9:
        partial.unlink(missing_ok=True)
        raise ValueError("O download do modelo veio incompleto. Tente de novo.")
    os.replace(partial, target)
    return target


def guess_language(text):
    """"pt", "en", "es" or "auto" from the lyrics' most common short words."""
    words = re.sub(r"[^\w']+", " ", tags.fold(text)).split()
    if len(words) < 8:
        return "auto"
    scores = {"pt": sum(w in PORTUGUESE for w in words), "en": sum(w in ENGLISH for w in words),
              "es": sum(w in SPANISH for w in words)}
    best = max(scores, key=scores.get)
    return best if scores[best] >= max(3, len(words) * 0.04) else "auto"


def to_wav(path, output, cancel=None, timeout=300):
    """Decode any audio file to 16 kHz mono WAV, the format whisper expects."""
    Gst.init(None)
    pipeline = Gst.parse_launch(
        "uridecodebin name=source ! audioconvert ! audioresample ! "
        "audio/x-raw,format=S16LE,rate=16000,channels=1 ! wavenc ! filesink name=sink")
    pipeline.get_by_name("source").set_property("uri", Path(path).resolve().as_uri())
    pipeline.get_by_name("sink").set_property("location", str(output))
    pipeline.set_state(Gst.State.PLAYING)
    bus = pipeline.get_bus()
    started = time.monotonic()
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise CancelledError("Cancelado")
            message = bus.timed_pop_filtered(200 * Gst.MSECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
            if message is None:
                if time.monotonic() - started > timeout:
                    raise TimeoutError("A conversão do áudio demorou demais")
                continue
            if message.type == Gst.MessageType.ERROR:
                raise ValueError(str(message.parse_error()[0]))
            return output
    finally:
        pipeline.set_state(Gst.State.NULL)


def words_from_json(data):
    """whisper-cli -ojf output → [(word, start_ms, end_ms)] (sub-word tokens are joined)."""
    words = []
    for segment in data.get("transcription", []):
        for token in segment.get("tokens", []):
            text = token.get("text", "")
            if not text.strip() or text.strip().startswith("[_"):
                continue
            offsets = token.get("offsets") or {}
            start, end = int(offsets.get("from", 0)), int(offsets.get("to", 0))
            if text.startswith(" ") or not words:
                words.append([text.strip(), start, end])
            else:
                words[-1][0] += text.strip()
                words[-1][2] = end
    return [(word, start, end) for word, start, end in words if re.search(r"\w", word)]


def transcribe(path, model="base", language="auto", cancel=None, threads=None):
    """Words heard in `path`, with times. Takes roughly a fraction of the song length on a laptop CPU."""
    program, weights = binary(), model_path(model)
    if not program or not weights:
        raise RuntimeError("Instale o whisper-cpp e baixe o modelo de voz nas preferências.")
    threads = threads or max(1, min(8, (os.cpu_count() or 2) - 1))
    with tempfile.TemporaryDirectory(prefix="ayo-voz-") as folder:
        wav = Path(folder) / "audio.wav"
        to_wav(path, wav, cancel)
        prefix = Path(folder) / "saida"
        process = subprocess.Popen([program, "-m", str(weights), "-f", str(wav), "-l", language, "-ojf",
                                    "-of", str(prefix), "-np", "-t", str(threads)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        while process.poll() is None:
            if cancel is not None and cancel.is_set():
                process.kill()
                process.wait()
                raise CancelledError("Cancelado")
            time.sleep(0.2)
        result = Path(f"{prefix}.json")
        if process.returncode != 0 or not result.is_file():
            error = (process.stderr.read() if process.stderr else "")[-300:]
            raise RuntimeError(f"O whisper não conseguiu ouvir a música. {error}".strip())
        return words_from_json(json.loads(result.read_text(encoding="utf-8", errors="replace")))
