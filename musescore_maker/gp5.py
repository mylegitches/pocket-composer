"""MusicXML -> Guitar Pro 5 (.gp5) with PyGuitarPro.

Reads the clean MusicXML that MuseScore exports (one voice per staff is used: voice 1 of
staff 1) and assigns each sounding pitch to a string/fret on the instrument's tuning.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import guitarpro as gp

from . import instruments

STEP_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
TYPE_VALUE = {"whole": 1, "half": 2, "quarter": 4, "eighth": 8, "16th": 16, "32nd": 32, "64th": 64}
# Treble-clef display position -> General MIDI drum (drum notation in most big-band charts).
DRUM_MAP = {("F", 4): 36, ("E", 4): 36, ("A", 4): 43, ("C", 5): 38, ("D", 5): 48, ("E", 5): 45,
            ("F", 5): 45, ("G", 5): 42, ("A", 5): 51, ("B", 5): 49, ("D", 4): 44}


def _midi(pitch: ET.Element) -> int:
    return (int(pitch.findtext("octave")) + 1) * 12 + STEP_SEMITONE[pitch.findtext("step")] + int(
        float(pitch.findtext("alter") or 0))


def _place(midi: int, tuning: list[int], used: set[int], last_string: int | None) -> tuple[int, int]:
    """Pick (string number 1-based, fret). Prefers low frets and staying near the last string."""
    while midi < tuning[-1]:
        midi += 12
    while midi > tuning[0] + 24:
        midi -= 12
    options = [(s + 1, midi - t) for s, t in enumerate(tuning) if 0 <= midi - t <= 24 and s + 1 not in used]
    if not options:
        options = [(s + 1, midi - t) for s, t in enumerate(tuning) if 0 <= midi - t <= 24] or [(1, 0)]
    return min(options, key=lambda o: (o[1] > 12, abs(o[0] - last_string) if last_string else 0, o[1]))


def convert(musicxml: Path, out: Path, title: str, part_insts: list[instruments.Instrument]) -> None:
    root = ET.parse(musicxml).getroot()
    song = gp.Song(title=title)
    song.tracks = []
    song.measureHeaders = []

    parts = root.findall("part")
    names = [sp.findtext("part-name") or f"Track {i + 1}" for i, sp in enumerate(root.findall("part-list/score-part"))]

    # Measure headers from the first part.
    start = gp.Duration.quarterTime
    time = (4, 4)
    for i, m in enumerate(parts[0].findall("measure")):
        t = m.find("attributes/time")
        if t is not None:
            time = (int(t.findtext("beats", "4").split("+")[0]), int(t.findtext("beat-type", "4")))
        header = gp.MeasureHeader(number=i + 1, start=start,
                                  timeSignature=gp.TimeSignature(time[0], gp.Duration(time[1])))
        if m.find("barline/repeat[@direction='forward']") is not None:
            header.isRepeatOpen = True
        rep = m.find("barline/repeat[@direction='backward']")
        if rep is not None:
            header.repeatClose = int(rep.get("times", "2")) - 1
        song.addMeasureHeader(header)
        start += header.length

    channel = 0
    for idx, part in enumerate(parts):
        inst = part_insts[idx] if idx < len(part_insts) else instruments.GENERIC
        if inst.percussion:
            ch = 9
        else:
            ch = channel if channel < 9 else channel + 1
            channel += 1
        track = gp.Track(song, number=idx + 1, name=names[idx][:40], isPercussionTrack=inst.percussion,
                         channel=gp.MidiChannel(channel=ch, effectChannel=ch, instrument=max(0, inst.program - 1)))
        tuning = [0] * 6 if inst.percussion else inst.tuning
        track.strings = [gp.GuitarString(n + 1, v) for n, v in enumerate(tuning)]
        song.tracks.append(track)

        transpose = 0
        divisions = 1
        last_string = None
        # gp.Track creates one empty measure per song header.
        for measure, m in zip(track.measures, part.findall("measure")):
            voice = measure.voices[0]
            for a in m.findall("attributes"):
                if a.find("divisions") is not None:
                    divisions = int(a.findtext("divisions"))
                if a.find("transpose/chromatic") is not None:
                    transpose = int(a.findtext("transpose/chromatic"))
            beat = None
            for n in m.findall("note"):
                if n.findtext("staff", "1") != "1" or n.findtext("voice", "1") not in ("1", None):
                    continue
                if n.find("grace") is not None:
                    continue
                if n.find("chord") is None or beat is None:
                    beat = gp.Beat(voice, duration=_duration(n, divisions), status=gp.BeatStatus.rest)
                    voice.beats.append(beat)
                    used: set[int] = set()
                if n.find("rest") is not None:
                    continue
                tied = n.find("tie[@type='stop']") is not None
                if inst.percussion:
                    un = n.find("unpitched")
                    key = (un.findtext("display-step"), int(un.findtext("display-octave"))) if un is not None else None
                    string, fret = 1 + len(beat.notes) % 6, DRUM_MAP.get(key, 38)
                else:
                    p = n.find("pitch")
                    if p is None:
                        continue
                    string, fret = _place(_midi(p) + transpose, inst.tuning, used, last_string)
                    last_string = string
                if any(x.string == string for x in beat.notes):
                    continue
                used.add(string)
                beat.status = gp.BeatStatus.normal
                beat.notes.append(gp.Note(beat, value=fret, string=string,
                                          type=gp.NoteType.tie if tied else gp.NoteType.normal))
            _fit(voice, measure.header.length)

    gp.write(song, str(out))


def _fit(voice: gp.Voice, length: int) -> None:
    """Guitar Pro has one time signature per bar for all tracks, so every track's bar must be
    exactly that long: trim what overflows (OMR rhythm slips) and pad gaps with rests."""
    kept, pos = [], 0
    for beat in voice.beats:
        t = beat.duration.time
        if pos + t > length:
            room = length - pos
            if room <= 0:
                break
            fitted = _largest_fitting(room)
            if fitted is None:
                break
            beat.duration = fitted
            t = fitted.time
        kept.append(beat)
        pos += t
    while pos < length:
        d = _largest_fitting(length - pos)
        if d is None:
            break
        kept.append(gp.Beat(voice, duration=d, status=gp.BeatStatus.rest))
        pos += d.time
    voice.beats = kept


def _largest_fitting(ticks: int) -> gp.Duration | None:
    for value in (1, 2, 4, 8, 16, 32, 64):
        for dotted in (True, False):
            d = gp.Duration(value=value, isDotted=dotted)
            if d.time <= ticks:
                return d
    return None


def _duration(n: ET.Element, divisions: int) -> gp.Duration:
    typ = n.findtext("type")
    dots = len(n.findall("dot"))
    tm = n.find("time-modification")
    tuplet = gp.Tuplet(int(tm.findtext("actual-notes")), int(tm.findtext("normal-notes"))) if tm is not None else gp.Tuplet()
    if typ in TYPE_VALUE:
        return gp.Duration(value=TYPE_VALUE[typ], isDotted=dots > 0, tuplet=tuplet)
    # Whole-measure rests and anything untyped: derive from the duration in quarters.
    quarters = Fraction(int(n.findtext("duration") or divisions), divisions)
    for value in (1, 2, 4, 8, 16, 32):
        base = Fraction(4, value)
        if quarters >= base:
            return gp.Duration(value=value, isDotted=quarters >= base * Fraction(3, 2))
    return gp.Duration(value=64)
