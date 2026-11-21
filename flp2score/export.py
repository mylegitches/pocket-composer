"""MusicXML -> .mscz / .pdf / .mid via the MuseScore command line (same approach as musescore-maker v2)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

MUSESCORE_CANDIDATES = [
    os.environ.get("MUSESCORE", ""),
    r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe",
    r"C:\Program Files\MuseScore 3\bin\MuseScore3.exe",
    "mscore",
    "musescore",
]


def find_musescore() -> str | None:
    for c in MUSESCORE_CANDIDATES:
        if c and (Path(c).exists() or shutil.which(c)):
            return c
    return None


def convert(src: Path, outs: list[Path]) -> list[Path]:
    exe = find_musescore()
    if not exe:
        raise SystemExit("MuseScore not found. Install MuseScore 4 or set the MUSESCORE env var.")
    made = []
    for out in outs:
        out.unlink(missing_ok=True)
        subprocess.run([exe, "-o", str(out), str(src)], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
        if out.exists():
            made.append(out)
    return made
