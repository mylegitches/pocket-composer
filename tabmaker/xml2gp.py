"""Convert MusicXML (.musicxml / .xml / compressed .mxl) into a PyGuitarPro song.

Uses <technical><string>/<fret> when the file has tab data; otherwise picks a
string/fret for each note from the instrument's tuning.
"""
import io
import xml.etree.ElementTree as ET
import zipfile
from fractions import Fraction

import guitarpro as gp

from .gpsong import add_track, duration, make_song

TYPES = {"breve": 1, "whole": 1, "half": 2, "quarter": 4, "eighth": 8, "16th": 16, "32nd": 32, "64th": 64,
         "128th": 64, "256th": 64}
STEPS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
BASS4 = [43, 38, 33, 28]
GUITAR6 = [64, 59, 55, 50, 45, 40]


def load_xml(data: bytes) -> ET.Element:
    if data[:2] == b"PK":  # compressed .mxl
        z = zipfile.ZipFile(io.BytesIO(data))
        name = None
        try:
            container = ET.fromstring(z.read("META-INF/container.xml"))
            name = container.find(".//rootfile").get("full-path")
        except (KeyError, AttributeError):
            name = next(n for n in z.namelist() if n.endswith((".xml", ".musicxml")) and not n.startswith("META-INF"))
        data = z.read(name)
    root = ET.fromstring(data)
    if root.tag != "score-partwise":
        raise ValueError("Only partwise MusicXML is supported (that's what MuseScore exports)")
    return root


def _fill_gap(q: Fraction) -> list[tuple[int, int]]:
    """Split a length in quarter notes into plain rest values (value, dots)."""
    out = []
    for v in (1, 2, 4, 8, 16, 32, 64):
        length = Fraction(4, v)
        while q >= length:
            out.append((v, 0))
            q -= length
    return out


class _Fretter:
    """Picks string/fret for notes that have no tab info, keeping the hand near one spot."""

    def __init__(self, tuning, max_fret=24):
        self.tuning, self.max_fret, self.hand = tuning, max_fret, 3

    def place(self, pitches: list[int]) -> list[tuple[int, int]]:
        lowest = min(self.tuning)
        used, out = set(), []
        for p in sorted(pitches, reverse=True):
            while p < lowest:
                p += 12
            opts = [(s, p - open_) for s, open_ in enumerate(self.tuning, 1)
                    if s not in used and 0 <= p - open_ <= self.max_fret]
            if not opts:
                continue
            s, f = min(opts, key=lambda o: (0 if o[1] == 0 else abs(o[1] - self.hand) + 0.3 * o[1], o[1]))
            used.add(s)
            out.append((s, f))
            if f:
                self.hand = f
        return out


def _part_info(root, part_el):
    pid = part_el.get("id")
    sp = root.find(f"part-list/score-part[@id='{pid}']")
    name = (sp.findtext("part-name") or "") if sp is not None else ""
    program = sp.findtext(".//midi-instrument/midi-program") if sp is not None else None
    program = int(program) - 1 if program else None
    # staff to read: a TAB staff if there is one (it carries string/fret), else staff 1
    attrs = part_el.find("measure/attributes")
    tab_staff, clef_f, tuning = None, False, None
    if attrs is not None:
        for c in attrs.findall("clef"):
            if c.findtext("sign") == "TAB":
                tab_staff = c.get("number", "1")
            if c.findtext("sign") == "F":
                clef_f = True
        for sd in attrs.findall("staff-details"):
            tun = sd.findall("staff-tuning")
            if tun:
                pitches = {}
                for t in tun:
                    pitches[int(t.get("line"))] = 12 * (int(t.findtext("tuning-octave")) + 1) + \
                        STEPS[t.findtext("tuning-step")] + int(float(t.findtext("tuning-alter") or 0))
                tuning = [pitches[k] for k in sorted(pitches, reverse=True)]  # line 1 is the lowest string
    lname = name.lower()
    is_bass = ("bass" in lname and "bassoon" not in lname) or (program is not None and 32 <= program <= 39) or \
        (clef_f and tuning is None and "guitar" not in lname)
    tuning = tuning or (BASS4 if is_bass else GUITAR6)
    if program is None:
        program = 33 if is_bass else 25
    return name, program, tab_staff or "1", tuning


def build_song(data: bytes, title: str = "") -> gp.Song:
    root = load_xml(data)
    parts = [p for p in root.findall("part")]
    if not parts:
        raise ValueError("No parts in this MusicXML file")
    title = title or root.findtext("work/work-title") or root.findtext("movement-title") or ""
    artist = next((c.text for c in root.findall("identification/creator") if c.text), "")

    # bar structure + tempo from the first part
    headers, tempo, sig, ending_open = [], None, (4, 4), []
    for m in parts[0].findall("measure"):
        t = m.find("attributes/time")
        if t is not None and t.findtext("beats"):
            try:
                sig = (int(t.findtext("beats").split("+")[0]), int(t.findtext("beat-type")))
            except ValueError:
                pass
        h = {"sig": sig, "alts": None}
        for bl in m.findall("barline"):
            rep = bl.find("repeat")
            if rep is not None and rep.get("direction") == "forward":
                h["repeatOpen"] = True
            if rep is not None and rep.get("direction") == "backward":
                h["repeatClose"] = int(rep.get("times", 2)) - 1  # GP5 stores repeat count minus one
            end = bl.find("ending")
            if end is not None and end.get("type") == "start":
                ending_open = [int(x) for x in end.get("number", "1").replace(" ", "").split(",") if x.isdigit()]
        if ending_open:
            h["alts"] = ending_open
            if any(e.get("type") in ("stop", "discontinue") for e in m.findall("barline/ending")):
                ending_open = []
        words = m.findtext("direction/direction-type/rehearsal")
        if words:
            h["marker"] = words
        snd = m.find(".//sound[@tempo]")
        if tempo is None and snd is not None:
            tempo = round(float(snd.get("tempo")))
        headers.append(h)
    song = make_song(title, artist, tempo or 120, headers)

    for part_el in parts:
        name, program, staff, tuning = _part_info(root, part_el)
        if all(n.find("unpitched") is not None for n in part_el.iter("note") if n.find("rest") is None) and \
                any(True for _ in part_el.iter("unpitched")):
            continue  # drum parts aren't handled
        track = add_track(song, name, tuning, program)
        _fill_track(track, part_el, staff, tuning)
    if not song.tracks:
        raise ValueError("No pitched parts found")
    return song


