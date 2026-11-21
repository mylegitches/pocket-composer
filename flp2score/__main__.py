"""flp2score: FL Studio project (.flp) -> MuseScore score.

    python -m flp2score data\\rac\\rac.flp            # one project
    python -m flp2score data                          # every .flp under a folder
    python -m flp2score song.flp --formats mscz,pdf,mxl,mid

Uses the project's mapping.json (made in the web UI) when there is one.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import flp, mapping
from .convert import convert

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="flp2score", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=Path, help=".flp files or folders containing them")
    ap.add_argument("-o", "--out", type=Path, default=ROOT / "output")
    ap.add_argument("--formats", default="mscz,pdf", help="comma list of mscz,pdf,mxl,mid,svg (default mscz,pdf)")
    args = ap.parse_args(argv)
    files = []
    for i in args.inputs:
        files += sorted(i.rglob("*.flp")) if i.is_dir() else [i]
    formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
    for f in files:
        try:
            print(flp.describe(flp.read(f)))
            res = convert(f, args.out / f.stem, formats, mapping.load(f.parent))
            if not res.parts:
                print("  no piano-roll notes on the playlist: nothing to notate (audio clips can't be scored)")
            for p in res.parts:
                print(f"  part {p['part']!r}: {p['instrument']} (channels {p['channels']}, {p['notes']} notes)")
            for x in res.files:
                print(f"  wrote {x}")
            for x in res.failed:
                print(f"  FAILED {x}")
            print()
        except Exception as e:  # keep going with the other projects
            print(f"{f.name}: ERROR {type(e).__name__}: {e}\n", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
