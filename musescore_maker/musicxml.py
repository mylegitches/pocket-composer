"""Clean up per-page OMR MusicXML and stitch it into parts and a full score.

OMR on a single-instrument part makes characteristic mistakes that are easy to repair once
we know which instrument the part is for:

* a staff's clef is misread (e.g. a hand-drawn treble clef read as an alto clef), which shifts
  every pitch on that staff -> we re-map those pitches onto the instrument's real clef;
* the key signature is dropped or misread on some staves -> we lock the part to the key seen
  on most measures (or the one given in parts.json) and re-spell unaltered notes in it;
* multi-measure rests are a single measure in MusicXML -> we expand them so parts line up;
* the part has no instrument/transposition -> we add it so playback and Sibelius/MuseScore
  instrument assignment are right.
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from .instruments import Instrument

STEPS = "CDEFGAB"
MELODIC_CHANNELS = [c for c in range(1, 17) if c != 10]
PLAUSIBLE_TIMES = {(2, 2), (3, 2), (4, 2), (2, 4), (3, 4), (4, 4), (5, 4), (6, 4), (7, 4),
                   (3, 8), (5, 8), (6, 8), (7, 8), (9, 8), (12, 8)}
SHARP_ORDER = "FCGDAEB"
FLAT_ORDER = "BEADGCF"

# clef (sign, line) -> diatonic index of the pitch sitting on that line
CLEF_REF = {
    ("G", 2): 4 + 7 * 4,  # G4
    ("G", 1): 4 + 7 * 4,
    ("F", 4): 3 + 7 * 3,  # F3
    ("F", 3): 3 + 7 * 3,
    ("F", 5): 3 + 7 * 3,
    ("C", 1): 0 + 7 * 4,  # C4
    ("C", 2): 0 + 7 * 4,
    ("C", 3): 0 + 7 * 4,
    ("C", 4): 0 + 7 * 4,
    ("C", 5): 0 + 7 * 4,
    ("percussion", 2): 4 + 7 * 4,  # positions read like a treble clef
}


def parse_clef(text: str | None) -> tuple[str, int] | None:
    if not text:
        return None
    t = text.strip()
    if t.lower().startswith("perc"):
        return ("percussion", 2)
    try:
        return (t[0].upper(), int(t[1:]))
    except (ValueError, IndexError):
        return None


def _clef_of(el: ET.Element) -> tuple[str, int]:
    sign = el.findtext("sign", "G")
    line = int(el.findtext("line") or (2 if sign in ("G", "percussion") else 4 if sign == "F" else 3))
    return (sign, line) if (sign, line) in CLEF_REF else ("G", 2)


def clef_shift(src: tuple[str, int], dst: tuple[str, int]) -> int:
    """Diatonic steps to add to a pitch read in clef src so it sits on the same line in clef dst."""
    return (CLEF_REF[dst] - CLEF_REF[src]) + 2 * (src[1] - dst[1])


def key_alter(fifths: int, step: str) -> int:
    if fifths > 0 and step in SHARP_ORDER[:fifths]:
        return 1
    if fifths < 0 and step in FLAT_ORDER[:-fifths]:
        return -1
    return 0


def _int(el: ET.Element | None, default: int = 0) -> int:
    try:
        return int(float(el.text)) if el is not None and el.text else default
    except ValueError:
        return default


# ---------------------------------------------------------------------------------------------
# Reading pages


def load_measures(xml_path: Path) -> list[ET.Element]:
    root = ET.parse(xml_path).getroot()
    part = root.find("part")
    return list(part.findall("measure")) if part is not None else []


def _vote(pages: list[list[ET.Element]]) -> tuple[Counter, Counter]:
    """Count how many measures were read under each clef and each key."""
    clefs: Counter = Counter()
    keys: Counter = Counter()
    for measures in pages:
        clef, key = None, None
        for m in measures:
            for a in m.findall("attributes"):
                c = a.find("clef")
                if c is not None:
                    clef = _clef_of(c)
                f = a.find("key/fifths")
                if f is not None:
                    key = _int(f)
            if m.find("note/pitch") is not None:
                if clef:
                    clefs[clef] += 1
                if key is not None:
                    keys[key] += 1
    return clefs, keys


# ---------------------------------------------------------------------------------------------
# Building a part


class PartBuilder:
    def __init__(self, inst: Instrument, clef_override: str | None, fifths_override: int | None, log=print):
        self.inst = inst
        self.clef_override = parse_clef(clef_override)
        self.fifths_override = fifths_override
        self.log = log

    def build(self, pages: list[list[ET.Element]]) -> list[ET.Element]:
        """pages: the OMR measures of each page, in order."""
        pages = [p for p in pages if p]
        if not pages:
            return []

        lock = self.inst.staves == 1
        clef_votes, key_votes = _vote(pages)
        target_clef = self.clef_override or self.inst.clef or (clef_votes.most_common(1)[0][0] if clef_votes else ("G", 2))
        if self.inst.percussion:
            target_key = 0
        elif self.fifths_override is not None:
            target_key = self.fifths_override
        else:
            target_key = key_votes.most_common(1)[0][0] if key_votes else 0
        if lock:
            self.log(f"    clef {target_clef[0]}{target_clef[1]}, key {target_key:+d} "
                     f"(read clefs {dict(clef_votes)}, keys {dict(key_votes)})")

        out: list[ET.Element] = []
        time = None  # (beats, beat_type) currently in force
        divisions = 1
        for page_no, measures in enumerate(pages):
            read_clef = target_clef
            read_key = target_key
            for m in measures:
                m = copy.deepcopy(m)
                for p in m.findall("print"):
                    m.remove(p)
                multi = 1
                for a in m.findall("attributes"):
                    d = a.find("divisions")
                    if d is not None:
                        divisions = _int(d, 1)
                    t = a.find("time")
                    if t is not None:
                        new_time = (_int(t.find("beats"), 4), _int(t.find("beat-type"), 4))
                        if new_time == time or new_time not in PLAUSIBLE_TIMES:
                            a.remove(t)  # repeated, or a misread like 1/2 for cut time
                        else:
                            time = new_time
                    if lock:
                        for c in a.findall("clef"):
                            read_clef = _clef_of(c)
                            a.remove(c)
                        for k in a.findall("key"):
                            read_key = _int(k.find("fifths"))
                            a.remove(k)
                    for ms in a.findall("measure-style"):
                        mr = ms.find("multiple-rest")
                        if mr is not None:
                            multi = max(1, _int(mr, 1))
                        a.remove(ms)
                    if len(a) == 0:
                        m.remove(a)
                _sanitise(m, single_voice=lock)
                if lock:
                    self._fix_pitches(m, clef_shift(read_clef, target_clef), read_key, target_key)
                    fit_measure(m, time or (4, 4), divisions, allow_short=not out)  # bar 1 may be a pickup
                out.append(m)
                for _ in range(multi - 1):
                    out.append(_rest_measure(time or (4, 4), divisions))

        if time is None:
            time = (4, 4)
        first = out[0]
        attrs = ET.Element("attributes")
        if not any(a.find("divisions") is not None for a in first.findall("attributes")):
            ET.SubElement(attrs, "divisions").text = str(divisions)
        if lock:
            ET.SubElement(ET.SubElement(attrs, "key"), "fifths").text = str(target_key)
        if not any(a.find("time") is not None for a in first.findall("attributes")):
            t = ET.SubElement(attrs, "time")
            ET.SubElement(t, "beats").text = str(time[0])
            ET.SubElement(t, "beat-type").text = str(time[1])
        if lock:
            c = ET.SubElement(attrs, "clef")
            ET.SubElement(c, "sign").text = target_clef[0]
            ET.SubElement(c, "line").text = str(target_clef[1])
        dia, chrom = self.inst.transpose
        if chrom:
            tr = ET.SubElement(attrs, "transpose")
            ET.SubElement(tr, "diatonic").text = str(dia)
            ET.SubElement(tr, "chromatic").text = str(chrom)
        # Insert after any leading <attributes> (which carry <divisions>), before the first note.
        idx = 0
        for i, child in enumerate(list(first)):
            if child.tag == "attributes":
                idx = i + 1
            elif child.tag in ("note", "backup", "forward"):
                break
        first.insert(idx, attrs)

        for i, m in enumerate(out, 1):
            m.set("number", str(i))
            m.attrib.pop("implicit", None)
        return out

    def _fix_pitches(self, measure: ET.Element, shift: int, read_key: int, target_key: int) -> None:
        orig_state: dict[tuple[str, int], int] = {}
        new_state: dict[tuple[str, int], int] = {}
        for note in measure.findall("note"):
            pitch = note.find("pitch")
            if pitch is None:
                continue
            step = pitch.findtext("step", "C")
            octave = _int(pitch.find("octave"), 4)
            alter_el = pitch.find("alter")
            alter = _int(alter_el) if alter_el is not None else 0
            expected = orig_state.get((step, octave), key_alter(read_key, step))
            d = STEPS.index(step) + 7 * octave + shift
            new_step, new_oct = STEPS[d % 7], d // 7
            if alter == expected:
                new_alter = new_state.get((new_step, new_oct), key_alter(target_key, new_step))
            else:
                new_alter = alter
                orig_state[(step, octave)] = alter
                new_state[(new_step, new_oct)] = alter
            for acc in note.findall("accidental"):
                note.remove(acc)
            if self.inst.percussion:
                _to_unpitched(note, pitch, new_step, new_oct)
                continue
            pitch.find("step").text = new_step
            pitch.find("octave").text = str(new_oct)
            if alter_el is not None:
                pitch.remove(alter_el)
            if new_alter:
                el = ET.Element("alter")
                el.text = str(new_alter)
                pitch.insert(1, el)


def _sanitise(m: ET.Element, single_voice: bool) -> None:
    """Remove OMR output that MuseScore 4's importer crashes on or rejects."""
    for note in m.findall("note"):
        if note.find("grace") is not None:  # articulations/breath marks on grace notes crash it
            for n in note.findall("notations"):
                for a in n.findall("articulations"):
                    n.remove(a)
        # Two-note tremolos are often read across a barline, which corrupts bar lengths.
        for trem in note.findall("notations/ornaments/tremolo"):
            if trem.get("type") in ("start", "stop"):
                trem.set("type", "single")
    # Every note of a chord must have the chord's (first note's) length.
    lead = None
    for note in m.findall("note"):
        if note.find("chord") is None:
            lead = note
        elif lead is not None and lead.find("duration") is not None and note.find("duration") is not None:
            for tag in ("duration", "type", "dot", "time-modification"):
                for el in note.findall(tag):
                    note.remove(el)
            pos = next((i for i, c in enumerate(note) if c.tag in ("voice", "staff", "notations")), len(note))
            for el in reversed([c for c in lead if c.tag in ("duration",)]):
                note.insert(pos, copy.deepcopy(el))
            if note.find("voice") is not None and lead.find("voice") is not None:
                note.find("voice").text = lead.find("voice").text
            tail = next((i for i, c in enumerate(note) if c.tag in ("staff", "notations")), len(note))
            for el in reversed([c for c in lead if c.tag in ("type", "dot", "time-modification")]):
                note.insert(tail, copy.deepcopy(el))
    if single_voice:
        # A single-line part: drop the occasional phantom second voice.
        kids = list(m)
        cut = next((i for i, c in enumerate(kids) if c.tag == "backup"), None)
        if cut is not None:
            for c in kids[cut:]:
                if c.tag in ("backup", "forward", "note"):
                    m.remove(c)


