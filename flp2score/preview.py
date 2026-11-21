"""Short audio samples of MuseScore instruments, rendered by MuseScore itself (its MS Basic sounds).

A sample is a tiny score for one instrument: an arpeggio in the instrument's comfortable range, or a
short beat for drum kits. It goes through the same pipeline as a real project, so it sounds exactly
like that instrument will in a converted score."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from . import catalogue, export, score
from .flp import Clip, Note, Pattern, Project
from .mapping import ChannelMap, Mapping

PPQ = 96
CHORDAL = {"Keyboards", "Strings", "Plucked strings", "Electronic", "Free reed"}
DEFAULT_CENTRE = {"G": 67, "G8vb": 55, "G8va": 79, "F": 48, "F8vb": 36, "C3": 60, "C4": 55}


def _pitched(ins: catalogue.MsInstrument) -> list[tuple[float, float, int]]:
    """(beat, beats, key) for an up-and-down arpeggio that fits the instrument."""
    if ins.range:
        lo, hi = ins.range
        root = max(lo, min((lo + hi) // 2 - 6, hi - 12))
    else:
        root = DEFAULT_CENTRE.get(ins.clefs[0], 60) - 5
    while root % 12 not in (0, 5, 7):  # start on C, F or G
        root -= 1
    tune = [(0, .5, 0), (.5, .5, 4), (1, .5, 7), (1.5, .5, 12), (2, .5, 7), (2.5, .5, 4), (3, 1, 0)]
    notes = [(b, d, root + k) for b, d, k in tune]
    if ins.staves > 1 or ins.group in CHORDAL:
        notes += [(4, 4, root + k) for k in (0, 4, 7, 12)]
    return notes


def _drums(ins: catalogue.MsInstrument) -> list[tuple[float, float, int]]:
    kit = set(ins.drums)
    if {36, 38, 42} <= kit:  # a drum kit: two bars of rock beat
        notes = []
        for bar in range(2):
            for i in range(8):
                b = bar * 4 + i / 2
                notes.append((b, .5, 46 if (bar, i) == (1, 7) and 46 in kit else 42))
                if i in (0, 4) or (bar, i) == (1, 5):
                    notes.append((b, .5, 36))
                if i in (2, 6):
                    notes.append((b, .5, 38))
        notes.append((8, 1, 49 if 49 in kit else 36))
        return notes
    common = [36, 38, 42, 46, 39, 41, 45, 48, 51, 49, 60, 61, 62, 63, 64, 75, 76, 77]
    pitches = ([p for p in common if p in kit] + sorted(kit - set(common)))[:8]
    return [(i * .5, .5, p) for i, p in enumerate(pitches)] + [(len(pitches) * .5, 1, pitches[0])]


def render(ins_id: str, out: Path) -> Path:
    """Write an .mp3 sample of the instrument to `out`. Raises RuntimeError if MuseScore fails."""
    ins = catalogue.get(ins_id)
    notes = _drums(ins) if ins.is_drums else _pitched(ins)
    end = max(b + d for b, d, _ in notes)
    bars = -(-end // 4)
    pat = Pattern(id=1, name="preview", notes=[
        Note(channel=0, pos=round(b * PPQ), length=round(d * PPQ), key=k, velocity=100) for b, d, k in notes])
    proj = Project(path=Path(f"{ins.id}.flp"), version="preview", ppq=PPQ, tempo=100.0, channels={0: ins.name},
                   patterns={1: pat}, clips=[Clip(pos=0, length=int(bars * 4 * PPQ), track=0, pattern=1)])
    m = Mapping(title=ins.name, channels={0: ChannelMap(channel=0, instrument=ins.id, part=ins.name,
                                                        drum_sound="keys" if ins.is_drums else "auto")})
    s, _ = score.build(proj, m)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        xml = score.write_musicxml(s, Path(tmp) / "preview.musicxml")
        tmp_out = Path(tmp) / "preview.mp3"
        export.convert(xml, [tmp_out])
        if not tmp_out.exists():
            raise RuntimeError(f"MuseScore could not render a preview of {ins.name}")
        shutil.move(tmp_out, out)  # tmp may be on another filesystem (Docker volume)
    return out
