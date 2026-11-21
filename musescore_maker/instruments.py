"""Instrument knowledge: how to recognise a part name and how to notate/play it."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

GUITAR_TUNING = (64, 59, 55, 50, 45, 40)
BASS_TUNING = (43, 38, 33, 28)
# Wide generic tuning used to put non-string instruments into Guitar Pro.
GENERIC_TUNING = (64, 59, 55, 50, 45, 40, 35)


@dataclass(frozen=True)
class Instrument:
    key: str  # canonical OCR key, e.g. "ALTOSAX"
    name: str  # display name, e.g. "Alto Sax"
    abbr: str
    sound: str  # MusicXML instrument-sound id (MuseScore uses it to pick the instrument)
    program: int  # General MIDI program, 1-based as in MusicXML
    clef: tuple[str, int] | None  # (sign, line) to lock every staff to; None = don't lock
    transpose: tuple[int, int] = (0, 0)  # (diatonic, chromatic) written -> sounding
    staves: int = 1
    percussion: bool = False
    tuning: tuple[int, ...] = GENERIC_TUNING
    aliases: tuple[str, ...] = ()


TREBLE = ("G", 2)
BASS = ("F", 4)
PERC = ("percussion", 2)

INSTRUMENTS: list[Instrument] = [
    Instrument("SOPRANOSAX", "Soprano Sax", "S. Sx.", "wind.reed.saxophone.soprano", 65, TREBLE, (-1, -2)),
    Instrument("ALTOSAX", "Alto Sax", "A. Sx.", "wind.reed.saxophone.alto", 66, TREBLE, (-5, -9)),
    Instrument("TENORSAX", "Tenor Sax", "T. Sx.", "wind.reed.saxophone.tenor", 67, TREBLE, (-8, -14)),
    Instrument("BARITONESAX", "Baritone Sax", "Bar. Sx.", "wind.reed.saxophone.baritone", 68, TREBLE, (-12, -21),
               aliases=("BARISAX",)),
    Instrument("TRUMPET", "Trumpet", "Tpt.", "brass.trumpet", 57, TREBLE, (-1, -2), aliases=("FLUGELHORN",)),
    Instrument("BASSTROMBONE", "Bass Trombone", "B. Tbn.", "brass.trombone.bass", 58, BASS),
    Instrument("TROMBONE", "Trombone", "Tbn.", "brass.trombone", 58, BASS),
    Instrument("FRENCHHORN", "Horn", "Hn.", "brass.french-horn", 61, TREBLE, (-4, -7), aliases=("HORNINF",)),
    Instrument("TUBA", "Tuba", "Tba.", "brass.tuba", 59, BASS),
    Instrument("FLUTE", "Flute", "Fl.", "wind.flutes.flute", 74, TREBLE),
    Instrument("CLARINET", "Clarinet", "Cl.", "wind.reed.clarinet.bflat", 72, TREBLE, (-1, -2)),
    Instrument("VIBRAPHONE", "Vibraphone", "Vib.", "pitched-percussion.vibraphone", 12, TREBLE, aliases=("VIBES",)),
    Instrument("PIANO", "Piano", "Pno.", "keyboard.piano", 1, None, staves=2, aliases=("KEYBOARD",)),
    Instrument("GUITAR", "Guitar", "Gtr.", "pluck.guitar.electric", 27, TREBLE, (-7, -12), tuning=GUITAR_TUNING),
    Instrument("BASS", "Bass", "Bass", "pluck.bass.electric", 34, BASS, (-7, -12), tuning=BASS_TUNING,
               aliases=("STRINGBASS", "ELECTRICBASS", "UPRIGHTBASS")),
    Instrument("DRUMS", "Drums", "Dr.", "drum.group.set", 1, PERC, percussion=True, aliases=("DRUMSET",)),
    Instrument("AUXPERCUSSION", "Aux. Percussion", "Perc.", "drum.group.set", 1, PERC, percussion=True,
               aliases=("AUXILIARYPERCUSSION", "PERCUSSION")),
    Instrument("VOCAL", "Vocal", "Voc.", "voice.vocals", 53, TREBLE, aliases=("VOICE", "VOCALS")),
]

GENERIC = Instrument("PART", "Part", "Pt.", "keyboard.piano", 1, None, staves=1)

BY_KEY = {i.key: i for i in INSTRUMENTS}

# OCR confusions seen in scanned parts (lower-case already folded).
_OCR_FIXES = [("AITO", "ALTO"), ("AFTO", "ALTO"), ("A/TO", "ALTO"), ("PTANO", "PIANO"), ("TRURMPET", "TRUMPET"),
              ("BARTONE", "BARITONE"), ("TROMBONET", "TROMBONE1")]


def normalise(text: str) -> str:
    t = text.upper()
    for a, b in _OCR_FIXES:
        t = t.replace(a, b)
    return re.sub(r"[^A-Z0-9]", "", t)


def match(text: str, cutoff: float = 0.82) -> tuple[Instrument, int | None] | None:
    """Recognise e.g. 'ALTO SAX 1' -> (Alto Sax, 1). Returns None if it isn't an instrument label."""
    t = normalise(text)
    m = re.fullmatch(r"([A-Z.]+?)(\d?)", t)
    if not m or len(m.group(1)) < 4:
        return None
    word, num = m.group(1), m.group(2)
    candidates = {}
    for inst in INSTRUMENTS:
        for k in (inst.key, *inst.aliases):
            candidates[k] = inst
    best = difflib.get_close_matches(word, candidates, n=1, cutoff=cutoff)
    if not best:
        return None
    return candidates[best[0]], (int(num) if num else None)


def display_name(inst: Instrument, number: int | None) -> str:
    return f"{inst.name} {number}" if number else inst.name


def from_display_name(name: str) -> tuple[Instrument, int | None]:
    """Inverse of display_name, used when the user edits parts.json."""
    m = re.fullmatch(r"(.*?)\s*(\d*)\s*(\(\d+\))?", name.strip())
    base = (m.group(1) if m else name).lower()
    for inst in INSTRUMENTS:
        if inst.name.lower() == base:
            return inst, (int(m.group(2)) if m and m.group(2) else None)
    hit = match(name, cutoff=0.7)
    return hit if hit else (GENERIC, None)
