"""MuseScore's instrument list (generated from instruments.xml by tools/build_catalogue.py)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).with_name("catalogue.json")


@dataclass(frozen=True)
class MsInstrument:
    id: str
    group: str
    name: str
    short: str
    xml: str  # MusicXML instrument-sound id; MuseScore picks the instrument by it
    staves: int
    clefs: tuple[str, ...]
    transpose: tuple[int, int]  # (diatonic, chromatic): written -> sounding
    program: int
    genres: tuple[str, ...] = ()
    drums: dict[int, dict] = field(default_factory=dict)  # GM pitch -> {line, head, name}
    range: tuple[int, int] | None = None  # comfortable (amateur) range, sounding MIDI pitches
    description: str = ""

    @property
    def is_drums(self) -> bool:
        return bool(self.drums)


@lru_cache
def all_instruments() -> dict[str, MsInstrument]:
    raw = json.loads(PATH.read_text(encoding="utf-8"))
    out = {}
    for r in raw:
        out[r["id"]] = MsInstrument(
            id=r["id"], group=r["group"], name=r["name"], short=r["short"], xml=r["xml"], staves=r["staves"],
            clefs=tuple(r["clefs"]), transpose=tuple(r["transpose"]), program=r["program"],
            genres=tuple(r.get("genres", ())),
            drums={int(k): v for k, v in r.get("drums", {}).items()},
            range=tuple(r["range"]) if r.get("range") else None,
            description=r.get("description", ""),
        )
    return out


def get(ins_id: str) -> MsInstrument:
    cat = all_instruments()
    return cat.get(ins_id) or cat["piano"]
