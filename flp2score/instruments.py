"""First guess at what each FL channel is, from its name (channels are usually named after the
plugin or preset). The guess is only a starting point: the web UI lets you pick any MuseScore
instrument per channel, and that choice is saved in the project's mapping.json."""

from __future__ import annotations

import re

# (pattern on the lower-cased channel name, MuseScore instrument id). First match wins.
PITCHED: list[tuple[str, str]] = [
    (r"piano|grand|keys|rhodes|wurli|\bep\b|electric piano", "piano"),
    (r"cello", "violoncello"),
    (r"viol(in|a)s?|strings?|orchestr|staccato", "strings"),
    (r"upright|contrabass|double bass", "contrabass"),
    (r"slap", "electric-bass"),
    (r"reese|808|sub|bass", "bass-synthesizer"),
    (r"koto", "koto"),
    (r"sitar|sytar", "sitar"),
    (r"cumbus|oud|saz|bouzouki|baglama", "oud"),
    (r"guitar|gtr", "electric-guitar"),
    (r"pluck", "poly-synth"),
    (r"bell|chime", "tubular-bells"),
    (r"celest|glock", "glockenspiel"),
    (r"pad", "pad-synth"),
    (r"organ", "organ"),
    (r"flute", "flute"),
    (r"vox|vocal|choir|monk", "voice"),
]
SYNTH = "saw-synth"

# Single-sound drum channels -> General MIDI drum note.
DRUM_SOUNDS: list[tuple[str, int]] = [
    (r"kick|\bbd\b|bass ?drum", 36),
    (r"snare|\bsd\b", 38),
    (r"clap", 39),
    (r"rim", 37),
    (r"open ?hat|\boh\b", 46),
    (r"hi-?hat|\bhat\b|\bch\b|\bhh\b", 42),
    (r"crash", 49),
    (r"ride", 51),
    (r"tom", 45),
    (r"shaker", 70),
    (r"tamb", 54),
    (r"cowbell", 56),
]
# Multi-sound drum channels: each distinct key is its own sound.
DRUM_KITS = r"fpc|drum ?kit|drumset|drums|hand ?drum|bongo|conga|perc|battery"
HAND_DRUMS = r"hand ?drum|bongo|conga|darbuka|tabla"
HAND_DRUM_SOUNDS = [64, 63, 61, 60, 62]  # low conga, open hi conga, low bongo, hi bongo, mute hi conga


def is_drum_channel(name: str) -> bool:
    n = name.lower()
    return drum_sound(n) is not None or bool(re.search(DRUM_KITS, n))


def guess_instrument(name: str) -> str:
    """MuseScore instrument id for a channel name."""
    n = name.lower()
    if is_drum_channel(n):
        return "percussion" if re.search(HAND_DRUMS, n) else "drumset"
    for pat, ins in PITCHED:
        if re.search(pat, n):
            return ins
    return SYNTH


def drum_sound(name: str) -> int | None:
    n = name.lower()
    for pat, note in DRUM_SOUNDS:
        if re.search(pat, n):
            return note
    return None


def drum_map(name: str, keys: list[int], how: str | int = "auto") -> dict[int, int]:
    """FL key -> General MIDI drum note for one drum channel.

    how: "auto" (guess from the name), "keys" (keys already are GM drum notes, as in FPC),
         or a GM drum note number (every hit plays that sound)."""
    distinct = sorted(set(keys))
    if isinstance(how, int) or (isinstance(how, str) and how.isdigit()):
        return {k: int(how) for k in distinct}
    if how == "keys":
        return {k: k for k in distinct}
    single = drum_sound(name)
    if single is not None:
        return {k: single for k in distinct}
    if re.search(HAND_DRUMS, name.lower()):
        return {k: HAND_DRUM_SOUNDS[i % len(HAND_DRUM_SOUNDS)] for i, k in enumerate(distinct)}
    return {k: k for k in distinct}