QUARTER_TYPES = [(4, "whole", 0), (3, "half", 1), (2, "half", 0), (1.5, "quarter", 1), (1, "quarter", 0),
                 (0.75, "eighth", 1), (0.5, "eighth", 0), (0.375, "16th", 1), (0.25, "16th", 0),
                 (0.125, "32nd", 0), (0.0625, "64th", 0)]


def _set_length(note: ET.Element, duration: int, divisions: int) -> None:
    """Give a note a new duration and the matching (single, untupleted) note type."""
    quarters = duration / divisions
    typ, dots = next(((t, d) for q, t, d in QUARTER_TYPES if q <= quarters + 1e-9), ("64th", 0))
    note.find("duration").text = str(duration)
    for tag in ("type", "time-modification"):
        for el in note.findall(tag):
            note.remove(el)
    for el in note.findall("dot"):
        note.remove(el)
    for tup in note.findall("notations/tuplet"):
        note.find("notations").remove(tup)
    idx = list(note).index(note.find("duration")) + 1
    for el in note:
        if el.tag in ("voice",):
            idx = list(note).index(el) + 1
    t = ET.Element("type")
    t.text = typ
    note.insert(idx, t)
    for _ in range(dots):
        note.insert(idx + 1, ET.Element("dot"))


def fit_measure(m: ET.Element, time: tuple[int, int], divisions: int, allow_short: bool) -> None:
    """Make a single-voice measure exactly one bar long: OMR rhythm slips produce over- and
    under-full bars, which MuseScore tolerates but Guitar Pro (and its importers) do not."""
    if m.find("backup") is not None or m.find("forward") is not None:
        return
    bar = int(round(divisions * 4 * time[0] / time[1]))
    pos = 0
    for note in list(m.findall("note")):
        if note.find("grace") is not None or note.find("chord") is not None:
            continue
        d_el = note.find("duration")
        if d_el is None:
            continue
        if note.find("rest") is not None and note.find("rest").get("measure") == "yes":
            d_el.text = str(bar)
        d = _int(d_el)
        if pos >= bar:
            _remove_with_chord(m, note)
            continue
        if pos + d > bar:
            _set_length(note, bar - pos, divisions)
            for c in _chord_followers(m, note):
                _set_length(c, bar - pos, divisions)
            d = bar - pos
        pos += d
    if pos < bar and pos > 0 and not allow_short:
        remaining = bar - pos
        while remaining > 0:
            q = next((q for q, _, _ in QUARTER_TYPES if q * divisions <= remaining + 1e-9), None)
            if q is None:
                break
            dur = max(1, int(round(q * divisions)))
            n = ET.SubElement(m, "note")
            ET.SubElement(n, "rest")
            ET.SubElement(n, "duration").text = str(dur)
            ET.SubElement(n, "voice").text = "1"
            _set_length(n, dur, divisions)
            remaining -= dur


