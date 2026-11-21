"""Find multi-measure rests (the thick bar with a number above it) that the OMR read as one bar.

Big-band parts are full of them, and if they are read as a single measure the part ends up
shorter than the others and the full score no longer lines up. We look for the thick
horizontal bar on the middle line of each staff homr found, OCR the number above it and mark
the matching whole-bar rest in the OMR output.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np


def _staff_boxes(staff_file: Path, shape: tuple[int, int]) -> list[tuple[float, float, float, float]]:
    h, w = shape
    boxes = []
    for line in staff_file.read_text().splitlines():
        parts = line.split()
        if len(parts) != 5:
            continue
        _, cx, cy, bw, bh = map(float, parts)
        boxes.append((cx * w - bw * w / 2, cy * h - bh * h / 2, cx * w + bw * w / 2, cy * h + bh * h / 2))
    return boxes


def _read_number(crop: np.ndarray, ocr) -> str:
    """OCR a lone number. The text detector misses isolated digits, so crop to the ink, pad it
    and run recognition only."""
    ink = crop < 140
    ys, xs = np.where(ink)
    if len(ys) < 10:
        return ""
    # Keep the ink cluster nearest the staff (bottom of the crop): the number sits just above it.
    rows = np.where(ink.any(axis=1))[0]
    bottom = rows[-1]
    gap_rows = [r for r in range(bottom, -1, -1) if not ink[r].any()]
    top = gap_rows[0] + 1 if gap_rows else 0
    sub = crop[top:bottom + 1]
    cols = np.where((sub < 140).any(axis=0))[0]
    sub = sub[:, cols[0]:cols[-1] + 1]
    pad = max(8, sub.shape[0] // 2)
    sub = cv2.copyMakeBorder(sub, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)
    if sub.shape[0] < 48:
        f = 48 / sub.shape[0]
        sub = cv2.resize(sub, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    result, _ = ocr(np.ascontiguousarray(cv2.cvtColor(sub, cv2.COLOR_GRAY2RGB)), use_det=False, use_cls=False)
    return "".join(str(r[0]) for r in (result or []))


def detect(page_png: Path, staff_file: Path, ocr) -> list[tuple[int, float, int]]:
    """Returns (staff index, relative x position on the staff, number of bars)."""
    if not staff_file.exists():
        return []
    img = cv2.imread(str(page_png), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return []
    found = []
    for si, (x1, y1, x2, y2) in enumerate(_staff_boxes(staff_file, img.shape)):
        sp = (y2 - y1) / 4
        if sp < 4:
            continue
        cy = (y1 + y2) / 2
        top, bottom = int(max(0, cy - 1.5 * sp)), int(min(img.shape[0], cy + 1.5 * sp))
        left, right = int(max(0, x1)), int(min(img.shape[1], x2))
        band = (img[top:bottom, left:right] < 140).astype(np.uint8)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, int(sp * 2.2)), max(2, int(sp * 0.55))))
        thick = cv2.morphologyEx(band, cv2.MORPH_OPEN, kernel)
        n, _, stats, _ = cv2.connectedComponentsWithStats(thick)
        for i in range(1, n):
            bx, by, bw, bh, area = stats[i]
            if not (0.55 * sp <= bh <= 1.7 * sp and bw >= 2.2 * sp and bw <= 25 * sp and area / (bw * bh) > 0.8):
                continue
            # homr's staff box includes any tilt of the scan, so allow some slack around the middle.
            if abs(top + by + bh / 2 - cy) > 0.9 * sp:
                continue
            # The count sits above the staff, roughly centred on the bar.
            ax1 = int(max(0, left + bx - 1.5 * sp))
            ax2 = int(min(img.shape[1], left + bx + bw + 1.5 * sp))
            # Find the real top staff line above the bar (the staff box is inflated by tilt).
            col = img[int(max(0, y1 - sp)):int(cy), ax1:ax2] < 140
            line_rows = np.where(col.mean(axis=1) > 0.7)[0]
            staff_top = int(max(0, y1 - sp)) + int(line_rows[0]) if len(line_rows) else int(y1)
            ay1 = int(max(0, staff_top - 4 * sp))
            ay2 = int(max(0, staff_top - 3))
            if ay2 - ay1 < sp:
                continue
            text = _read_number(img[ay1:ay2, ax1:ax2], ocr)
            text = text.replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1").replace("S", "5")
            m = re.fullmatch(r"\D*(\d{1,3})\D*", text)
            if not m:
                continue
            count = int(m.group(1))
            if 2 <= count <= 200:
                found.append((si, (left + bx + bw / 2 - x1) / max(1.0, x2 - x1), count))
    return found


def _is_rest_measure(m: ET.Element) -> bool:
    notes = m.findall("note")
    return bool(notes) and all(n.find("rest") is not None for n in notes)


def apply(measures: list[ET.Element], hits: list[tuple[int, float, int]], log=print) -> None:
    """Mark the whole-bar rests in the page's OMR measures as multi-measure rests (in place)."""
    if not hits:
        return
    systems: list[list[ET.Element]] = []
    for m in measures:
        if not systems or m.find("print[@new-system='yes']") is not None:
            systems.append([])
        systems[-1].append(m)
    staff_count = max(h[0] for h in hits) + 1
    if len(systems) < staff_count:
        log(f"    multi-rests: staff count mismatch ({len(systems)} systems vs staff {staff_count}); skipped")
        return
    for si, rel_x, count in hits:
        system = systems[si]
        candidates = [i for i, m in enumerate(system) if _is_rest_measure(m) and m.find("attributes/measure-style") is None]
        if not candidates:
            continue
        best = min(candidates, key=lambda i: abs((i + 0.5) / len(system) - rel_x))
        m = system[best]
        attrs = m.find("attributes")
        if attrs is None:
            attrs = ET.Element("attributes")
            idx = next((k for k, c in enumerate(m) if c.tag == "note"), 0)
            m.insert(idx, attrs)
        style = ET.SubElement(attrs, "measure-style")
        ET.SubElement(style, "multiple-rest").text = str(count)
        log(f"    multi-rest: {count} bars (staff {si + 1})")
