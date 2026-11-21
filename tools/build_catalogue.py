"""Build flp2score/catalogue.json from MuseScore's instruments.xml.

    python tools/build_catalogue.py path/to/instruments.xml

instruments.xml comes from the MuseScore source, e.g.
https://raw.githubusercontent.com/musescore/MuseScore/v4.7.6/share/instruments/instruments.xml
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "flp2score" / "catalogue.json"
GROUP_NAMES = {
    "woodwinds": "Woodwinds", "free-reed": "Free reed", "brass": "Brass", "pitched-percussion": "Pitched percussion",
    "unpitched-percussion": "Drums & percussion", "marching-percussion": "Marching percussion",
    "body-percussion": "Body percussion", "vocals": "Voices", "keyboards": "Keyboards",
    "electronic-instruments": "Electronic", "plucked-strings": "Plucked strings", "strings": "Strings",
}


def text(el, tag, default=None):
    t = el.findtext(tag)
    return t.strip() if t else default


def parse(path: Path) -> list[dict]:
    root = ET.parse(path).getroot()
    raw: dict[str, tuple[str, ET.Element]] = {}
    for group in root.iter("InstrumentGroup"):
        for ins in group.findall("Instrument"):
            raw[ins.get("id")] = (group.get("id"), ins)

    def field(ins_id, getter, seen=()):
        _g, ins = raw[ins_id]
        v = getter(ins)
        if v is None and (base := text(ins, "init")) and base in raw and base not in seen:
            return field(base, getter, (*seen, ins_id))
        return v

    out = []
    for ins_id, (group, ins) in raw.items():
        def clefs(el):
            # Scores are written at written pitch, so a transposing instrument uses its transposingClef.
            cs = {c.get("staff", "1"): c.text.strip() for tag in ("clef", "transposingClef")
                  for c in el.findall(tag) if c.text}
            return cs or None

        def drums(el):
            ds = {}
            for d in el.findall("Drum"):
                ds[int(d.get("pitch"))] = {"line": int(text(d, "line", "0")), "head": text(d, "head", "normal"),
                                           "name": text(d, "name", "")}
            return ds or None

        def program(el):
            p = el.find("Channel/program")
            return int(p.get("value")) if p is not None else None

        cl = field(ins_id, clefs) or {"1": "G"}
        staves = int(field(ins_id, lambda e: text(e, "staves")) or 1)
        # trackName repeats across keys ("Horn" for Horn in F, E♭, D...); longName tells them apart.
        name = field(ins_id, lambda e: text(e, "longName")) or field(ins_id, lambda e: text(e, "trackName")) or ins_id
        rng = field(ins_id, lambda e: text(e, "aPitchRange")) or field(ins_id, lambda e: text(e, "pPitchRange"))
        entry = {
            "id": ins_id,
            "group": GROUP_NAMES.get(group, group),
            "name": name,
            "short": field(ins_id, lambda e: text(e, "shortName")) or name[:4] + ".",
            "xml": field(ins_id, lambda e: text(e, "musicXMLid")) or "",
            "staves": staves,
            "clefs": [cl.get(str(i + 1), cl.get("1")) for i in range(staves)],
            "transpose": [int(field(ins_id, lambda e: text(e, "transposeDiatonic")) or 0),
                          int(field(ins_id, lambda e: text(e, "transposeChromatic")) or 0)],
            "program": field(ins_id, program) or 0,
            "genres": [g.text for g in ins.findall("genre")],
            "range": [int(x) for x in rng.split("-")] if rng else None,
            "description": field(ins_id, lambda e: text(e, "description")) or "",
        }
        d = field(ins_id, drums) if text(ins, "drumset") == "1" or field(ins_id, lambda e: text(e, "drumset")) == "1" \
            else None
        if d:
            entry["drums"] = d
        out.append(entry)
    return out


KEYS = {"c": "C", "db": "D♭", "d": "D", "eb": "E♭", "e": "E", "f": "F", "gb": "G♭", "g": "G",
        "ab": "A♭", "a": "A", "bb": "B♭", "b": "B"}
SOUNDING = ["C", "D♭", "D", "E♭", "E", "F", "G♭", "G", "A♭", "A", "B♭", "B"]
WORDS = {"tab": "tab", "tablature": "tab", "str": "string", "chrom": "chromatic"}


def disambiguate(cat: list[dict]) -> None:
    """Instruments that share a name (Horn in F / E♭ / D..., tab vs staff) get their key and variant added."""
    def count():
        c: dict[str, int] = {}
        for e in cat:
            c[e["name"]] = c.get(e["name"], 0) + 1
        return c

    seen = count()
    for e in cat:
        if seen[e["name"]] < 2:
            continue
        tokens = e["id"].strip(")").replace("bass-clef", "bass_clef").split("-")
        key = KEYS.get(tokens[0]) if len(tokens) > 1 else None
        if key:
            tokens = tokens[1:]
        elif e["transpose"][1] % 12:
            key = SOUNDING[e["transpose"][1] % 12]
        name_words = {w.lower().strip(".") for w in e["name"].replace("-", " ").split()}
        extra = [] if len(tokens) < 2 else [WORDS.get(t, t).replace("_", " ") for t in tokens
                                            if t.strip(".") not in name_words and t not in ("str", "string")]
        label = e["name"] + (f" in {key}" if key else "")
        if extra:
            label += f" ({' '.join(extra)})"
        e["name"] = label
    seen = count()
    for e in cat:
        if seen[e["name"]] > 1:
            e["name"] = f'{e["name"]} [{e["id"]}]'


def main() -> None:
    src = Path(sys.argv[1])
    cat = parse(src)
    disambiguate(cat)
    OUT.write_text(json.dumps(cat, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(cat)} instruments -> {OUT}")


if __name__ == "__main__":
    main()