def _chord_followers(m: ET.Element, note: ET.Element) -> list[ET.Element]:
    kids = list(m)
    out = []
    for el in kids[kids.index(note) + 1:]:
        if el.tag == "note" and el.find("chord") is not None:
            out.append(el)
        elif el.tag == "note":
            break
    return out


def _remove_with_chord(m: ET.Element, note: ET.Element) -> None:
    for c in _chord_followers(m, note):
        m.remove(c)
    m.remove(note)


def _to_unpitched(note: ET.Element, pitch: ET.Element, step: str, octave: int) -> None:
    idx = list(note).index(pitch)
    note.remove(pitch)
    un = ET.Element("unpitched")
    ET.SubElement(un, "display-step").text = step
    ET.SubElement(un, "display-octave").text = str(octave)
    note.insert(idx, un)


def _rest_measure(time: tuple[int, int], divisions: int) -> ET.Element:
    m = ET.Element("measure")
    n = ET.SubElement(m, "note")
    ET.SubElement(n, "rest", measure="yes")
    ET.SubElement(n, "duration").text = str(int(divisions * 4 * time[0] / time[1]))
    ET.SubElement(n, "voice").text = "1"
    return m


# ---------------------------------------------------------------------------------------------
# Writing


def _last_time_divisions(measures: list[ET.Element]) -> tuple[tuple[int, int], int]:
    time, div = (4, 4), 1
    for m in measures:
        for a in m.findall("attributes"):
            if a.find("divisions") is not None:
                div = _int(a.find("divisions"), 1)
            if a.find("time") is not None:
                time = (_int(a.find("time/beats"), 4), _int(a.find("time/beat-type"), 4))
    return time, div


