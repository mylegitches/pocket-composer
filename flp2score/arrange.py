"""Lay the playlist out in time: every pattern clip contributes its notes at the clip's position.

Result: for each channel, a list of notes with absolute tick positions across the whole song.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .flp import Project


@dataclass(frozen=True)
class SongNote:
    start: int  # ticks from song start
    length: int  # ticks
    key: int  # MIDI note number
    velocity: int


def flatten(proj: Project) -> dict[int, list[SongNote]]:
    by_channel: dict[int, set[SongNote]] = defaultdict(set)
    for clip in proj.clips:
        if clip.pattern is None or clip.muted:
            continue
        pat = proj.patterns.get(clip.pattern)
        if not pat:
            continue
        lo = clip.start_offset
        hi = clip.start_offset + clip.length
        for n in pat.notes:
            if not lo <= n.pos < hi:
                continue
            start = clip.pos + n.pos - lo
            end = min(clip.pos + n.pos - lo + n.length, clip.pos + clip.length)
            # A set drops exact duplicates (the same clip stacked twice on the playlist).
            by_channel[n.channel].add(SongNote(start, max(1, end - start), n.key, n.velocity))
    return {ch: sorted(notes, key=lambda x: (x.start, x.key)) for ch, notes in sorted(by_channel.items())}


def song_length(proj: Project) -> int:
    return max((c.pos + c.length for c in proj.clips if c.pattern is not None), default=0)
