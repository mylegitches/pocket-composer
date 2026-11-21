"""Read guitar/bass tab from photos or scans with the configured AI endpoint (musescore-maker v1 used Claude
only for this). The model returns the tab as JSON; to_songsterr_part maps it onto the Songsterr part format
that songsterr_song.build_song turns into a Guitar Pro song."""

from __future__ import annotations

import io

from PIL import Image, ImageOps

from flp2score import ai

MAX_SIDE = 2000  # keeps each image well inside providers' size limits

FORMAT = """Return one JSON object:
{
  "title": string, "artist": string,
  "instrument": string,            // e.g. "Electric Bass (finger)", "Electric Guitar"
  "tempo": integer,                // quarter-note BPM
  "tuning": [integer, ...],        // MIDI pitch of each open string, highest string first
  "measures": [{
    "number": integer,
    "signature": [beats, beatType],
    "repeatStart": boolean,
    "repeatTimes": integer,        // total plays if this bar ends a repeat, else 0
    "ending": integer,             // volta/ending number this bar is under, else 0
    "marker": string,              // rehearsal/section text, or ""
    "beats": [{
      "type": 1|2|4|8|16|32|64,    // note value: 1 whole, 2 half, 4 quarter, 8 eighth, 16 sixteenth
      "dots": integer, "tuplet": integer,   // tuplet: 3 for triplets, 0 if none
      "rest": boolean,
      "notes": [{"string": integer,  // 0 = highest-pitched string (top tab line)
                 "fret": integer, "tie": boolean, "dead": boolean, "staccato": boolean,
                 "accent": boolean, "slide": boolean, "hammer": boolean}]
    }]
  }]
}"""

PROMPT = """These images are pages of a tab (guitar or bass), in order. Transcribe every measure.

- Read fret numbers from the tab lines. String 0 is the top line (highest pitch).
- Take each note's rhythm from the stems/beams/flags under the tab (or the standard notation if present).
  Rests count as beats with rest=true and no notes. Every measure's beats must add up to its time signature.
- Multi-measure rests: emit one whole-measure rest measure per bar they cover, so bar numbers stay aligned.
- Tied/slurred continuation notes (a tie arc into a note) get tie=true. "x" = dead note.
- Tempo: convert to quarter-note BPM (e.g. half note = 120 in cut time -> 240).
- Tuning: standard unless marked. 4-string bass = [43, 38, 33, 28]; 6-string guitar = [64, 59, 55, 50, 45, 40].
- Cut time is [2, 2]; common time (C) is [4, 4]. Put the signature on every measure.
Number measures as printed. Don't skip or summarise repeated bars; write each one out.

""" + FORMAT

SYSTEM = "You transcribe guitar and bass tablature from images into exact JSON. Output only JSON."


def _prepare(data: bytes) -> bytes:
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    except Exception as e:  # Pillow raises several types for bad input
        raise ai.AIError(f"Can't open image ({e}). Use JPEG, PNG or WebP.")
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def read_tab(cfg: dict, images: list[bytes]) -> dict:
    """The transcription as a dict in the FORMAT above."""
    tab = ai.ask_json(cfg, SYSTEM, PROMPT, [_prepare(d) for d in images], max_tokens=64000)
    if not isinstance(tab, dict) or not tab.get("measures"):
        raise ai.AIError("The AI didn't return any measures. Try a model that can read images, or fewer pages.")
    return tab


def to_songsterr_part(tab: dict) -> dict:
    """Map the transcription onto the Songsterr part format that songsterr_song.build_song reads."""
    measures = []
    for m in tab.get("measures", []):
        beats = []
        for b in m.get("beats", []):
            notes = [] if b.get("rest") else [
                {"string": n["string"], "fret": n["fret"], "tie": n.get("tie"), "dead": n.get("dead"),
                 "staccato": n.get("staccato"), "accentuated": n.get("accent"),
                 "slide": "legato" if n.get("slide") else None, "hp": n.get("hammer")}
                for n in b.get("notes", []) if "string" in n and "fret" in n]
            beats.append({"type": b.get("type", 4), "dots": b.get("dots", 0), "tuplet": b.get("tuplet") or None,
                          "rest": bool(b.get("rest")) or not notes, "notes": notes})
        sig = m.get("signature") or []
        measures.append({"signature": sig if len(sig) == 2 else None, "repeatStart": m.get("repeatStart"),
                         "repeatClose": m.get("repeatTimes") or None,
                         "alternateEnding": [m["ending"]] if m.get("ending") else None,
                         "marker": {"text": m["marker"]} if m.get("marker") else None,
                         "voices": [{"beats": beats}]})
    instrument = tab.get("instrument") or "Electric Bass (finger)"
    tuning = tab.get("tuning") or [43, 38, 33, 28]
    return {"name": instrument, "instrument": instrument,
            "instrumentId": 33 if "bass" in instrument.lower() else 27,
            "tuning": tuning, "strings": len(tuning),
            "automations": {"tempo": [{"measure": 0, "position": 0, "bpm": tab.get("tempo") or 120}]},
            "measures": measures}
