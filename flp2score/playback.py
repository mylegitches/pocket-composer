"""What the Play screen needs: where each line of music (system) sits on the pages, and when each instrument sounds.

MuseScore writes both next to the audio, from the same score, so they line up with the .mp3:
  <stem>.mpos         measure positions (like .spos, but one box per bar)
  _play/<stem>.mid    a MIDI file whose tracks are the score's staves, used only for the instrument lights
"""

from __future__ import annotations

import copy
import json
import re
import statistics
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

from . import export

PLAY_DIR = "_play"
SCALE = 12  # MuseScore writes .spos/.mpos coordinates at 12x the SVG pages' units
UNITS_PER_INCH = 1200  # MuseScore SVG units


def strip_dir(out_dir: Path) -> Path:
    return out_dir / PLAY_DIR


def strip_ready(out_dir: Path) -> bool:
    d = strip_dir(out_dir)
    return (d / "strip.spos").is_file() and (d / "strip-1.svg").is_file() and (d / "stems.json").is_file()


# ---------------------------------------------------------------- the strip: the whole piece on one line

def _write_style(path: Path, width_in: float, height_in: float) -> None:
    path.write_text(f'''<?xml version="1.0" encoding="UTF-8"?>
<museScore version="4.60">
  <Style>
    <pageWidth>{width_in:.2f}</pageWidth>
    <pagePrintableWidth>{width_in - 1:.2f}</pagePrintableWidth>
    <pageHeight>{height_in:.2f}</pageHeight>
    <pagePrintableHeight>{height_in - 1:.2f}</pagePrintableHeight>
    <pageEvenLeftMargin>0.5</pageEvenLeftMargin>
    <pageOddLeftMargin>0.5</pageOddLeftMargin>
    <lastSystemFillLimit>1</lastSystemFillLimit>
    <enableVerticalSpread>0</enableVerticalSpread>
    <enableIndentationOnFirstSystem>0</enableIndentationOnFirstSystem>
  </Style>
</museScore>
''', encoding="utf-8")


