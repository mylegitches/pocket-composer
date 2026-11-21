"""The jobs the configured AI endpoint does: reading sheet-music pages, suggesting instruments."""

from __future__ import annotations

import io
from collections import Counter
from pathlib import Path

from PIL import Image

from musescore_maker import instruments, split

from . import ai, catalogue

KNOWN_PARTS = ", ".join(i.name for i in instruments.INSTRUMENTS)

PAGE_SYSTEM = (
    "You read pages of printed or handwritten sheet music and report what is on them as JSON. "
    "Be literal: report only what is printed. Use null when something is not shown."
)
PAGE_PROMPT = f"""This is page {{n}} of {{total}} of a scanned music file.
Return a JSON object with:
  "title": the song title printed on the page, or null
  "instrument": the instrument this page is for, as printed in the header (e.g. "Alto Sax 1"), or null.
                Use one of these names when it fits, plus the part number: {KNOWN_PARTS}
  "first_page": true if this page starts a part (title and instrument name at the top), else false
  "full_score": true if the page is a full score (several instruments labelled down the left margin)
  "has_music": true if the page has music staves
  "clef": clef of the first staff: "G2" (treble), "F4" (bass), "C3" (alto), "C4" (tenor), "percussion", or null
  "fifths": key signature of the first staff as a number: sharps positive, flats negative, 0 for none"""


def _page_image(path: Path, long_side: int = 1600) -> bytes:
    im = Image.open(path).convert("L")
    s = long_side / max(im.size)
    if s < 1:
        im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return buf.getvalue()


def _clean_name(name: str | None) -> str | None:
    if not name or not str(name).strip():
        return None
    hit = instruments.match(str(name))
    return instruments.display_name(*hit) if hit else str(name).strip().title()


def plan_pages(cfg: dict, page_files: list[Path], fallback_title: str, log=print) -> split.Plan:
    reads = []
    for n, p in enumerate(page_files, 1):
        try:
            d = ai.ask_json(cfg, PAGE_SYSTEM, PAGE_PROMPT.format(n=n, total=len(page_files)), [_page_image(p)])
            if not isinstance(d, dict):
                raise ai.AIError("expected a JSON object")
        except ai.AIError as e:
            if n == 1:
                raise  # a broken endpoint: say so instead of grinding through every page
            log(f"page {n}: AI couldn't read it ({e})")
            d = {}
        name = _clean_name(d.get("instrument"))
        log(f"page {n}: " + ("full score (skipped)" if d.get("full_score") else name or "—"))
        reads.append({**d, "instrument": name})

    titles = Counter(str(r["title"]).strip() for r in reads if r.get("title"))
    title = titles.most_common(1)[0][0] if titles else fallback_title

    parts: list[split.Part] = []
    score_pages: list[int] = []
    current: split.Part | None = None
    votes: dict[int, tuple[Counter, Counter]] = {}
    for n, r in enumerate(reads, 1):
        if r.get("full_score"):
            score_pages.append(n)
            current = None
            continue
        if r.get("has_music") is False and not r.get("instrument"):
            continue  # cover or blank page
        name = r.get("instrument")
        if current is None or (name and name != current.name) or (r.get("first_page") and current.pages and name):
            current = split.Part(name=name or f"Part {len(parts) + 1}", pages=[])
            parts.append(current)
            votes[id(current)] = (Counter(), Counter())
        current.pages.append(n)
        clefs, keys = votes[id(current)]
        if r.get("clef") in ("G2", "F4", "C3", "C4", "percussion"):
            clefs[r["clef"]] += 1
        if isinstance(r.get("fifths"), int) and -7 <= r["fifths"] <= 7:
            keys[r["fifths"]] += 1
    for part in parts:
        clefs, keys = votes[id(part)]
        # Only pin clef/key when the AI read them consistently; otherwise let the clean-up vote.
        if clefs and clefs.most_common(1)[0][1] >= max(1, len(part.pages) * 0.6):
            part.clef = clefs.most_common(1)[0][0]
        if keys and keys.most_common(1)[0][1] >= max(1, len(part.pages) * 0.6):
            part.fifths = keys.most_common(1)[0][0]
    seen: Counter[str] = Counter()
    for part in parts:
        seen[part.name] += 1
        if seen[part.name] > 1:
            part.name = f"{part.name} ({seen[part.name]})"
    return split.Plan(title=title, parts=parts, score_pages=score_pages)


MAP_SYSTEM = (
    "You are an orchestrator turning an FL Studio project into a printed score. For each FL Studio channel "
    "you choose the closest MuseScore instrument from a fixed list, and a part name. Answer in JSON."
)


def suggest_mapping(cfg: dict, channels: list[dict], favorites: list[str]) -> list[dict]:
    """channels: [{channel, name, notes, low, high}] -> [{channel, instrument, part, include}]."""
    cat = catalogue.all_instruments()
    fav = [f"{i}: {cat[i].name}" for i in favorites if i in cat]
    listing = "\n".join(f"{i.id}: {i.name} ({i.group})" for i in cat.values())
    chans = "\n".join(f"- channel {c['channel']}: \"{c['name']}\", {c['notes']} notes, range {c['low']}–{c['high']}"
                      for c in channels)
    prompt = f"""FL Studio channels (name, note count, pitch range):
{chans}

The user's favourite instruments (prefer these when they fit):
{chr(10).join(fav) or "(none)"}

All MuseScore instruments, as "id: name (group)":
{listing}

For every channel return an object {{"channel": number, "instrument": an id from the list, "part": a short part
name, "include": false only for channels that are clearly not music to notate (e.g. FX, risers, noise)}}.
Drum and percussion channels should all use the same drum-kit id and the part name "Drums" so they share a staff.
Return {{"channels": [...]}}."""
    d = ai.ask_json(cfg, MAP_SYSTEM, prompt)
    rows = d.get("channels", d) if isinstance(d, dict) else d
    out = []
    for r in rows if isinstance(rows, list) else []:
        try:
            ch = int(r["channel"])
        except (KeyError, TypeError, ValueError):
            continue
        ins = str(r.get("instrument", ""))
        out.append({"channel": ch, "instrument": ins if ins in cat else None,
                    "part": str(r.get("part") or "").strip()[:60] or None, "include": r.get("include", True) is not False})
    if not out:
        raise ai.AIError("The AI didn't return any channel suggestions.")
    return out
