"""Shared helpers for building PyGuitarPro songs."""
import io

import guitarpro as gp

GP_VALUES = [1, 2, 4, 8, 16, 32, 64]


def make_song(title: str, artist: str, tempo: int, measures: list[dict]) -> gp.Song:
    """measures: dicts with sig (num, den), repeatOpen, repeatClose, alts (list[int]), marker."""
    song = gp.Song()
    song.title, song.artist, song.tempo = title or "", artist or "", int(tempo or 120)
    song.measureHeaders, song.tracks = [], []
    start = gp.Duration.quarterTime
    for i, m in enumerate(measures, 1):
        h = gp.MeasureHeader(number=i, start=start)
        num, den = m.get("sig") or (4, 4)
        h.timeSignature = gp.TimeSignature(numerator=num, denominator=gp.Duration(value=den))
        h.isRepeatOpen = bool(m.get("repeatOpen"))
        h.repeatClose = int(m["repeatClose"]) if m.get("repeatClose") else -1
        if m.get("alts"):
            h.repeatAlternative = sum(1 << (k - 1) for k in m["alts"] if 1 <= k <= 8)
        if m.get("marker"):
            h.marker = gp.Marker(title=str(m["marker"]))
        song.addMeasureHeader(h)
        start += h.length
    return song


def add_track(song: gp.Song, name: str, tuning: list[int] | None, program: int = 0, drums: bool = False) -> gp.Track:
    idx = len(song.tracks) + 1
    ch = 9 if drums else (idx - 1 if idx <= 9 else idx)
    track = gp.Track(song, number=idx, name=(name or f"Track {idx}")[:40])
    track.channel = gp.MidiChannel(channel=ch, effectChannel=ch, instrument=max(0, min(127, program)))
    track.isPercussionTrack = drums
    track.strings = [gp.GuitarString(i, 0) for i in range(1, 7)] if drums else \
        [gp.GuitarString(i, v) for i, v in enumerate(tuning, 1)]
    track.measures = [gp.Measure(track, h) for h in song.measureHeaders]
    song.tracks.append(track)
    return track


def duration(value: int, dots: int = 0, tuplet: int | None = None) -> gp.Duration:
    d = gp.Duration(value=value, isDotted=dots == 1)
    d.isDoubleDotted = dots >= 2
    if tuplet and tuplet > 1:
        t = 1
        while t * 2 < tuplet:
            t *= 2
        d.tuplet = gp.Tuplet(enters=tuplet, times=t)
    return d


def to_gp5_bytes(song: gp.Song) -> bytes:
    buf = io.BytesIO()
    gp.write(song, buf, version=(5, 1, 0))
    return buf.getvalue()