def make_strip(src: Path, out_dir: Path, stem: str) -> None:
    """Lay the score out on one very wide page, so it reads as one continuous line of music.

    The width comes from the normal layout: the total length of all its systems, with room to spare,
    because MuseScore only spreads music out to fill a line when it is nearly full."""
    d = strip_dir(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = systems(mpos(out_dir, stem))
    total = sum(s["w"] for s in rows) / UNITS_PER_INCH
    height = max(11.0, max((s["h"] for s in rows), default=0) / UNITS_PER_INCH + 4)
    style = d / "strip.mss"
    _write_style(style, min(3000.0, total * 1.5 + 6), height)
    for old in d.glob("strip-*.svg"):
        old.unlink()
    export.convert(src, [d / "strip.svg", d / "strip.spos", d / "strip.mpos"], style=style)


def svg_layout(svg: Path) -> dict:
    """Where each staff and each instrument name is on a page, from MuseScore's tagged SVG.

    staves: [[top, bottom], ...] top to bottom (first system only); names: [[x0, y0, x1, y1], ...]."""
    text = svg.read_text(encoding="utf-8", errors="ignore")
    lines = []
    for m in re.finditer(r'class="StaffLines"[^>]*?points="([-\d.]+),([-\d.]+) ([-\d.]+),([-\d.]+)"', text):
        x0, y, _x1, _ = map(float, m.groups())
        lines.append((x0, y))
    staves: list[list[float]] = []
    if lines:
        first_x = min(x for x, _ in lines)
        ys = sorted({round(y, 1) for x, y in lines if abs(x - first_x) < 1})  # the first system's lines
        gaps = [b - a for a, b in zip(ys, ys[1:])]
        line_gap = statistics.median(sorted(gaps)[: max(1, len(gaps) // 2)]) if gaps else 0
        cur = [ys[0]]
        for a, b in zip(ys, ys[1:]):
            if b - a > line_gap * 1.6:
                staves.append([cur[0], cur[-1]])
                cur = [b]
            else:
                cur.append(b)
        staves.append([cur[0], cur[-1]])
    names = []
    for m in re.finditer(r'class="InstrumentName"[^>]*?\sd="([^"]+)"', text):
        nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", m.group(1))]
        xs, ys2 = nums[0::2], nums[1::2]
        if xs and ys2:
            names.append([min(xs), min(ys2), max(xs), max(ys2)])
    names.sort(key=lambda r: r[1])
    return {"staves": staves, "names": names}


def timeline(spos_path: Path, page_svg: Path) -> dict | None:
    """Where each note column sits and when it plays: {page_w, page_h, events: [[ms, page, x, y, w, h]]}."""
    try:
        root = ET.parse(spos_path).getroot()
        head = page_svg.read_text(encoding="utf-8", errors="ignore")[:1000]
        w, h = map(float, re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', head).groups())
    except (OSError, ValueError, AttributeError, ET.ParseError):
        return None
    els = {e.get("id"): e for e in root.iter("element")}
    events = []
    for ev in root.iter("event"):
        e = els.get(ev.get("elid"))
        if e is None:
            continue
        events.append([int(ev.get("position")), int(e.get("page")), round(float(e.get("x")) / SCALE),
                       round(float(e.get("y")) / SCALE), round(float(e.get("sx")) / SCALE),
                       round(float(e.get("sy")) / SCALE)])
    events.sort()
    return {"page_w": w, "page_h": h, "events": events}


# ---------------------------------------------------------------- one audio file per instrument

def _split_parts(xml_bytes: bytes) -> list[tuple[str, bytes]]:
    """A MusicXML score -> one MusicXML per part (its name, its bytes), in score order."""
    root = ET.fromstring(xml_bytes)
    plist = root.find("part-list")
    ids = [sp.get("id") for sp in plist.findall("score-part")] if plist is not None else []
    out = []
    for pid in ids:
        r = copy.deepcopy(root)
        pl = r.find("part-list")
        for child in list(pl):
            if child.tag == "part-group" or (child.tag == "score-part" and child.get("id") != pid):
                pl.remove(child)
        for part in r.findall("part"):
            if part.get("id") != pid:
                r.remove(part)
        sp = pl.find("score-part")
        name = (sp.findtext("part-name") or pid or "").strip() if sp is not None else pid
        out.append((name, ET.tostring(r, encoding="utf-8", xml_declaration=True)))
    return out


def make_stems(src: Path, out_dir: Path, log=print) -> list[dict]:
    """Render each instrument on its own (same score, same tempo), so the player can mute and solo them."""
    d = strip_dir(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("stem-*.mp3"):
        old.unlink()
    full = d / "full.musicxml"
    export.convert(src, [full])
    if not full.is_file():
        raise RuntimeError("MuseScore could not re-export the score")
    parts = _split_parts(full.read_bytes())
    stems = []
    for i, (name, data) in enumerate(parts):
        log(f"Rendering {name or f'part {i + 1}'} on its own ({i + 1}/{len(parts)})…")
        xml = d / f"part-{i}.musicxml"
        xml.write_bytes(data)
        mp3 = d / f"stem-{i}.mp3"
        export.convert(xml, [mp3])
        xml.unlink(missing_ok=True)
        stems.append({"name": name, "file": mp3.name if mp3.is_file() else None})
    full.unlink(missing_ok=True)
    (d / "stems.json").write_text(json.dumps(stems), encoding="utf-8")
    return stems


def play_mid(out_dir: Path, stem: str) -> Path:
    return out_dir / PLAY_DIR / f"{stem}.mid"


def mpos(out_dir: Path, stem: str) -> Path:
    return out_dir / f"{stem}.mpos"


# ---------------------------------------------------------------- systems (from .mpos)

def systems(path: Path) -> list[dict]:
    """One box per system, in playing order: {page, x, y, w, h, ms} in SVG page units.

    Bars on the same page with the same top edge belong to one system. ms is when the system starts."""
    root = ET.parse(path).getroot()
    first_ms: dict[str, int] = {}
    for ev in root.iter("event"):
        el, ms = ev.get("elid"), int(ev.get("position"))
        if el not in first_ms or ms < first_ms[el]:
            first_ms[el] = ms
    rows: dict[tuple[int, int], dict] = {}
    for e in root.iter("element"):
        page = int(e.get("page"))
        x, y = float(e.get("x")) / SCALE, float(e.get("y")) / SCALE
        w, h = float(e.get("sx")) / SCALE, float(e.get("sy")) / SCALE
        key = (page, round(y))
        s = rows.get(key)
        ms = first_ms.get(e.get("id"))
        if s is None:
            rows[key] = {"page": page, "x0": x, "y0": y, "x1": x + w, "y1": y + h, "ms": ms}
        else:
            s["x0"], s["y0"] = min(s["x0"], x), min(s["y0"], y)
            s["x1"], s["y1"] = max(s["x1"], x + w), max(s["y1"], y + h)
            if ms is not None and (s["ms"] is None or ms < s["ms"]):
                s["ms"] = ms
    out = [{"page": s["page"], "x": round(s["x0"], 1), "y": round(s["y0"], 1),
            "w": round(s["x1"] - s["x0"], 1), "h": round(s["y1"] - s["y0"], 1), "ms": s["ms"]}
           for s in sorted(rows.values(), key=lambda s: (s["page"], s["y0"]))]
    return out


# ---------------------------------------------------------------- instrument activity (from MIDI)

def _vlq(data: bytes, i: int) -> tuple[int, int]:
    v = 0
    while True:
        b = data[i]
        i += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, i


def _read_midi(path: Path) -> tuple[int, list[dict]]:
    """(ticks per quarter, tracks). Each track: {name, notes: [(start_tick, end_tick)], tempos: [(tick, us)]}."""
    data = path.read_bytes()
    if data[:4] != b"MThd":
        raise ValueError("not a MIDI file")
    hlen = struct.unpack(">I", data[4:8])[0]
    _fmt, ntrk, division = struct.unpack(">HHH", data[8:14])
    if division & 0x8000:
        raise ValueError("SMPTE-timed MIDI isn't supported")
    i = 8 + hlen
    tracks = []
    for _ in range(ntrk):
        if data[i:i + 4] != b"MTrk":
            break
        tlen = struct.unpack(">I", data[i + 4:i + 8])[0]
        j, end = i + 8, i + 8 + tlen
        tick, status = 0, 0
        name, notes, tempos, open_notes = "", [], [], {}
        while j < end:
            delta, j = _vlq(data, j)
            tick += delta
            b = data[j]
            if b == 0xFF:  # meta
                mtype = data[j + 1]
                mlen, j = _vlq(data, j + 2)
                body = data[j:j + mlen]
                j += mlen
                if mtype == 0x03 and not name:
                    name = body.decode("latin-1", errors="replace").strip()
                elif mtype == 0x51 and mlen == 3:
                    tempos.append((tick, int.from_bytes(body, "big")))
                continue
            if b in (0xF0, 0xF7):  # sysex
                slen, j = _vlq(data, j + 1)
                j += slen
                continue
            if b & 0x80:
                status = b
                j += 1
            hi, ch = status & 0xF0, status & 0x0F
            if hi in (0xC0, 0xD0):
                j += 1
                continue
            d1, d2 = data[j], data[j + 1]
            j += 2
            if hi == 0x90 and d2 > 0:
                open_notes.setdefault((ch, d1), []).append(tick)
            elif hi == 0x80 or (hi == 0x90 and d2 == 0):
                starts = open_notes.get((ch, d1))
                if starts:
                    notes.append((starts.pop(0), tick))
        tracks.append({"name": name, "notes": notes, "tempos": tempos})
        i = end
    return division, tracks


def _tick_to_ms(division: int, tempos: list[tuple[int, int]]):
    tempos = sorted(tempos) or [(0, 500000)]
    if tempos[0][0] != 0:
        tempos.insert(0, (0, 500000))
    marks = []  # (tick, ms at that tick, us per quarter)
    ms = 0.0
    for k, (t, us) in enumerate(tempos):
        if k:
            pt, _pms, pus = marks[-1]
            ms += (t - pt) * pus / division / 1000
        marks.append((t, ms, us))

    def conv(tick: int) -> int:
        lo, hi = 0, len(marks) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if marks[mid][0] <= tick:
                lo = mid
            else:
                hi = mid - 1
        t, base, us = marks[lo]
        return round(base + (tick - t) * us / division / 1000)

    return conv


def instruments(path: Path) -> list[dict]:
    """When each instrument is sounding, in score order: [{name, notes, spans, hits}].

    spans: [[start_ms, end_ms], ...] while any of its notes sounds (chords and overlaps merged).
    hits: each note start, so the light can flash on every new note even when they run together.
    Staves of one instrument (a piano's two hands) share a name and are merged."""
    division, tracks = _read_midi(path)
    conv = _tick_to_ms(division, [t for tr in tracks for t in tr["tempos"]])
    parts: dict[str, dict] = {}
    for n, tr in enumerate(tracks, 1):
        if not tr["notes"] and not tr["name"]:  # e.g. a tempo-only track; named but silent parts stay, in order
            continue
        name = tr["name"] or f"Track {n}"
        p = parts.setdefault(name, {"name": name, "notes": 0, "raw": []})
        p["notes"] += len(tr["notes"])
        p["raw"] += [(conv(a), conv(b)) for a, b in tr["notes"]]
    out = []
    for p in parts.values():
        raw = sorted(p.pop("raw"))
        spans: list[list[int]] = []
        for a, b in raw:
            b = max(b, a + 80)  # very short hits (drums) still show
            if spans and a <= spans[-1][1]:
                spans[-1][1] = max(spans[-1][1], b)
            else:
                spans.append([a, b])
        hits = sorted({a for a, _ in raw})
        out.append({**p, "spans": spans, "hits": hits})
    return out
