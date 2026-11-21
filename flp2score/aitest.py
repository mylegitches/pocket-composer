"""Check an AI model on each job it does, against pages whose right answers we know.

    run(cfg, task, work_dir) -> {"score": 0-100, "seconds", "summary", "details"}

Tasks:
  parts    read a band-chart page: title, instrument, clef, key  (needs vision)
  tab      transcribe a printed bass tab: string + fret of every note  (needs vision, long JSON)
  suggest  pick MuseScore instruments for FL Studio channel names  (text only)

The sample pages are engraved by MuseScore the first time and cached in work_dir.
"""

from __future__ import annotations

import difflib
import time
from pathlib import Path

from . import ai, assist, catalogue, export

TITLE = "Midnight Test"
PART = "Trumpet 1"

# A bass riff for the tab test: (string 1 = G ... 4 = E, fret), all eighth notes, two bars.
TAB_NOTES = [(4, 0), (4, 3), (3, 0), (3, 2), (2, 0), (2, 2), (3, 2), (4, 3),
             (4, 5), (3, 3), (3, 5), (2, 2), (2, 5), (1, 2), (2, 5), (3, 3)]
BASS_TUNING = {1: ("G", 2), 2: ("D", 2), 3: ("A", 1), 4: ("E", 1)}  # MusicXML string -> open pitch
STEPS = ["C", "C", "D", "D", "E", "F", "F", "G", "G", "A", "A", "B"]
ALTER = [0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0]
OPEN_MIDI = {1: 43, 2: 38, 3: 33, 4: 28}


def _parts_xml() -> str:
    notes = "".join(f"<note><pitch><step>{s}</step>{'<alter>-1</alter>' if s == 'B' else ''}<octave>{o}</octave></pitch>"
                    f"<duration>1</duration><type>quarter</type></note>"
                    for s, o in [("F", 4), ("A", 4), ("C", 5), ("B", 4)] * 1)
    measures = "".join(
        f'<measure number="{i}">' + (
            "<attributes><divisions>1</divisions><key><fifths>-1</fifths></key><time><beats>4</beats>"
            "<beat-type>4</beat-type></time><clef><sign>G</sign><line>2</line></clef></attributes>" if i == 1 else "")
        + notes + "</measure>" for i in range(1, 17))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <work><work-title>{TITLE}</work-title></work>
  <credit page="1"><credit-type>title</credit-type><credit-words justify="center" valign="top" default-x="600" default-y="1600" font-size="22">{TITLE}</credit-words></credit>
  <credit page="1"><credit-type>part name</credit-type><credit-words justify="left" valign="top" default-x="80" default-y="1600" font-size="14">{PART}</credit-words></credit>
  <part-list><score-part id="P1"><part-name>{PART}</part-name></score-part></part-list>
  <part id="P1">{measures}</part>
</score-partwise>"""


def _tab_xml() -> str:
    def note(string, fret):
        midi = OPEN_MIDI[string] + fret
        pc, octave = midi % 12, midi // 12 - 1
        alter = f"<alter>{ALTER[pc]}</alter>" if ALTER[pc] else ""
        return (f"<note><pitch><step>{STEPS[pc]}</step>{alter}<octave>{octave}</octave></pitch><duration>1</duration>"
                f"<type>eighth</type><notations><technical><string>{string}</string><fret>{fret}</fret>"
                f"</technical></notations></note>")

    tuning = "".join(f'<staff-tuning line="{5 - s}"><tuning-step>{BASS_TUNING[s][0]}</tuning-step>'
                     f"<tuning-octave>{BASS_TUNING[s][1]}</tuning-octave></staff-tuning>" for s in (4, 3, 2, 1))
    bars = []
    for b in range(2):
        attrs = ("<attributes><divisions>2</divisions><key><fifths>0</fifths></key><time><beats>4</beats>"
                 "<beat-type>4</beat-type></time><clef><sign>TAB</sign><line>5</line></clef>"
                 f"<staff-details><staff-lines>4</staff-lines>{tuning}</staff-details></attributes>"
                 "<direction placement=\"above\"><direction-type><metronome><beat-unit>quarter</beat-unit>"
                 "<per-minute>96</per-minute></metronome></direction-type><sound tempo=\"96\"/></direction>") if b == 0 else ""
        bars.append(f'<measure number="{b + 1}">{attrs}' + "".join(note(*n) for n in TAB_NOTES[b * 8:(b + 1) * 8])
                    + "</measure>")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <work><work-title>Tab Test</work-title></work>
  <part-list><score-part id="P1"><part-name>Electric Bass</part-name>
    <score-instrument id="P1-I1"><instrument-name>Electric Bass</instrument-name></score-instrument>
    <midi-instrument id="P1-I1"><midi-program>34</midi-program></midi-instrument></score-part></part-list>
  <part id="P1">{''.join(bars)}</part>
</score-partwise>"""


