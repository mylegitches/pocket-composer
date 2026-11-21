"""Sheet music (PDF scans, phone photos) -> score. This is musescore-maker v2's pipeline:

1. pages.render     PDF/photos -> one 300-dpi PNG per page (cached in _work/pages)
2. part plan        which pages belong to which instrument part. Read by the configured AI
                    endpoint when there is one (assist.plan_pages), else by OCR of the page
                    headers (split.detect). Saved as parts.json next to the upload; editable.
3. omr.recognise    homr (local deep-learning OMR) reads the notes, one page at a time (cached)
4. PartBuilder      clean-up per part: clef/key repair, multi-bar rests, bar lengths, instrument
5. export           full score + one file per part, through the MuseScore command line
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from musescore_maker import instruments, multirest, omr, pages, split
from musescore_maker.musicxml import PartBuilder, load_measures, write_score

from . import convert as conv

PLAN_FILE = "parts.json"
MODE_FILE = "mode.json"  # {"read_as": "notation" | "tab"}
Log = Callable[[str], None]


def safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "-", name).strip(" .") or "score"


def render_pages(src_dir: Path, work: Path) -> list[Path]:
    return pages.render(src_dir, work / "pages")


def read_as(src_dir: Path) -> str:
    import json

    try:
        return json.loads((src_dir / MODE_FILE).read_text(encoding="utf-8")).get("read_as", "notation")
    except (OSError, ValueError):
        return "notation"


def set_read_as(src_dir: Path, mode: str) -> None:
    import json

    (src_dir / MODE_FILE).write_text(json.dumps({"read_as": mode}), encoding="utf-8")


def process_tab(src_dir: Path, work: Path, out_dir: Path, stem: str, formats: list[str], ai_cfg: dict, log: Log,
                progress: Callable[[float], None] = lambda f: None) -> conv.Result:
    """Guitar/bass tab photos -> Guitar Pro song (read by the AI endpoint, as musescore-maker v1 did
    with Claude) -> MuseScore for the other formats."""
    from tabmaker import gpsong, songsterr_song, tabread

    from . import ai

    ai_cfg = ai.for_task(ai_cfg, "tab")
    page_files = render_pages(src_dir, work)
    log(f"Sending {len(page_files)} page(s) to {ai_cfg.get('model')} to read the tab (this can take a few minutes)…")
    progress(0.1)
    tab = tabread.read_tab(ai_cfg, [p.read_bytes() for p in page_files])
    log(f"Read {len(tab.get('measures', []))} measures: {tab.get('title') or stem}, {tab.get('instrument', '')}")
    song = songsterr_song.build_song([tabread.to_songsterr_part(tab)], tab.get("title", ""), tab.get("artist", ""))
    work.mkdir(parents=True, exist_ok=True)
    gp = work / "tab.gp5"
    gp.write_bytes(gpsong.to_gp5_bytes(song))
    progress(0.7)
    log("MuseScore: writing the score…")
    res = conv.export_file(gp, out_dir, stem, formats, title=tab.get("title") or stem)
    res.parts = [{"part": t.name, "instrument": "tab", "notes": sum(len(b.notes) for m in t.measures
                                                                     for v in m.voices for b in v.beats)}
                 for t in song.tracks]
    progress(1.0)
    return res


def load_plan(src_dir: Path) -> split.Plan | None:
    f = src_dir / PLAN_FILE
    return split.Plan.load(f) if f.exists() else None


def save_plan(src_dir: Path, plan: split.Plan) -> None:
    plan.save(src_dir / PLAN_FILE)


def detect_plan(src_dir: Path, work: Path, title: str, ai_cfg: dict | None, log: Log) -> split.Plan:
    from . import ai, assist

    page_files = render_pages(src_dir, work)
    log(f"{len(page_files)} page(s)")
    if ai.configured(ai_cfg):
        cfg = ai.for_task(ai_cfg, "parts")
        log(f"Reading the pages with {cfg['model']}…")
        plan = assist.plan_pages(cfg, page_files, title, log)
    else:
        log("No AI endpoint set up: reading page headers with OCR…")
        plan = split.detect(page_files, title, log=lambda m: log(m.strip()))
    save_plan(src_dir, plan)
    return plan


def process(src_dir: Path, work: Path, out_dir: Path, stem: str, formats: list[str], log: Log,
            progress: Callable[[float], None] = lambda f: None) -> conv.Result:
    """Run OMR and build the score. Uses the saved part plan (detect_plan makes one if missing)."""
    page_files = render_pages(src_dir, work)
    plan = load_plan(src_dir)
    if plan is None:
        raise RuntimeError("Find the parts first.")

    todo = [(part, n) for part in plan.parts if not part.skip for n in part.pages if 1 <= n <= len(page_files)]
    omr_dir = work / "omr-homr"
    omr_dir.mkdir(parents=True, exist_ok=True)
    for i, (part, n) in enumerate(todo, 1):
        xml = omr_dir / f"p{n:03d}.musicxml"
        if not (xml.exists() or xml.with_suffix(".empty").exists()):
            log(f"Reading notes on page {n} ({part.name}) — {i}/{len(todo)}")
            omr.recognise(page_files[n - 1], xml, "homr")
        progress(0.85 * i / max(1, len(todo)))

    built = []
    for part in plan.parts:
        if part.skip:
            continue
        nums = [n for n in part.pages if (omr_dir / f"p{n:03d}.musicxml").exists()]
        if not nums:
            continue
        inst, _ = instruments.from_display_name(part.name)
        log(f"Building {part.name} ({inst.name})")
        page_measures = []
        for n in nums:
            xml = omr_dir / f"p{n:03d}.musicxml"
            measures = load_measures(xml)
            if inst.staves == 1:
                hits = multirest.detect(page_files[n - 1], omr.staff_file(xml), split.get_ocr())
                multirest.apply(measures, hits)
            page_measures.append(measures)
        measures = PartBuilder(inst, part.clef, part.fifths, log=lambda m: log(m.strip())).build(page_measures)
        if measures:
            built.append((part.name, inst, measures))
    res = conv.Result(title=plan.title, out_dir=out_dir,
                      parts=[{"part": n, "instrument": i.name, "notes": sum(len(m.findall(".//note")) for m in ms)}
                             for n, i, ms in built])
    if not built:
        log("No music recognised.")
        return res

    xml_dir = work / "xml"
    xml_dir.mkdir(parents=True, exist_ok=True)
    score_xml = xml_dir / "score.musicxml"
    write_score(score_xml, plan.title, built, pad=len(built) > 1)
    log("MuseScore: writing the full score…")
    progress(0.9)
    full = conv.export_file(score_xml, out_dir, stem, formats, title=plan.title,
                            gp5_instruments=[inst for _, inst, _ in built])
    res.files, res.failed = full.files, full.failed

    if len(built) > 1:  # one file per part, like musescore-maker's parts/ folder
        parts_dir = out_dir / "parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        for old in parts_dir.iterdir():
            if old.is_file():
                old.unlink()
        for i, (name, inst, measures) in enumerate(built, 1):
            label = f"{i:02d} {safe(name)}"
            xml = xml_dir / f"{label}.musicxml"
            write_score(xml, f"{plan.title} - {name}", [(name, inst, measures)])
            log(f"MuseScore: part {i}/{len(built)} ({name})")
            conv.export_file(xml, parts_dir, label, [f for f in formats if f in ("mscz", "pdf")] or ["pdf"],
                             previews=False)
    progress(1.0)
    return res
