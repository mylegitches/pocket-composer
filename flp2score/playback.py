"""What the Play screen needs: where each line of music (system) sits on the pages, and when each instrument sounds.

MuseScore writes both next to the audio, from the same score, so they line up with the .mp3:
  <stem>.mpos         measure positions (like .spos, but one box per bar)
  _play/<stem>.mid    a MIDI file whose tracks are the score's staves, used only for the instrument lights
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from pathlib import Path

PLAY_DIR = "_play"
SCALE = 12  # MuseScore writes .spos/.mpos coordinates at 12x the SVG pages' units


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
        if not tr["notes"]:
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