def _page(work: Path, name: str, xml: str) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    png = work / f"{name}-1.png"
    if not png.exists():
        src = work / f"{name}.musicxml"
        src.write_text(xml, encoding="utf-8")
        export.convert(src, [work / f"{name}.png"])  # MuseScore writes name-1.png per page
        if not png.exists():
            raise RuntimeError("MuseScore could not engrave the test page")
    return png


def _parts(cfg: dict, work: Path) -> dict:
    img = assist._page_image(_page(work, "parts", _parts_xml()))  # same downscaling as real pages
    d = ai.ask_json(cfg, assist.PAGE_SYSTEM, assist.PAGE_PROMPT.format(n=1, total=1), [img])
    if not isinstance(d, dict):
        raise ai.AIError("expected a JSON object")
    checks = {
        "title": TITLE.lower() in str(d.get("title") or "").lower(),
        "instrument": "trumpet" in str(d.get("instrument") or "").lower(),
        "clef": d.get("clef") == "G2",
        "key": d.get("fifths") == -1,
    }
    got = f"title {d.get('title')!r}, instrument {d.get('instrument')!r}, clef {d.get('clef')}, key {d.get('fifths')}"
    return {"score": round(100 * sum(checks.values()) / len(checks)),
            "summary": f"{sum(checks.values())}/4 right — {got}", "details": checks}


def _tab(cfg: dict, work: Path) -> dict:
    from tabmaker import tabread

    img = _page(work, "tab", _tab_xml()).read_bytes()  # read_tab downscales it like real photos
    tab = tabread.read_tab(cfg, [img])
    got = [(n.get("string"), n.get("fret")) for m in tab.get("measures", []) for b in m.get("beats", [])
           if not b.get("rest") for n in b.get("notes", [])]
    want = [(s - 1, f) for s, f in TAB_NOTES]  # tab JSON numbers strings from 0 = top line
    ratio = difflib.SequenceMatcher(None, want, got, autojunk=False).ratio()
    rhythm = sum(1 for m in tab.get("measures", []) for b in m.get("beats", []) if b.get("type") == 8)
    return {"score": round(100 * ratio),
            "summary": f"{round(100 * ratio)}% of {len(want)} notes right ({len(got)} read, "
                       f"{rhythm} as eighth notes); tuning {tab.get('tuning')}",
            "details": {"want": want, "got": got}}


SUGGEST_CASE = [
    ("Grand Piano", lambda i: i.group == "Keyboards" and "piano" in i.id),
    ("808 Kick", lambda i: bool(i.drums)),
    ("Cello Legato", lambda i: i.id in ("violoncello", "cello")),
    ("Sub Bass", lambda i: "bass" in i.id),
    ("Supersaw Lead", lambda i: i.group == "Electronic"),
]


def _suggest(cfg: dict, work: Path) -> dict:
    chans = [{"channel": n, "name": name, "notes": 32, "low": "C2", "high": "C5"} for n, (name, _) in
             enumerate(SUGGEST_CASE)]
    rows = {r["channel"]: r for r in assist.suggest_mapping(cfg, chans, [])}
    cat = catalogue.all_instruments()
    picks, ok = [], 0
    for n, (name, good) in enumerate(SUGGEST_CASE):
        ins = cat.get((rows.get(n) or {}).get("instrument") or "")
        right = bool(ins and good(ins))
        ok += right
        picks.append(f"{name} → {ins.name if ins else '?'}{'' if right else ' ✗'}")
    return {"score": round(100 * ok / len(SUGGEST_CASE)), "summary": f"{ok}/{len(SUGGEST_CASE)} sensible — " + "; ".join(picks),
            "details": picks}


TASKS = {"parts": _parts, "tab": _tab, "suggest": _suggest}


def run(cfg: dict, task: str, work: Path) -> dict:
    t0 = time.time()
    res = TASKS[task](ai.for_task(cfg, task), work)
    res["seconds"] = round(time.time() - t0, 1)
    res["model"] = ai.for_task(cfg, task)["model"]
    return res
