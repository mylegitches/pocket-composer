"""Reader for FL Studio project files (.flp).

An .flp is a 'FLhd' header chunk followed by a 'FLdt' chunk holding a flat stream of events.
Each event is an id byte followed by a value whose size depends on the id:
  0-63 -> 1 byte, 64-127 -> 2 bytes, 128-191 -> 4 bytes, 192-255 -> varint length + data.
Only the events needed to rebuild the notes and the arrangement are decoded here.
Tested on projects saved by FL 21.0, 21.2 and 26.1.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

# Event ids
EV_TS_NUM = 17
EV_TS_DEN = 18
EV_CHANNEL = 64  # starts a channel's block; value = channel index
EV_PATTERN = 65  # starts a pattern's block; value = pattern id
EV_TEMPO = 156  # tempo * 1000
EV_PATTERN_NAME = 193
EV_CHANNEL_NAME = 203  # plugin / channel display name (also used for mixer inserts)
EV_VERSION = 199
EV_NOTES = 224
EV_PLAYLIST = 233
EV_MIXER_INSERT = 236  # marks the start of mixer data: later 203 events name inserts, not channels

PATTERN_BASE = 0x5000
NOTE_SIZE = 24


@dataclass
class Note:
    channel: int
    pos: int  # ticks from pattern start
    length: int  # ticks
    key: int  # MIDI note number (FL's C5 == MIDI 60)
    velocity: int  # 0-128 in FL
    porta: bool = False


@dataclass
class Pattern:
    id: int
    name: str = ""
    notes: list[Note] = field(default_factory=list)


@dataclass
class Clip:
    pos: int  # ticks
    length: int  # ticks
    track: int  # playlist track, 0 = top
    pattern: int | None  # pattern id, or None for an audio/automation clip
    start_offset: int = 0  # ticks trimmed from the start of the pattern
    muted: bool = False


@dataclass
class Project:
    path: Path
    version: str = ""
    ppq: int = 96
    tempo: float = 120.0
    time_sig: tuple[int, int] = (4, 4)
    channels: dict[int, str] = field(default_factory=dict)
    patterns: dict[int, Pattern] = field(default_factory=dict)
    clips: list[Clip] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.path.stem


def _events(data: bytes, start: int):
    p = start
    n_data = len(data)
    while p < n_data:
        ev = data[p]
        p += 1
        if ev < 64:
            n = 1
        elif ev < 128:
            n = 2
        elif ev < 192:
            n = 4
        else:
            n = shift = 0
            while True:
                b = data[p]
                p += 1
                n |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    break
        yield ev, data[p:p + n]
        p += n


def _text(v: bytes) -> str:
    # FL 12+ writes UTF-16LE; older projects wrote ASCII.
    if len(v) >= 2 and v[1:2] == b"\0":
        return v.decode("utf-16-le", "replace").rstrip("\0").strip()
    return v.decode("latin-1").rstrip("\0").strip()


def _clip_record_size(v: bytes) -> int:
    """Playlist items are 60 bytes up to FL 21 and 88 bytes in FL 26 (maybe earlier)."""
    for size in (60, 88, 32):
        if len(v) % size == 0 and all(
            struct.unpack_from("<H", v, i + 4)[0] == PATTERN_BASE for i in range(0, len(v), size)
        ):
            return size
    raise ValueError(f"unrecognised playlist record layout ({len(v)} bytes)")


def _decode_clips(v: bytes, ppq: int) -> list[Clip]:
    if not v:
        return []
    size = _clip_record_size(v)
    clips = []
    for i in range(0, len(v), size):
        pos, base, idx, length, track_rv = struct.unpack_from("<IHHIH", v, i)
        flags = struct.unpack_from("<H", v, i + 18)[0]
        start_off = struct.unpack_from("<f", v, i + 24)[0]
        pattern = idx - base if idx >= base else None
        # Offsets are stored as floats; -1 means "not trimmed". For pattern clips they are ticks.
        off = int(round(start_off)) if pattern is not None and start_off > 0 else 0
        clips.append(Clip(pos=pos, length=length, track=499 - track_rv, pattern=pattern,
                          start_offset=off, muted=bool(flags & 0x2000)))
    return clips


def read(path: str | Path) -> Project:
    path = Path(path)
    data = path.read_bytes()
    if data[:4] != b"FLhd":
        raise ValueError(f"{path.name} is not an FL Studio project")
    header_len = struct.unpack_from("<I", data, 4)[0]
    proj = Project(path=path)
    proj.ppq = struct.unpack_from("<H", data, 12)[0]
    body = 8 + header_len
    if data[body:body + 4] != b"FLdt":
        raise ValueError("missing FLdt chunk")

    channel = None
    pattern = None
    in_mixer = False
    playlist_raw = b""
    num, den = 4, 4
    for ev, v in _events(data, body + 8):
        if ev == EV_VERSION:
            proj.version = v.decode("ascii", "replace").rstrip("\0")
        elif ev == EV_TEMPO:
            proj.tempo = int.from_bytes(v, "little") / 1000
        elif ev == EV_TS_NUM:
            num = v[0]
        elif ev == EV_TS_DEN:
            den = v[0]
        elif ev == EV_CHANNEL:
            channel = int.from_bytes(v, "little")
            proj.channels.setdefault(channel, "")
        elif ev == EV_MIXER_INSERT:
            in_mixer = True
        elif ev == EV_CHANNEL_NAME and channel is not None and not in_mixer:
            # The first name after a channel starts is its display name.
            if not proj.channels.get(channel):
                proj.channels[channel] = _text(v)
        elif ev == EV_PATTERN:
            pattern = int.from_bytes(v, "little")
            proj.patterns.setdefault(pattern, Pattern(pattern))
        elif ev == EV_PATTERN_NAME and pattern is not None:
            proj.patterns[pattern].name = _text(v)
        elif ev == EV_NOTES and pattern is not None:
            pat = proj.patterns.setdefault(pattern, Pattern(pattern))
            for i in range(0, len(v) - len(v) % NOTE_SIZE, NOTE_SIZE):
                pos, flags, rack, length, key, _group, _fine, _u1, _rel, _midich, _pan, vel = \
                    struct.unpack_from("<IHHIHHBBBBBB", v, i)
                pat.notes.append(Note(channel=rack, pos=pos, length=max(1, length), key=key & 0xFF,
                                      velocity=vel, porta=bool(flags & 0x4000)))
        elif ev == EV_PLAYLIST:
            playlist_raw += v
    proj.time_sig = (num or 4, den or 4)
    proj.clips = _decode_clips(playlist_raw, proj.ppq)
    return proj


def describe(proj: Project) -> str:
    names = "C C# D D# E F F# G G# A A# B".split()

    def nn(k):
        return f"{names[k % 12]}{k // 12 - 1}"

    q = proj.ppq
    lines = [f"{proj.title}: FL {proj.version}, {proj.tempo:g} BPM, {proj.time_sig[0]}/{proj.time_sig[1]}, PPQ {q}"]
    lines.append("  channels: " + ", ".join(f"{i}={n or '?'}" for i, n in sorted(proj.channels.items())))
    for pid, pat in sorted(proj.patterns.items()):
        if not pat.notes:
            continue
        chans = sorted({n.channel for n in pat.notes})
        keys = [n.key for n in pat.notes]
        lines.append(f"  pattern {pid:>2} {pat.name!r:24} {len(pat.notes):3} notes  ch {chans}  "
                     f"{nn(min(keys))}-{nn(max(keys))}")
    pclips = [c for c in proj.clips if c.pattern is not None]
    other = len(proj.clips) - len(pclips)
    end = max((c.pos + c.length for c in proj.clips), default=0) / q
    lines.append(f"  playlist: {len(pclips)} pattern clips, {other} audio/automation clips, ends at beat {end:g}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    for f in sys.argv[1:]:
        print(describe(read(f)))
