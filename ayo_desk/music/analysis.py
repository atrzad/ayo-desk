"""Loudness (ReplayGain 2.0 style) and waveform in a single decode, for leveling and the seek bar.

Runs in a worker thread: its own pipeline, polled with a timeout, cancellable.
"""
from concurrent.futures import CancelledError
from pathlib import Path
import re

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst

BARS = 400
PEAKS = re.compile(r"peak=\(GValueArray\)<\s*([^>]*)>")


def resample(values, count):
    """Shrink or stretch a list to `count` items, keeping the maximum of each slice."""
    if not values:
        return [0.0] * count
    step = len(values) / count
    return [max(values[int(n * step):max(int(n * step) + 1, int((n + 1) * step))]) for n in range(count)]


def to_bytes(levels):
    top = max(levels) if levels and max(levels) > 0 else 1.0
    return bytes(min(255, round(value / top * 255)) for value in levels)


def from_bytes(data):
    return [value / 255 for value in data] if data else []


def analyze(path, duration=0.0, bars=BARS, cancel=None, timeout=120):
    """Return {"gain": dB or None, "peak": linear or None, "waveform": bytes(bars)}."""
    Gst.init(None)
    interval = int(max(0.02, (duration or 0) / bars) * Gst.SECOND) if duration else 100 * Gst.MSECOND
    uri = Path(path).resolve().as_uri()
    pipeline = Gst.parse_launch(
        "uridecodebin name=source ! audioconvert ! audioresample ! rganalysis forced=true ! audioconvert ! "
        f"audio/x-raw,channels=1 ! level interval={interval} post-messages=true ! fakesink sync=false")
    pipeline.get_by_name("source").set_property("uri", uri)
    bus = pipeline.get_bus()
    wanted = Gst.MessageType.ELEMENT | Gst.MessageType.TAG | Gst.MessageType.EOS | Gst.MessageType.ERROR
    gain = peak = None
    levels, waited = [], 0.0
    pipeline.set_state(Gst.State.PLAYING)
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise CancelledError("Análise cancelada")
            message = bus.timed_pop_filtered(200 * Gst.MSECOND, wanted)
            if message is None:
                waited += 0.2
                if waited > timeout:
                    raise TimeoutError("A análise demorou demais")
                continue
            waited = 0.0
            if message.type == Gst.MessageType.ERROR:
                error, _debug = message.parse_error()
                raise ValueError(str(error))
            if message.type == Gst.MessageType.EOS:
                break
            if message.type == Gst.MessageType.TAG:
                tags = message.parse_tag()
                ok, value = tags.get_double(Gst.TAG_TRACK_GAIN)
                if ok:
                    gain = value
                ok, value = tags.get_double(Gst.TAG_TRACK_PEAK)
                if ok:
                    peak = value
            elif message.type == Gst.MessageType.ELEMENT:
                found = PEAKS.search(message.get_structure().to_string())
                if found:
                    channels = [float(v) for v in found[1].split(",") if v.strip()]
                    db = max(channels) if channels else -90.0
                    levels.append(10 ** (max(-60.0, db) / 20))
    finally:
        pipeline.set_state(Gst.State.NULL)
    return {"gain": gain, "peak": peak, "waveform": to_bytes(resample(levels, bars))}


def leveling_factor(gain_db, peak=None, preamp=0.0, allow_boost=True):
    """Linear factor for the loudness gain; boosts never push the known peak above full scale."""
    if gain_db is None:
        return 1.0
    total = gain_db + preamp
    if not allow_boost:
        total = min(0.0, total)
    factor = 10 ** (total / 20)
    if peak and factor * peak > 1.0 and total > 0:
        factor = max(1.0, 1.0 / peak)
    return factor
