"""Conversions into an output folder: FL Studio projects, and ready-made MusicXML/MIDI files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import export, flp, score
from .mapping import Mapping

FORMATS = ["mscz", "pdf", "mxl", "mid", "mp3", "gp5", "svg"]


@dataclass
class Result:
    title: str
    out_dir: Path
    parts: list[dict]
    files: list[Path] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def export_file(src: Path, out_dir: Path, stem: str, formats: list[str], previews: bool = True,
                title: str = "", gp5_instruments: list | None = None) -> Result:
    """Run MuseScore on a score file (MusicXML, .mxl, MIDI...) and collect what it wrote.

    With previews, page SVGs are always made, and a note-position file (.spos) goes with .mp3
    so the web UI can follow the playback."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in [*out_dir.glob(f"{stem}.*"), *out_dir.glob(f"{stem}-*.svg")]:
        if old.resolve() != src.resolve():
            old.unlink()
    fmts = [f for f in formats if f in FORMATS]
    if previews and "svg" not in fmts:
        fmts.append("svg")
    if not previews:
        fmts = [f for f in fmts if f != "svg"]
    targets = [out_dir / f"{stem}.{f}" for f in fmts if f != "gp5"]
    if previews and "mp3" in fmts:
        targets.append(out_dir / f"{stem}.spos")
    export.convert(src, targets)
    res = Result(title=title or stem, out_dir=out_dir, parts=[])
    if "gp5" in fmts:
        gp5 = out_dir / f"{stem}.gp5"
        try:
            _write_gp5(src, gp5, title or stem, gp5_instruments)
            res.files.append(gp5)
        except Exception as e:  # keep the other formats
            print(f"gp5 export failed for {stem}: {e}")
            res.failed.append(gp5.name)
    for t in targets:
        if t.suffix == ".svg":  # MuseScore writes one SVG per page: name-1.svg, name-2.svg...
            pages = sorted(out_dir.glob(f"{stem}-*.svg"), key=lambda p: int(p.stem.rsplit("-", 1)[1]))
            res.files += pages
            if not pages:
                res.failed.append(t.name)
        elif t.exists():
            res.files.append(t)
        else:
            res.failed.append(t.name)
    return res


def _write_gp5(src: Path, out: Path, title: str, instruments: list | None) -> None:
    """Guitar Pro 5, the musescore-maker way: from MusicXML cleaned up by MuseScore.

    Scans use musescore-maker v2's writer (it knows each part's instrument and tuning); everything else
    uses v1's MusicXML converter, which picks strings and frets from the tuning."""
    import shutil
    import tempfile

    if src.suffix.lower() == ".gp5":
        shutil.copyfile(src, out)
        return
    with tempfile.TemporaryDirectory() as tmp:
        clean = Path(tmp) / "clean.musicxml"
        export.convert(src, [clean])
        if not clean.exists():
            raise RuntimeError("MuseScore could not re-export the score")
        if instruments:
            from musescore_maker import gp5

            gp5.convert(clean, out, title, instruments)
        else:
            from tabmaker import gpsong, xml2gp

            out.write_bytes(gpsong.to_gp5_bytes(xml2gp.build_song(clean.read_bytes(), title)))


def convert(flp_path: Path, out_dir: Path, formats: list[str], mapping: Mapping | None = None) -> Result:
    proj = flp.read(flp_path)
    s, report = score.build(proj, mapping)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = proj.title
    if not report["parts"]:
        return Result(title=report["title"], out_dir=out_dir, parts=[])
    xml = score.write_musicxml(s, out_dir / f"{stem}.musicxml")
    res = export_file(xml, out_dir, stem, formats, title=report["title"])
    res.parts = report["parts"]
    res.files.insert(0, xml)
    return res
