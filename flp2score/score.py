"""Build a notated score (music21) from the flattened FL arrangement and a channel->instrument mapping,
and write MusicXML that MuseScore imports with the chosen instruments."""

from __future__ import annotations

import copy
import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from music21 import (chord, clef, duration, instrument, interval, key, layout, metadata, meter, note,
                     percussion, stream, tempo, tie)

from . import catalogue, instruments
from .arrange import SongNote, flatten, song_length
from .catalogue import MsInstrument
from .flp import Project
from .mapping import Mapping, merged

SPARE_DISPLAY = [("A", 5, "triangle"), ("D", 4, "normal"), ("B", 5, "x"), ("C", 6, "x"), ("G", 4, "x"),
                 ("A", 4, "triangle"), ("B", 3, "x")]
GM_DRUM_NAMES = {35: "Acoustic Bass Drum", 36: "Bass Drum", 37: "Side Stick", 38: "Snare", 39: "Hand Clap",
                 40: "Electric Snare", 41: "Low Floor Tom", 42: "Closed Hi-Hat", 43: "High Floor Tom",
                 44: "Pedal Hi-Hat", 45: "Low Tom", 46: "Open Hi-Hat", 47: "Low-Mid Tom", 48: "Hi-Mid Tom",
                 49: "Crash Cymbal", 50: "High Tom", 51: "Ride Cymbal", 53: "Ride Bell", 54: "Tambourine",
                 55: "Splash Cymbal", 56: "Cowbell", 57: "Crash Cymbal 2", 60: "Hi Bongo", 61: "Low Bongo",
                 62: "Mute Hi Conga", 63: "Open Hi Conga", 64: "Low Conga", 69: "Cabasa", 70: "Maracas",
                 75: "Claves", 76: "Hi Wood Block", 77: "Low Wood Block"}
CLEFS = {"G": clef.TrebleClef, "F": clef.BassClef, "G8vb": clef.Treble8vbClef, "G8va": clef.Treble8vaClef,
         "G15ma": clef.Treble8vaClef, "F8vb": clef.Bass8vbClef, "F8va": clef.Bass8vaClef,
         "F15ma": clef.Bass8vaClef, "C1": clef.SopranoClef, "C2": clef.MezzoSopranoClef, "C3": clef.AltoClef,
         "C4": clef.TenorClef, "C5": clef.CBaritoneClef, "PERC": clef.PercussionClef}
NOTEHEADS = {"normal": "normal", "cross": "x", "xcircle": "circle-x", "diamond": "diamond",
             "triangle-up": "triangle", "triangle-down": "inverted triangle", "slash": "slash",
             "circled": "circle-dot", "plus": "cross", "do": "triangle", "re": "normal", "withx": "x"}


# ---------------------------------------------------------------- timing

