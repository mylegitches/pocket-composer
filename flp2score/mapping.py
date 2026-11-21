"""Per-project choices: which channels to include and which MuseScore instrument each becomes.

Saved as mapping.json next to the .flp, so a re-run gives the same score.
Channels with the same part name and instrument are merged into one part (e.g. two melody
channels that take turns, or all the drum channels on one drum staff)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import catalogue, instruments
from .arrange import SongNote
from .flp import Project

FILE_NAME = "mapping.json"


@dataclass
class ChannelMap:
    channel: int
    include: bool = True
    instrument: str = "piano"  # MuseScore instrument id
    part: str = ""  # part name shown in the score; same name + instrument = merged part
    staves: str = "auto"  # "auto" | "1" | "2": "auto" uses one staff when the notes fit on one
    drum_sound: str = "auto"  # drum instruments only: "auto" | "keys" | GM drum note number
    transpose: int = 0  # semitones, applied before notating (e.g. -12 for a part written an octave high)


@dataclass
class Mapping:
    title: str = ""
    channels: dict[int, ChannelMap] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"title": self.title, "channels": [asdict(c) for c in self.channels.values()]}

    @classmethod
    def from_json(cls, d: dict) -> Mapping:
        chans = {}
        for c in d.get("channels", []):
            known = {k: v for k, v in c.items() if k in ChannelMap.__dataclass_fields__}
            cm = ChannelMap(**known)
            cm.drum_sound = str(cm.drum_sound)
            cm.staves = str(cm.staves)
            chans[int(cm.channel)] = cm
        return cls(title=d.get("title", ""), channels=chans)


def default(proj: Project, channels: dict[int, list[SongNote]]) -> Mapping:
    m = Mapping(title=proj.title)
    for ch in channels:
        name = proj.channels.get(ch) or f"Channel {ch}"
        ins = instruments.guess_instrument(name)
        is_drums = catalogue.get(ins).is_drums
        m.channels[ch] = ChannelMap(channel=ch, instrument=ins, part="Drums" if is_drums else name)
    return m


def merged(saved: Mapping | None, proj: Project, channels: dict[int, list[SongNote]]) -> Mapping:
    """Saved choices where there are any, guesses for channels the saved mapping doesn't cover."""
    base = default(proj, channels)
    if saved:
        base.title = saved.title or base.title
        for ch, cm in saved.channels.items():
            if ch in base.channels:
                base.channels[ch] = cm
    return base


def load(project_dir: Path) -> Mapping | None:
    f = project_dir / FILE_NAME
    if not f.exists():
        return None
    return Mapping.from_json(json.loads(f.read_text(encoding="utf-8")))


def save(project_dir: Path, m: Mapping) -> Path:
    f = project_dir / FILE_NAME
    f.write_text(json.dumps(m.to_json(), indent=2), encoding="utf-8")
    return f