def _fill_track(track: gp.Track, part_el, staff: str, tuning: list[int]):
    fretter = _Fretter(tuning)
    divisions, transpose = 1, 0
    last_string = {}  # pitch -> string, so tied notes stay on one string
    for mi, m in enumerate(part_el.findall("measure")):
        if mi >= len(track.measures):
            break
        meas = track.measures[mi]
        voice_map, pos = {}, Fraction(0)          # xml voice -> gp voice index; cursor in quarters
        vpos = {0: Fraction(0), 1: Fraction(0)}   # where each gp voice has got to
        last_beat = {}
        for el in m:
            if el.tag == "attributes":
                divisions = int(el.findtext("divisions") or divisions)
                tr = el.find("transpose")
                if tr is not None:
                    transpose = int(tr.findtext("chromatic") or 0) + 12 * int(tr.findtext("octave-change") or 0)
            elif el.tag == "backup":
                pos -= Fraction(int(el.findtext("duration")), divisions)
            elif el.tag == "forward":
                pos += Fraction(int(el.findtext("duration")), divisions)
            elif el.tag == "note":
                if el.find("grace") is not None or el.find("cue") is not None:
                    continue
                if (el.findtext("staff") or "1") != staff:
                    if el.find("chord") is None:
                        pos += Fraction(int(el.findtext("duration") or 0), divisions)
                    continue
                xv = el.findtext("voice") or "1"
                if xv not in voice_map:
                    if len(voice_map) >= 2:
                        if el.find("chord") is None:
                            pos += Fraction(int(el.findtext("duration") or 0), divisions)
                        continue
                    voice_map[xv] = len(voice_map)
                vi = voice_map[xv]
                v = meas.voices[vi]
                dur_q = Fraction(int(el.findtext("duration") or 0), divisions)
                pitch = el.find("pitch")
                tech = el.find("notations/technical")
                tied_stop = any(t.get("type") == "stop" for t in el.findall("tie"))

                if el.find("chord") is not None and vi in last_beat and pitch is not None:
                    beat = last_beat[vi]
                else:
                    if pos > vpos[vi]:  # gap (e.g. <forward>) -> rests
                        for val, dots in _fill_gap(pos - vpos[vi]):
                            v.beats.append(gp.Beat(v, duration=duration(val, dots), status=gp.BeatStatus.rest))
                    typ = TYPES.get(el.findtext("type") or "", None)
                    tm = el.find("time-modification")
                    tup = int(tm.findtext("actual-notes")) if tm is not None else None
                    dots = len(el.findall("dot"))
                    if typ is None:  # whole-measure rest without a type
                        parts = _fill_gap(dur_q) or [(4, 0)]
                        for val, d in parts[:-1]:
                            v.beats.append(gp.Beat(v, duration=duration(val, d), status=gp.BeatStatus.rest))
                        typ, dots = parts[-1]
                    is_rest = el.find("rest") is not None or pitch is None
                    beat = gp.Beat(v, duration=duration(typ, dots, tup),
                                   status=gp.BeatStatus.rest if is_rest else gp.BeatStatus.normal)
                    v.beats.append(beat)
                    last_beat[vi] = beat
                    pos += dur_q
                    vpos[vi] = pos
                    if is_rest:
                        continue

                midi = 12 * (int(pitch.findtext("octave")) + 1) + STEPS[pitch.findtext("step")] + \
                    round(float(pitch.findtext("alter") or 0)) + transpose
                if tech is not None and tech.findtext("fret") is not None and tech.findtext("string") is not None:
                    s, f = int(tech.findtext("string")), int(tech.findtext("fret"))
                elif tied_stop and midi in last_string:
                    s = last_string[midi]
                    f = midi - tuning[s - 1]
                else:
                    placed = fretter.place([midi])
                    if not placed:
                        continue
                    s, f = placed[0]
                if any(n.string == s for n in beat.notes):  # string clash inside a chord
                    alt = [(ss, midi - o) for ss, o in enumerate(tuning, 1)
                           if ss not in {n.string for n in beat.notes} and 0 <= midi - o <= 24]
                    if not alt:
                        continue
                    s, f = alt[0]
                last_string[midi] = s
                note = gp.Note(beat, value=f, string=s, type=gp.NoteType.tie if tied_stop else gp.NoteType.normal)
                arts = el.find("notations/articulations")
                if arts is not None:
                    note.effect.staccato = arts.find("staccato") is not None
                    note.effect.accentuatedNote = arts.find("accent") is not None
                if tech is not None:
                    note.effect.hammer = tech.find("hammer-on") is not None or tech.find("pull-off") is not None
                if el.find("notations/slide") is not None or el.find("notations/glissando") is not None:
                    note.effect.slides = [gp.SlideType.legatoSlideTo]
                beat.notes.append(note)
