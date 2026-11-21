"""Work out which pages belong to which instrument part.

Band charts are usually a stack of individual parts: each part starts with a title page and
every page carries the instrument name in its header. We OCR the page header to read that
name, and the left margin to spot full-score pages (one instrument label per staff).

The result is written to parts.json, which the user can edit and re-run with.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from . import instruments

HEADER_FRACTION = 0.18
MARGIN_FRACTION = 0.13
SCORE_PAGE_MIN_LABELS = 4


@dataclass
class Part:
    name: str
    pages: list[int]  # 1-based page numbers
    clef: str | None = None  # optional override, e.g. "G2", "F4"
    fifths: int | None = None  # optional key signature override (-7..7)
    skip: bool = False


@dataclass
class Plan:
    title: str
    parts: list[Part] = field(default_factory=list)
    score_pages: list[int] = field(default_factory=list)
    notes: str = (
        "Edit this file to fix part names/page ranges, set 'clef' (G2/F4/C3/percussion) or "
        "'fifths' (key signature: -2 = two flats) per part, or set 'skip': true. Then re-run."
    )

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Plan":
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            title=d["title"],
            parts=[Part(**p) for p in d["parts"]],
            score_pages=d.get("score_pages", []),
        )


_ocr = None


def get_ocr():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR

        _ocr = RapidOCR()
    return _ocr


def _ocr_lines(im: np.ndarray) -> list[tuple[str, float]]:
    """(text, text height in px) for each line found."""
    result, _ = get_ocr()(np.ascontiguousarray(im))
    return [(r[1], float(max(p[1] for p in r[0]) - min(p[1] for p in r[0]))) for r in (result or [])]


def _read_page(path: Path) -> tuple[list[tuple[str, float]], list[str]]:
    im = Image.open(path).convert("RGB")
    im = im.resize((im.width // 2, im.height // 2))  # 150 dpi is plenty for header text
    a = np.asarray(im)
    h, w = a.shape[:2]
    header = _ocr_lines(a[: int(h * HEADER_FRACTION)])
    margin = [t for t, _ in _ocr_lines(a[:, : int(w * MARGIN_FRACTION)])]
    return header, margin


def _pretty_title(token: str, fallback: str) -> str:
    if not token:
        return fallback
    return token.title() if token.isupper() else token


def detect(pages: list[Path], fallback_title: str, log=print) -> Plan:
    headers, heights, page_inst, score_pages = [], [], [], []
    for i, p in enumerate(pages, 1):
        header_lines, margin = _read_page(p)
        header = [t for t, _ in header_lines]
        headers.append(header)
        heights.append(dict(header_lines))
        labels = {instruments.match(t) for t in margin} - {None}
        if len({(m[0].key, m[1]) for m in labels}) >= SCORE_PAGE_MIN_LABELS:
            score_pages.append(i)
            page_inst.append(None)
            log(f"  page {i:3d}: full score page (skipped)")
            continue
        found = next((instruments.match(t) for t in header if instruments.match(t)), None)
        page_inst.append(found)
        log(f"  page {i:3d}: {instruments.display_name(*found) if found else '-'}")

    # Song title: header text that repeats on many pages, is not an instrument, and is printed
    # biggest (composer lines and subtitles repeat just as often but in smaller type).
    counts: Counter[str] = Counter()
    sizes: dict[str, list[float]] = {}
    original: dict[str, str] = {}
    for h, hh in zip(headers, heights):
        seen: set[str] = set()
        for t in h:
            n = instruments.normalise(t)
            if len(n) >= 4 and re.search(r"[A-Z]{3}", n) and not instruments.match(t) and n not in seen:
                seen.add(n)
                counts[n] += 1
                sizes.setdefault(n, []).append(hh.get(t, 0.0))
                original.setdefault(n, t)
    min_count = max(2, len(pages) // 8)
    top = max(counts.values(), default=0)
    candidates = [k for k in counts if counts[k] >= max(min_count, top / 2)]
    title_key = max(candidates, key=lambda k: (np.median(sizes[k]), counts[k])) if candidates else ""
    title = _pretty_title(original.get(title_key, ""), fallback_title)

    def is_title_page(i: int) -> bool:
        return bool(title_key) and any(instruments.normalise(t) == title_key for t in headers[i])

    # Group pages into parts.
    parts: list[Part] = []
    current = None
    for idx in range(len(pages)):
        page_no = idx + 1
        if page_no in score_pages:
            current = None
            continue
        inst = page_inst[idx]
        if inst is None and is_title_page(idx):
            # Title page whose instrument label we missed: borrow it from the next page.
            inst = next((page_inst[j] for j in range(idx + 1, min(idx + 3, len(pages))) if page_inst[j]), None)
        starts_new = (
            current is None
            or (is_title_page(idx) and current.pages)
            or (inst is not None and instruments.display_name(*inst) != _base_name(current.name))
        )
        if starts_new:
            name = instruments.display_name(*inst) if inst else f"Part {len(parts) + 1}"
            current = Part(name=name, pages=[])
            parts.append(current)
        current.pages.append(page_no)

    # Make duplicate names unique ("Alto Sax 1", "Alto Sax 1 (2)").
    seen: Counter[str] = Counter()
    for part in parts:
        seen[part.name] += 1
        if seen[part.name] > 1:
            part.name = f"{part.name} ({seen[part.name]})"

    return Plan(title=title, parts=parts, score_pages=score_pages)


def _base_name(name: str) -> str:
    return re.sub(r"\s*\(\d+\)$", "", name)
