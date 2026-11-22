"""Printable parts: one PDF per instrument, with multi-bar rests and page turns placed where the player rests.

Each part is cut out of the score's MusicXML (the full score's own line and page breaks removed), laid out by
MuseScore, and then checked at every page turn of an open folder (pages 1-2 side by side, turn, 3-4...).
When the music runs straight through a turn, a page break is put right after the last rest on the page before
it, so the turn falls in a rest instead. Only turns are touched; everything else is MuseScore's own layout.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from . import export
from .playback import _split_parts

PRINT_DIR = "print"
MAX_FIXES = 6  # re-layouts per part; each fixes one page turn

STYLE = '''<?xml version="1.0" encoding="UTF-8"?>
<museScore version="4.60">
  <Style>
    <createMultiMeasureRests>1</createMultiMeasureRests>
    <minEmptyMeasures>2</minEmptyMeasures>
    <enableVerticalSpread>0</enableVerticalSpread>
  </Style>
</museScore>
'''


def print_dir(out_dir: Path) -> Path:
    return out_dir / PRINT_DIR


def _safe(name: str) -> str:
    return re.sub(r"[^\w\- .()]+", "_", name).strip(" .") or "part"


def _measures(root: ET.Element) -> list[ET.Element]:
    part = root.find("part")
    return list(part.findall("measure")) if part is not None else []


def _clear_breaks(root: ET.Element) -> None:
    """Drop the full score's system and page breaks: a part makes its own lines."""
    for m in _measures(root):
        for pr in m.findall("print"):
            for attr in ("new-system", "new-page"):
                pr.attrib.pop(attr, None)
            if not pr.attrib and not len(pr):
                m.remove(pr)


def _set_page_break(m: ET.Element) -> None:
    pr = m.find("print")
    if pr is None:
        pr = ET.Element("print")
        m.insert(0, pr)
    pr.set("new-page", "yes")


def _silent(m: ET.Element) -> bool:
    return all(n.find("rest") is not None for n in m.iter("note"))


def _starts_new_block(m: ET.Element) -> bool:
    """Things MuseScore won't fold into a multi-bar rest: key/time changes, rehearsal marks, text, page breaks."""
    if m.find("print[@new-page='yes']") is not None:
        return True
    for a in m.findall("attributes"):
        if a.find("key") is not None or a.find("time") is not None:
            return True
    for d in m.iter("direction-type"):
        if d.find("rehearsal") is not None or d.find("words") is not None or d.find("metronome") is not None:
            return True
    return False


def _units(ms: list[ET.Element]) -> list[tuple[int, int, bool]]:
    """What MuseScore draws as one bar: (first measure, last measure, is a rest); runs of 2+ silent bars fold."""
    units, i = [], 0
    while i < len(ms):
        if _silent(ms[i]):
            j = i
            while j + 1 < len(ms) and _silent(ms[j + 1]) and not _starts_new_block(ms[j + 1]):
                j += 1
            if j > i:
                units.append((i, j, True))
                i = j + 1
                continue
        units.append((i, i, _silent(ms[i])))
        i += 1
    return units


def _bar_pages(mpos: Path) -> list[int]:
    els = sorted(ET.parse(mpos).getroot().iter("element"), key=lambda e: int(e.get("id")))
    return [int(e.get("page")) for e in els]


def _layout(xml: Path, root: ET.Element, mpos: Path, style: Path) -> list[int] | None:
    ET.ElementTree(root).write(xml, encoding="utf-8", xml_declaration=True)
    export.convert(xml, [mpos], style=style)
    return _bar_pages(mpos) if mpos.is_file() else None


def _tidy_turns(root: ET.Element, xml: Path, mpos: Path, style: Path) -> int:
    """Move page turns into rests. Returns how many turns were moved."""
    ms = _measures(root)
    done: set[int] = set()  # turns (page numbers) already looked at
    moved = 0
    for _ in range(MAX_FIXES):
        pages = _layout(xml, root, mpos, style)
        units = _units(ms)
        if not pages or len(pages) != len(units):
            break  # MuseScore folded the rests differently than we expect: leave its layout alone
        last_page = pages[-1]
        fix = None
        for turn in range(2, last_page + 1, 2):  # a turn comes before pages 3, 5, 7... (0-based 2, 4, 6...)
            if turn in done:
                continue
            first = pages.index(turn)
            if first == 0 or units[first - 1][2]:
                done.add(turn)  # already turns during a rest
                continue
            before = [u for u in range(first) if pages[u] == turn - 1]
            # The latest rest in the second half of the page before the turn.
            cands = [u for u in before[len(before) // 2:] if units[u][2] and u + 1 < first]
            done.add(turn)
            if cands:
                fix = units[cands[-1] + 1][0]
                break
        if fix is None:
            break
        _set_page_break(ms[fix])
        moved += 1
    return moved


def make_parts(src: Path, out_dir: Path, title: str, log=print, progress=lambda f: None) -> list[Path]:
    """One printable PDF per instrument in out_dir/print/, plus all of them in one .zip."""
    d = print_dir(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    for old in d.iterdir():
        if old.is_file():
            old.unlink()
    work = d / "_work"
    work.mkdir(exist_ok=True)
    style = work / "part.mss"
    style.write_text(STYLE, encoding="utf-8")
    full = work / "full.musicxml"
    export.convert(src, [full])
    if not full.is_file():
        raise RuntimeError("MuseScore could not re-export the score")
    parts = _split_parts(full.read_bytes())
    made = []
    for i, (name, data) in enumerate(parts, 1):
        label = f"{i:02d} {_safe(name or f'Part {i}')}"
        log(f"Part {i}/{len(parts)}: {name or label}…")
        root = ET.fromstring(data)
        _clear_breaks(root)
        for t in root.iter("work-title"):
            t.text = f"{title} - {name}" if name else title
        xml, mpos = work / f"{label}.musicxml", work / f"{label}.mpos"
        try:
            moved = _tidy_turns(root, xml, mpos, style)
        except (ET.ParseError, OSError, ValueError):
            moved = 0
        if moved:
            log(f"  moved {moved} page turn(s) into rests")
        ET.ElementTree(root).write(xml, encoding="utf-8", xml_declaration=True)
        pdf = d / f"{label}.pdf"
        export.convert(xml, [pdf], style=style)
        if pdf.is_file():
            made.append(pdf)
        progress(0.1 + 0.85 * i / max(1, len(parts)))
    for f in work.iterdir():
        f.unlink()
    work.rmdir()
    if made:
        with zipfile.ZipFile(d / f"{_safe(title)} - parts.zip", "w", zipfile.ZIP_DEFLATED) as z:
            for f in made:
                z.write(f, f.name)
    return made