class Grid:
    """Snaps tick positions to a grid chosen per beat: 16ths, or 8th-note triplets when the notes in
    that beat clearly sit on triplets. One grid per beat keeps rhythms notatable (no 16th/triplet mix)."""

    def __init__(self, ticks: list[int], ppq: int):
        self.ppq = ppq
        by_beat: dict[int, list[int]] = defaultdict(list)
        for t in ticks:
            by_beat[t // ppq].append(t)
        self.div: dict[int, int] = {}
        for beat, ts in by_beat.items():
            err = {d: sum(abs(t - round(t / (ppq / d)) * (ppq / d)) for t in ts) for d in (4, 3)}
            self.div[beat] = 3 if err[3] < 0.5 * err[4] else 4

    def __call__(self, ticks: int) -> float:
        """-> quarter lengths, as exact twelfths so music21 gets clean fractions."""
        beat = ticks // self.ppq
        step = self.ppq / self.div.get(beat, 4)
        q = round(ticks / step) * step
        return round(q / self.ppq * 12) / 12

    def step(self, ql: float) -> float:
        return 1 / self.div.get(int(ql), 4)


def _quantized(notes: list[SongNote], ppq: int, legato: bool) -> list[tuple[float, float, int, int]]:
    """-> [(offset, quarterLength, key, velocity)], cleaned up for notation."""
    grid = Grid([n.start for n in notes] + [n.start + n.length for n in notes], ppq)
    out = []
    for n in notes:
        on = grid(n.start)
        off = grid(n.start + n.length)
        if off <= on:
            off = on + grid.step(on)
        out.append([on, off, n.key, n.velocity])
    if legato:
        # Single-line parts: cut a note when the next one starts, unless they start together (chord).
        onsets = sorted({o[0] for o in out})
        for o in out:
            later = [t for t in onsets if t > o[0]]
            if later and later[0] < o[1]:
                o[1] = later[0]
    return [(on, off - on, k, v) for on, off, k, v in out]


def _beat_pieces(on: float, ql: float) -> list[tuple[float, float]]:
    """Split a note that starts off the beat and runs past it, so no single note mixes a 16th grid
    with a triplet grid (that would need unreadable durations like 7/12 of a beat)."""
    end = on + ql
    cuts = [on]
    if on != math.floor(on) and end > math.floor(on) + 1:
        cuts.append(math.floor(on) + 1)
    if end != math.floor(end) and math.floor(end) > cuts[-1]:
        cuts.append(math.floor(end))
    cuts.append(end)
    return [(a, b - a) for a, b in zip(cuts, cuts[1:])]


# ---------------------------------------------------------------- pitch spelling

SHARP_NAMES = "C C# D D# E F F# G G# A A# B".split()
FLAT_NAMES = "C D- D E- E F G- G A- A B- B".split()
MAJOR = (0, 2, 4, 5, 7, 9, 11)


def choose_key(weights: dict[int, float]) -> int:
    """Key signature (-5..5 sharps) that needs the fewest accidentals for these pitch classes.
    Modal music (e.g. E phrygian dominant: E F G# A B C D) lands on the signature with one accidental."""
    def cost(sharps: int) -> tuple[float, int]:
        tonic = (7 * sharps) % 12
        scale = {(tonic + i) % 12 for i in MAJOR}
        return sum(w for pc, w in weights.items() if pc not in scale), abs(sharps)
    return min(range(-5, 6), key=cost)


def _spell(p, sharps: int | None, weights: dict[int, float] | None = None) -> None:
    """Spell a pitch for the key signature. Diatonic notes follow the key. A chromatic note is spelled
    against whichever neighbouring letter the music uses less: in E phrygian dominant with A in the
    melody, G#-A reads better than Ab-A (no naturals going back and forth)."""
    if sharps is None:
        return
    tonic = (7 * sharps) % 12
    pc = p.pitchClass
    midi = p.midi
    use_sharp = sharps >= 0
    if (pc - tonic) % 12 not in MAJOR and weights:
        below, above = weights.get((pc - 1) % 12, 0), weights.get((pc + 1) % 12, 0)
        if below != above:
            use_sharp = below < above  # sharp shares a letter with the note below
    p.name = (SHARP_NAMES if use_sharp else FLAT_NAMES)[pc]
    p.octave = midi // 12 - 1  # names never cross a C boundary (no B#/Cb), so this is exact


def _written_sharps(concert_sharps: int, chromatic: int) -> int:
    """Key signature for a transposing instrument (written = concert - chromatic transposition)."""
    s = concert_sharps + (-chromatic * 7) % 12
    s = (s + 6) % 12 - 6  # fold into -6..5
    return max(-6, min(6, s))


# ---------------------------------------------------------------- parts

@dataclass
class PartPlan:
    """One staff group in the score: one or more FL channels on one MuseScore instrument."""
    name: str
    ins: MsInstrument
    channels: list[int] = field(default_factory=list)
    notes: list[SongNote] = field(default_factory=list)
    drum_hits: list[tuple[SongNote, int]] = field(default_factory=list)  # (note, GM drum pitch)
    staves: str = "auto"


def plan_parts(proj: Project, mapping: Mapping | None = None) -> tuple[list[PartPlan], Mapping]:
    channels = flatten(proj)
    m = merged(mapping, proj, channels)
    plans: dict[tuple[str, str], PartPlan] = {}
    for ch, notes in channels.items():
        cm = m.channels[ch]
        if not cm.include:
            continue
        ins = catalogue.get(cm.instrument)
        name = cm.part or proj.channels.get(ch) or ins.name
        plan = plans.setdefault((name, ins.id), PartPlan(name=name, ins=ins, staves=cm.staves))
        plan.channels.append(ch)
        shifted = [SongNote(n.start, n.length, max(0, min(127, n.key + cm.transpose)), n.velocity) for n in notes]
        if ins.is_drums:
            ch_name = proj.channels.get(ch, "")
            dm = instruments.drum_map(ch_name, [n.key for n in shifted], cm.drum_sound)
            plan.drum_hits += [(n, dm[n.key]) for n in shifted]
        else:
            plan.notes += shifted
    return list(plans.values()), m


def build(proj: Project, mapping: Mapping | None = None) -> tuple[stream.Score, dict]:
    ppq = proj.ppq
    num, den = proj.time_sig
    sig = meter.TimeSignature(f"{num}/{den}")
    bar_ql = sig.barDuration.quarterLength
    end_ql = max(bar_ql, -(-song_length(proj) / ppq // bar_ql) * bar_ql)
    plans, m = plan_parts(proj, mapping)

    # One concert key for the whole piece, from every pitched note.
    weights: dict[int, float] = defaultdict(float)
    for plan in plans:
        for n in plan.notes:
            weights[n.key % 12] += max(0.25, n.length / ppq)
    concert = choose_key(weights) if weights else None

    score = stream.Score()
    md = metadata.Metadata(title=m.title or proj.title)
    md.composer = ""
    score.insert(0, md)
    report = {"title": md.title, "parts": []}
    first = True
    for i, plan in enumerate(plans, start=1):
        pid = f"P{i}"
        if plan.drum_hits:
            part = _drum_part(plan, ppq, sig, proj.tempo, end_ql, first)
            part.id = pid
            score.insert(0, part)
            n_notes = len(plan.drum_hits)
        elif plan.notes:
            for st in _pitched_part(plan, ppq, sig, concert, weights, proj.tempo, end_ql, first, pid):
                score.insert(0, st)
            n_notes = len(plan.notes)
        else:
            continue
        report["parts"].append({"part": plan.name, "instrument": plan.ins.name, "channels": plan.channels,
                                "notes": n_notes})
        first = False
    return score, report


def _make_instrument(ins: MsInstrument, name: str, abbr: str | None = None) -> instrument.Instrument:
    i = instrument.Instrument()
    i.partName = name
    i.partAbbreviation = abbr or ins.short
    i.instrumentName = ins.name
    i.instrumentSound = ins.xml
    i.midiProgram = ins.program
    dia, chrom = ins.transpose
    if chrom:
        generic = dia - 1 if dia < 0 else dia + 1
        i.transposition = interval.intervalFromGenericAndChromatic(generic, chrom)
    return i


def _pick_staves(plan: PartPlan, q) -> int:
    n = plan.ins.staves
    if plan.staves in ("1", "2", "3"):
        return min(int(plan.staves), max(n, 1)) if n > 1 else 1
    if n < 2:
        return 1
    keys = [k for _o, _l, k, _v in q]
    # One staff is enough when the part stays on one side of middle C (allowing a few ledger lines).
    if keys and (min(keys) >= 55 or max(keys) <= 64):
        return 1
    return 2


def _fill(part: stream.Stream, notes, sharps, weights) -> None:
    """Insert notes; notes sharing onset and length become one chord."""
    groups: dict[tuple[float, float], list[int]] = defaultdict(list)
    for on, ql, k, _v in notes:
        groups[(on, ql)].append(k)
    for (on, ql), keys in sorted(groups.items()):
        el = note.Note(keys[0]) if len(keys) == 1 else chord.Chord(sorted(set(keys)))
        for p in el.pitches:
            _spell(p, sharps, weights)
        for i, (start, length) in enumerate(pieces := _beat_pieces(on, ql)):
            piece = copy.deepcopy(el) if i else el
            piece.duration = duration.Duration(length)
            if len(pieces) > 1:
                piece.tie = tie.Tie("start" if i == 0 else "stop" if i == len(pieces) - 1 else "continue")
            part.insert(start, piece)


def _pitched_part(plan: PartPlan, ppq, sig, concert, weights, mm, end_ql, first, pid) -> list[stream.Stream]:
    ins = plan.ins
    multi = ins.staves > 1
    q = [(on, min(ql, end_ql - on), k, v) for on, ql, k, v in
         _quantized(plan.notes, ppq, legato=not multi) if on < end_ql]
    # Written pitch: transposing instruments (bass, guitar-family octave, Bb/Eb horns) are notated
    # transposed, with a <transpose> element telling MuseScore how they sound.
    chrom = ins.transpose[1]
    q = [(on, ql, k - chrom, v) for on, ql, k, v in q]
    sharps = None if concert is None else _written_sharps(concert, chrom)
    w = {(pc - chrom) % 12: x for pc, x in weights.items()}
    staves = _pick_staves(plan, q)

    if staves == 1:
        part = stream.Part()
        part.id = pid
        _fill(part, q, sharps, w)
        want = ins.clefs[0] if ins.staves == 1 else None
        if want is None:  # one staff of a two-staff instrument: clef by range
            keys = [k for _o, _l, k, _v in q]
            want = "G" if keys and sum(keys) / len(keys) >= 57 else "F"
        return [_finish(part, _make_instrument(ins, plan.name), CLEFS.get(want, clef.TrebleClef)(), sig, sharps,
                        mm, end_ql, first)]

    staffs = []
    split = [[n for n in q if n[2] >= 60], [n for n in q if n[2] < 60]]
    for s in range(staves):
        st = stream.PartStaff()
        st.id = f"{pid}-S{s + 1}"
        _fill(st, split[s] if s < 2 else [], sharps, w)
        c = CLEFS.get(ins.clefs[s] if s < len(ins.clefs) else "F", clef.BassClef)()
        staffs.append(_finish(st, _make_instrument(ins, plan.name), c, sig, sharps, mm, end_ql, first and s == 0))
    group = layout.StaffGroup(staffs, name=plan.name, symbol="brace")
    return [*staffs, group]


def _finish(part, ins, staff_clef, sig, sharps, mm, end_ql, first):
    notes = list(part.notes)
    if any(a.offset + a.quarterLength > b.offset + 1e-9 for a, b in zip(notes, notes[1:])):
        # Overlapping notes (pads, sustained keys) -> chords with ties.
        pid = part.id
        part = part.chordify()
        part.id = pid
    if part.highestTime < end_ql:
        # Pad to the song's end so every part has the same number of bars.
        part.insert(part.highestTime, note.Rest(quarterLength=end_ql - part.highestTime))
    part.insert(0, ins)
    part.insert(0, staff_clef)
    part.insert(0, meter.TimeSignature(sig.ratioString))
    if sharps is not None:
        part.insert(0, key.KeySignature(sharps))
    if first:
        part.insert(0, tempo.MetronomeMark(number=mm))
    pid = part.id
    out = part.makeNotation()
    out.id = pid
    return out


# ---------------------------------------------------------------- drums

def _line_to_display(line: int) -> tuple[str, int]:
    """MuseScore drum 'line' (0 = top line of the staff, +1 per step down) -> (step, octave)."""
    steps = "C D E F G A B".split()
    top = 5 * 7 + steps.index("F")  # F5
    d = top - line
    return steps[d % 7], d // 7


def _drum_display(gm: int, ins: MsInstrument, used: dict) -> tuple[str, int, str]:
    if gm not in used:
        spot = None
        if gm in ins.drums:
            d = ins.drums[gm]
            step, octave = _line_to_display(d["line"])
            spot = (step, octave, NOTEHEADS.get(d["head"], "normal"))
        if spot is None or spot in used.values():
            spot = next(s for s in SPARE_DISPLAY if s not in used.values())
        used[gm] = spot
    return used[gm]


def _drum_part(plan: PartPlan, ppq: int, sig, mm: float, end_ql: float, first: bool) -> stream.Part:
    part = stream.Part()
    used: dict[int, tuple[str, int, str]] = {}
    groups: dict[float, set[int]] = defaultdict(set)
    grid = Grid([n.start for n, _gm in plan.drum_hits], ppq)
    for n, gm in plan.drum_hits:
        groups[grid(n.start)].add(gm)
    onsets = sorted(groups)
    for i, on in enumerate(onsets):
        if on >= end_ql:
            continue
        # Drum hits are notated short: up to the next hit, at most a quarter, never across a beat.
        nxt = onsets[i + 1] if i + 1 < len(onsets) else on + 1
        ql = min(1.0, nxt - on, end_ql - on, math.floor(on) + 1 - on)
        els = []
        for gm in sorted(groups[on]):
            step, octave, head = _drum_display(gm, plan.ins, used)
            u = note.Unpitched(displayName=f"{step}{octave}")
            if head != "normal":
                u.notehead = head
            els.append(u)
        el = els[0] if len(els) == 1 else percussion.PercussionChord(els)
        el.duration = duration.Duration(ql)
        part.insert(on, el)
    ins = instrument.UnpitchedPercussion()
    ins.partName = plan.name
    ins.partAbbreviation = plan.ins.short
    ins.instrumentSound = plan.ins.xml
    part.insert(0, ins)
    part.insert(0, clef.PercussionClef())
    part.insert(0, meter.TimeSignature(sig.ratioString))
    if first:
        part.insert(0, tempo.MetronomeMark(number=mm))
    if part.highestTime < end_ql:
        part.insert(part.highestTime, note.Rest(quarterLength=end_ql - part.highestTime))
    part = part.makeNotation()
    part.drum_layout = used  # type: ignore[attr-defined]
    return part


# ---------------------------------------------------------------- MusicXML

def write_musicxml(score: stream.Score, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    score.write("musicxml", fp=str(out))
    tree = ET.parse(out)
    root = tree.getroot()
    # music21 numbers parts itself; drum parts are matched by order of appearance.
    xml_parts = [sp.get("id") for sp in root.iter("score-part")]
    music21_parts = _top_level_parts(score)
    for xml_id, part in zip(xml_parts, music21_parts):
        if hasattr(part, "drum_layout"):
            _add_drum_instruments(root, xml_id, part.drum_layout)
    _tidy(root)
    tree.write(out, encoding="UTF-8", xml_declaration=True)
    return out


def _top_level_parts(score: stream.Score) -> list[stream.Part]:
    """Parts as MusicXML sees them: the staves of a grand-staff group count as one part."""
    out, seen = [], set()
    for p in score.parts:
        if isinstance(p, stream.PartStaff):
            group = next((g for g in score.getElementsByClass(layout.StaffGroup) if p in g), None)
            if group is not None:
                if id(group) in seen:
                    continue
                seen.add(id(group))
        out.append(p)
    return out


def _add_drum_instruments(root, pid: str, layout_: dict[int, tuple[str, int, str]]) -> None:
    """music21 writes drum notes without per-sound instruments; add them so MuseScore plays each
    note with the right drum sound (and builds a matching drumset)."""
    sp = root.find(f".//score-part[@id='{pid}']")
    sound = sp.findtext("score-instrument/instrument-sound")
    for tag in ("score-instrument", "midi-instrument"):
        for el in sp.findall(tag):
            sp.remove(el)
    anchor = sp.find("part-abbreviation") if sp.find("part-abbreviation") is not None else sp.find("part-name")
    at = list(sp).index(anchor) + 1
    for i, gm in enumerate(sorted(layout_)):
        si = ET.Element("score-instrument", id=f"{pid}-I{gm}")
        ET.SubElement(si, "instrument-name").text = GM_DRUM_NAMES.get(gm, f"Drum {gm}")
        if sound:
            ET.SubElement(si, "instrument-sound").text = sound
        sp.insert(at + i, si)
    for gm in sorted(layout_):
        mi = ET.SubElement(sp, "midi-instrument", id=f"{pid}-I{gm}")
        ET.SubElement(mi, "midi-channel").text = "10"
        ET.SubElement(mi, "midi-unpitched").text = str(gm + 1)  # 1-based in MusicXML
    by_display = {v: gm for gm, v in layout_.items()}
    for n in root.find(f"part[@id='{pid}']").iter("note"):
        up = n.find("unpitched")
        if up is None:
            continue
        head = n.findtext("notehead") or "normal"
        gm = by_display.get((up.findtext("display-step"), int(up.findtext("display-octave")), head))
        if gm is None:
            continue
        kids = list(n)
        pos = max(i for i, k in enumerate(kids) if k.tag in ("duration", "tie", "unpitched", "chord", "grace",
                                                               "cue")) + 1
        n.insert(pos, ET.Element("instrument", id=f"{pid}-I{gm}"))


def _tidy(root) -> None:
    """Drop music21's 'Music21' credit and its explicit <accidental> marks: MuseScore works out which
    accidentals to show from the pitches and key signature, and does it better."""
    for parent in list(root.iter()):
        for child in list(parent):
            if child.tag == "accidental" and parent.tag == "note":
                parent.remove(child)
            elif child.tag == "creator" and (child.text or "").strip() in ("Music21", ""):
                parent.remove(child)
            elif child.tag == "credit" and "Music21" in "".join(child.itertext()):
                parent.remove(child)
