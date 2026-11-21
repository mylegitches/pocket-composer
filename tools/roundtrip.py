"""Check a conversion: compare the notes MuseScore plays back (output/<name>/<name>.mid) with the notes
in the .flp arrangement (after the same quantization). Prints missing/extra notes per project.

    .venv\\Scripts\\python tools\\roundtrip.py data
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from music21 import midi  # noqa: E402

from flp2score import arrange, flp, mapping, score  # noqa: E402


def expected(p: flp.Project, m) -> tuple[set, set]:
    end = arrange.song_length(p) / p.ppq
    pitched, drums = set(), set()
    plans, _ = score.plan_parts(p, m)
    for plan in plans:
        if plan.drum_hits:
            grid = score.Grid([n.start for n, _ in plan.drum_hits], p.ppq)
            drums |= {(round(grid(n.start), 2), gm) for n, gm in plan.drum_hits if grid(n.start) < end}
        else:
            ns = plan.notes
            grid = score.Grid([n.start for n in ns] + [n.start + n.length for n in ns], p.ppq)
            pitched |= {(round(grid(n.start), 2), n.key) for n in ns if grid(n.start) < end}
    return pitched, drums


def played(path: Path) -> tuple[set, set]:
    mf = midi.MidiFile()
    mf.open(str(path))
    mf.read()
    mf.close()
    tpq = mf.ticksPerQuarterNote
    pitched, drums = set(), set()
    for tr in mf.tracks:
        t = 0
        for e in tr.events:
            if e.isDeltaTime():
                t += e.time
            elif e.type == midi.ChannelVoiceMessages.NOTE_ON and e.velocity > 0:
                (drums if e.channel == 10 else pitched).add((round(t / tpq, 2), e.pitch))
    return pitched, drums


def main(args: list[str]) -> int:
    files = []
    for a in map(Path, args or ["data"]):
        files += sorted(a.rglob("*.flp")) if a.is_dir() else [a]
    bad = 0
    for f in files:
        p = flp.read(f)
        mid = ROOT / "output" / p.title / f"{p.title}.mid"
        if not mid.exists():
            print(f"{p.title}: no {mid.name} (run with --formats including mid)")
            continue
        (ep, ed), (gp, gd) = expected(p, mapping.load(f.parent)), played(mid)
        ok = ep == gp and ed == gd
        bad += not ok
        print(f"{p.title}: {'OK ' if ok else 'DIFF'} pitched {len(gp)}/{len(ep)}  drums {len(gd)}/{len(ed)}")
        for label, s in (("missing", ep - gp), ("extra", gp - ep), ("drum missing", ed - gd),
                         ("drum extra", gd - ed)):
            if s:
                print(f"   {label}: {sorted(s)[:8]}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