def write_score(path: Path, title: str, parts: list[tuple[str, Instrument, list[ET.Element]]],
                pad: bool = False) -> None:
    root = ET.Element("score-partwise", version="4.0")
    ET.SubElement(ET.SubElement(root, "work"), "work-title").text = title
    ident = ET.SubElement(root, "identification")
    ET.SubElement(ET.SubElement(ident, "encoding"), "software").text = "musescore-maker (homr OMR)"
    plist = ET.SubElement(root, "part-list")
    longest = max(len(m) for _, _, m in parts)
    for n, (name, inst, measures) in enumerate(parts, 1):
        pid = f"P{n}"
        sp = ET.SubElement(plist, "score-part", id=pid)
        ET.SubElement(sp, "part-name").text = name
        ET.SubElement(sp, "part-abbreviation").text = _abbr(name, inst)
        si = ET.SubElement(sp, "score-instrument", id=f"{pid}-I1")
        ET.SubElement(si, "instrument-name").text = inst.name
        ET.SubElement(si, "instrument-sound").text = inst.sound
        mi = ET.SubElement(sp, "midi-instrument", id=f"{pid}-I1")
        ET.SubElement(mi, "midi-channel").text = "10" if inst.percussion else str(MELODIC_CHANNELS[(n - 1) % 15])
        ET.SubElement(mi, "midi-program").text = str(inst.program)
        part = ET.SubElement(root, "part", id=pid)
        for m in measures:
            part.append(m)
        if pad and len(measures) < longest:
            time, div = _last_time_divisions(measures)
            for i in range(len(measures), longest):
                rm = _rest_measure(time, div)
                rm.set("number", str(i + 1))
                part.append(rm)
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)


def _abbr(name: str, inst: Instrument) -> str:
    num = name.split()[-1] if name.split()[-1].isdigit() else ""
    return f"{inst.abbr} {num}".strip()
