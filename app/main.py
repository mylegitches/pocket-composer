"""Web UI for flp2score: turn FL Studio projects, sheet-music scans, MusicXML and MIDI into MuseScore scores.

    uvicorn app.main:app --reload            # local
    docker compose up                        # container (see Dockerfile)

Project kinds (decided by what was uploaded into data/<name>/):
  flp     FL Studio project: map channels to instruments, then convert (fast, synchronous)
  scan    PDF / photos of sheet music: find parts (AI endpoint or OCR), then OMR (slow, background job)
  score   MusicXML / .mxl / MIDI: converted as-is by MuseScore
  tab     saved Songsterr tab page (.html) or Songsterr part .json: tracks -> Guitar Pro -> MuseScore

Scan projects can also be read as guitar/bass tab (by the AI endpoint) instead of notation.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import traceback
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from flp2score import ai, aitest, arrange, assist, catalogue, export, flp, mapping, preview, sheet
from flp2score.convert import FORMATS, convert, export_file

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data")).resolve()
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", ROOT / "output")).resolve()
STATIC = Path(__file__).with_name("static")
NOTE_NAMES = "C C# D D# E F F# G G# A A# B".split()
SETTINGS_FILE = DATA_DIR / "settings.json"
PREVIEW_DIR = OUTPUT_DIR / "_previews"
# Starting favourites, until the user picks their own on the settings page.
DEFAULT_FAVORITES = ["piano", "strings", "violoncello", "contrabass", "electric-bass", "bass-synthesizer",
                     "saw-synth", "pad-synth", "drumset"]

SCAN_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".heic", ".heif"}
SCORE_EXTS = {".musicxml", ".mxl", ".xml", ".mid", ".midi"}
TAB_EXTS = {".html", ".htm", ".json"}
UPLOAD_EXTS = {".flp"} | SCAN_EXTS | SCORE_EXTS | TAB_EXTS
META_FILES = {"parts.json", "mode.json", "mapping.json", "tracks.json"}  # our own files in a project folder

app = FastAPI(title="Pocket Composer")
_preview_lock = threading.Lock()  # one instrument sample render at a time


@app.middleware("http")
async def revalidate_ui(request, call_next):
    """Browsers (phones especially) must re-check the UI files, or an update keeps running old JS."""
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# ---------------------------------------------------------------- helpers

def _note(k: int) -> str:
    return f"{NOTE_NAMES[k % 12]}{k // 12 - 1}"


def _safe_name(name: str) -> str:
    clean = re.sub(r"[^\w\- .()]+", "_", name).strip(" .")
    if not clean:
        raise HTTPException(400, "bad name")
    return clean


def _project_dir(name: str) -> Path:
    d = (DATA_DIR / name).resolve()
    if d.parent != DATA_DIR or not d.is_dir():
        raise HTTPException(404, f"no project {name!r}")
    return d


def _sources(d: Path) -> tuple[str, list[Path]]:
    """The project's kind and its input files."""
    files = sorted(p for p in d.iterdir() if p.is_file() and p.name not in META_FILES)
    if flps := [f for f in files if f.suffix.lower() == ".flp"]:
        return "flp", flps[:1]
    if scores := [f for f in files if f.suffix.lower() in SCORE_EXTS]:
        return "score", scores[:1]
    if tabs := [f for f in files if f.suffix.lower() in TAB_EXTS]:
        return "tab", tabs[:1]
    if scans := [f for f in files if f.suffix.lower() in SCAN_EXTS]:
        return "scan", scans
    return "", []


def _work(name: str) -> Path:
    return OUTPUT_DIR / name / "_work"


def _outputs(name: str, stem: str) -> list[dict]:
    out = OUTPUT_DIR / name
    if not out.is_dir():
        return []
    files = []

    def add(f: Path, url_path: str, part: bool = False):
        files.append({"file": f.name, "kind": f.suffix.lstrip("."), "size": f.stat().st_size, "part": part,
                      "url": f"/api/projects/{name}/files/{url_path}", "mtime": f.stat().st_mtime})

    for f in sorted(out.iterdir()):
        if f.is_file() and (f.name.startswith(stem + ".") or f.name.startswith(stem + "-")):
            add(f, f.name)
    if (out / "parts").is_dir():
        for f in sorted((out / "parts").iterdir()):
            if f.is_file():
                add(f, "parts/" + f.name, part=True)
    page_no = lambda f: int(m.group(1)) if (m := re.search(r"-(\d+)\.svg$", f["file"])) else 0  # noqa: E731
    svgs = sorted((f for f in files if f["kind"] == "svg"), key=page_no)
    return [f for f in files if f["kind"] != "svg"] + svgs


def _timeline(name: str, stem: str) -> dict | None:
    """Where each note sits on the score pages and when it plays, from MuseScore's .spos export.

    Coordinates are converted to the SVG pages' own units (MuseScore writes .spos at 12x)."""
    out = OUTPUT_DIR / name
    spos, page = out / f"{stem}.spos", out / f"{stem}-1.svg"
    if not spos.is_file() or not page.is_file():
        return None
    try:
        root = ET.parse(spos).getroot()
        head = page.read_text(encoding="utf-8", errors="ignore")[:1000]
        w, h = map(float, re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', head).groups())
    except (OSError, ValueError, AttributeError, ET.ParseError):
        return None
    els = {e.get("id"): e for e in root.iter("element")}
    events = []
    for ev in root.iter("event"):
        e = els.get(ev.get("elid"))
        if e is None:
            continue
        events.append([int(ev.get("position")), int(e.get("page")), round(float(e.get("x")) / 12),
                       round(float(e.get("y")) / 12), round(float(e.get("sx")) / 12), round(float(e.get("sy")) / 12)])
    events.sort()
    return {"page_w": w, "page_h": h, "events": events}  # event: [ms, page, x, y, width, height]


def _stem(name: str, kind: str, files: list[Path]) -> str:
    return files[0].stem if kind == "flp" else name


def _summary(name: str) -> dict:
    d = _project_dir(name)
    kind, files = _sources(d)
    if not kind:
        raise HTTPException(404, "project folder has no usable input file")
    stem = _stem(name, kind, files)
    base = {"name": name, "kind": kind, "file": files[0].name, "files": [f.name for f in files],
            "outputs": _outputs(name, stem), "timeline": _timeline(name, stem),
            "job": _active_job(name) or _last_job(name),
            "ai": _ai_public()}
    if kind == "flp":
        base.update(_flp_summary(d, files[0]))
    elif kind == "scan":
        base.update(_scan_summary(name, d))
    elif kind == "tab":
        base.update(_tab_summary(d, files[0]))
    else:
        base["title"] = name
    return base


def _flp_summary(d: Path, f: Path) -> dict:
    proj = flp.read(f)
    chans = arrange.flatten(proj)
    m = mapping.merged(mapping.load(d), proj, chans)
    pattern_names: dict[int, set[str]] = {}
    for pat in proj.patterns.values():
        for n in pat.notes:
            pattern_names.setdefault(n.channel, set()).add(pat.name or f"Pattern {pat.id}")
    channels = []
    for ch, notes in chans.items():
        keys = [n.key for n in notes]
        channels.append({
            "channel": ch,
            "name": proj.channels.get(ch) or f"Channel {ch}",
            "notes": len(notes),
            "low": _note(min(keys)), "high": _note(max(keys)),
            "distinct_keys": len(set(keys)),
            "patterns": sorted(pattern_names.get(ch, [])),
        })
    unused = [proj.channels[c] for c in proj.channels if c not in chans and proj.channels[c]]
    bars = arrange.song_length(proj) / proj.ppq / (proj.time_sig[0] * 4 / proj.time_sig[1])
    return {
        "fl_version": proj.version,
        "tempo": proj.tempo,
        "time_sig": f"{proj.time_sig[0]}/{proj.time_sig[1]}",
        "bars": round(bars, 1),
        "audio_clips": sum(1 for c in proj.clips if c.pattern is None),
        "channels": channels,
        "silent_channels": unused,
        "mapping": m.to_json(),
        "mapping_saved": (d / mapping.FILE_NAME).exists(),
    }


def _scan_summary(name: str, d: Path) -> dict:
    pages_dir = _work(name) / "pages"
    n_pages = len(list(pages_dir.glob("p[0-9][0-9][0-9].png"))) if pages_dir.is_dir() else 0
    plan = sheet.load_plan(d)
    return {
        "read_as": sheet.read_as(d),
        "pages": n_pages,
        "title": plan.title if plan else name,
        "plan": None if plan is None else {
            "title": plan.title, "score_pages": plan.score_pages,
            "parts": [{"name": p.name, "pages": p.pages, "clef": p.clef, "fifths": p.fifths, "skip": p.skip}
                      for p in plan.parts]},
    }


def _read_tab_source(f: Path) -> dict:
    """What a Songsterr page / part JSON holds: {"kind": "songsterr" | "songsterr-json", meta...}."""
    from tabmaker import musescore_page, songsterr

    data = f.read_bytes()
    if f.suffix.lower() == ".json":
        part = json.loads(data)
        if not isinstance(part, dict) or "measures" not in part:
            raise ValueError("this .json isn't a Songsterr part (no measures)")
        name = part.get("name") or part.get("instrument") or "Track 1"
        return {"kind": "songsterr-json", "title": f.stem, "artist": "", "defaultPartId": 0,
                "tracks": [{"partId": 0, "title": name, "instrument": part.get("instrument", ""), "isDrums": False}]}
    text = data.decode("utf-8", errors="replace")
    if musescore_page.is_musescore_page(text):
        info = musescore_page.parse_page(text)
        raise ValueError(info["message"] + (f" Download link: {info['mxlUrl']}" if info.get("mxlUrl") else ""))
    if '<script id="state"' not in text:
        raise ValueError("not a saved Songsterr tab page (save the tab page itself with Ctrl+S, as HTML only)")
    return {"kind": "songsterr", **songsterr.parse_page(text)}


def _tab_tracks(d: Path, meta: dict) -> list:
    try:
        return json.loads((d / "tracks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [meta["defaultPartId"]]


def _tab_summary(d: Path, f: Path) -> dict:
    meta = _read_tab_source(f)
    chosen = set(_tab_tracks(d, meta))
    return {"title": meta.get("title") or d.name, "artist": meta.get("artist", ""), "source": meta["kind"],
            "tracks": [{**t, "selected": t["partId"] in chosen} for t in meta["tracks"]]}


# ---------------------------------------------------------------- background jobs
#
# Every conversion and part search is a job. Jobs run one at a time, in order, on a worker thread.
# Each is saved to output/_jobs/<id>.json as it goes, so the browser can close and the server can
# restart: unfinished jobs are picked up again on startup (OMR pages already read stay cached).
# What a job needs (mapping, part plan, chosen tracks) is saved in the project before it is queued.

import queue

JOBS_DIR = OUTPUT_DIR / "_jobs"
JOBS: dict[str, dict] = {}
_queue: queue.Queue[str] = queue.Queue()
_jobs_io = threading.Lock()
KEEP_JOBS = 60
JOB_FIELDS = ("id", "project", "kind", "params", "state", "progress", "log", "error", "result", "started",
              "finished")


def _save_job(j: dict, force: bool = True) -> None:
    now = time.time()
    if not force and now - j.get("_saved", 0) < 1.0:  # progress/log updates: at most once a second
        return
    j["_saved"] = now
    with _jobs_io:
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = JOBS_DIR / f"{j['id']}.tmp"
        tmp.write_text(json.dumps({k: j.get(k) for k in JOB_FIELDS}), encoding="utf-8")
        tmp.replace(JOBS_DIR / f"{j['id']}.json")


def _job_public(j: dict) -> dict:
    return {k: j.get(k) for k in JOB_FIELDS}


def _active_job(name: str) -> dict | None:
    for j in JOBS.values():
        if j["project"] == name and j["state"] in ("queued", "running"):
            return _job_public(j)
    return None


def _last_job(name: str) -> dict | None:
    jobs = [j for j in JOBS.values() if j["project"] == name]
    return _job_public(max(jobs, key=lambda j: j["started"])) if jobs else None


def _queue_job(project: str, kind: str, params: dict) -> dict:
    if _active_job(project):
        raise HTTPException(409, "This piece is already working on something.")
    j = {"id": uuid.uuid4().hex[:12], "project": project, "kind": kind, "params": params, "state": "queued",
         "progress": 0.0, "log": [], "error": None, "result": None, "started": time.time(), "finished": None}
    ahead = sum(1 for x in JOBS.values() if x["state"] in ("queued", "running"))
    if ahead:
        j["log"].append(f"Waiting for {ahead} other job(s) to finish…")
    JOBS[j["id"]] = j
    _save_job(j)
    _prune_jobs()
    _queue.put(j["id"])
    return _job_public(j)


def _prune_jobs() -> None:
    done = sorted((j for j in JOBS.values() if j["state"] not in ("queued", "running")), key=lambda j: j["started"])
    for old in done[:-KEEP_JOBS]:
        JOBS.pop(old["id"], None)
        (JOBS_DIR / f"{old['id']}.json").unlink(missing_ok=True)


def _worker() -> None:
    while True:
        j = JOBS.get(_queue.get())
        if not j or j["state"] != "queued":
            continue

        def log(msg: str, j=j):
            msg = msg.strip()
            if msg:
                j["log"].append(msg)
                del j["log"][:-200]
                _save_job(j, force=False)

        def progress(f: float, j=j):
            j["progress"] = round(f, 3)
            _save_job(j, force=False)

        j["state"] = "running"
        _save_job(j)
        try:
            j["result"] = _run_job(j, log, progress)
            j["state"], j["progress"] = "done", 1.0
        except Exception as e:  # report anything to the UI rather than dying silently
            msg = e.detail if isinstance(e, HTTPException) else (str(e) or e.__class__.__name__)
            j["state"], j["error"] = "failed", msg
            log("Failed: " + msg)
            traceback.print_exc()
        j["finished"] = time.time()
        _save_job(j)


@app.on_event("startup")
def _load_jobs() -> None:
    """Reload saved jobs; anything that was queued or running when the server stopped goes back in the queue."""
    if JOBS_DIR.is_dir():
        for f in JOBS_DIR.glob("*.json"):
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not (DATA_DIR / j.get("project", "")).is_dir():
                f.unlink(missing_ok=True)  # the piece was deleted
                continue
            JOBS[j["id"]] = j
        for tmp in JOBS_DIR.glob("*.tmp"):
            tmp.unlink(missing_ok=True)
    resume = sorted((j for j in JOBS.values() if j["state"] in ("queued", "running")), key=lambda j: j["started"])
    for j in resume:
        j["state"] = "queued"
        j["log"].append("The server restarted: picking this up again.")
        _save_job(j)
        _queue.put(j["id"])
    threading.Thread(target=_worker, daemon=True, name="jobs").start()


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    return [_job_public(j) for j in sorted(JOBS.values(), key=lambda j: -j["started"])]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "no such job")
    return _job_public(j)


@app.delete("/api/jobs/{job_id}")
def cancel_job(job_id: str) -> dict:
    """Take a job out of the queue before it starts (a running job finishes)."""
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "no such job")
    if j["state"] != "queued":
        raise HTTPException(409, "only waiting jobs can be cancelled")
    j["state"], j["error"], j["finished"] = "failed", "Cancelled", time.time()
    j["log"].append("Cancelled.")
    _save_job(j)
    return _job_public(j)


# ---------------------------------------------------------------- settings (favourites + AI endpoint)

def _settings() -> dict:
    try:
        s = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    s.setdefault("favorites", DEFAULT_FAVORITES)
    s.setdefault("ai", {})
    s["ai"].setdefault("use_for_parts", True)
    return s


def _write_settings(s: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(s, indent=1), encoding="utf-8")


def _ai_cfg() -> dict:
    return _settings()["ai"]


def _ai_public() -> dict:
    """The AI settings without the key itself (only whether one is saved, and its last 4 characters)."""
    a = dict(_ai_cfg())
    key = a.pop("api_key", "") or ""
    a["has_key"] = bool(key)
    a["key_hint"] = ("…" + key[-4:]) if len(key) > 8 else ("saved" if key else "")
    a["configured"] = ai.configured(_ai_cfg())
    a.setdefault("models", {})
    a["task_models"] = {t: ai.for_task(_ai_cfg(), t)["model"] for t in ai.TASKS}
    return a


class AIIn(BaseModel):
    preset: str = ""
    provider: str = ""
    base_url: str = ""
    model: str = ""
    api_key: str | None = None  # None = keep the saved key
    clear_key: bool = False
    use_for_parts: bool = True
    models: dict[str, str] = {}  # per-task model overrides ("" = main model)


class SettingsIn(BaseModel):
    favorites: list[str] | None = None
    ai: AIIn | None = None


@app.get("/api/settings")
def get_settings() -> dict:
    s = _settings()
    return {"favorites": s["favorites"], "ai": _ai_public(), "ai_presets": ai.PRESETS, "ai_tasks": ai.TASKS}


@app.put("/api/settings")
def put_settings(body: SettingsIn) -> dict:
    s = _settings()
    if body.favorites is not None:
        known = catalogue.all_instruments()
        s["favorites"] = list(dict.fromkeys(i for i in body.favorites if i in known))
    if body.ai is not None:
        _merge_ai(s["ai"], body.ai)
    _write_settings(s)
    return get_settings()


def _merge_ai(cur: dict, new: AIIn) -> dict:
    if new.provider and new.provider not in ai.PROVIDERS:
        raise HTTPException(400, f"unknown provider {new.provider!r}")
    cur.update(preset=new.preset, provider=new.provider, base_url=new.base_url.strip().rstrip("/"),
               model=new.model.strip(), use_for_parts=new.use_for_parts,
               models={t: (new.models.get(t) or "").strip() for t in ai.TASKS})
    if new.clear_key:
        cur.pop("api_key", None)
    elif new.api_key:
        cur["api_key"] = new.api_key.strip()
    return cur


@app.post("/api/ai/test")
def ai_test(body: AIIn) -> dict:
    """Try the settings in the form (without saving them): one tiny text request."""
    cfg = _merge_ai(dict(_ai_cfg()), body)
    t0 = time.time()
    try:
        reply = ai.ask(cfg, "You are a connection test.", "Reply with the single word OK.")
    except ai.AIError as e:
        raise HTTPException(502, str(e)) from e
    return {"ok": True, "reply": reply.strip()[:200], "seconds": round(time.time() - t0, 1)}


class AITaskTestIn(AIIn):
    task: str


@app.post("/api/ai/test_task")
def ai_test_task(body: AITaskTestIn) -> dict:
    """Run one job on a sample page with a known answer, with the settings in the form, and score it."""
    if body.task not in ai.TASKS:
        raise HTTPException(400, "unknown task")
    cfg = _merge_ai(dict(_ai_cfg()), body)
    if not ai.configured(cfg):
        raise HTTPException(400, "Fill in the service, model and API key first.")
    try:
        return aitest.run(cfg, body.task, OUTPUT_DIR / "_aitest")
    except ai.AIError as e:
        raise HTTPException(502, str(e)) from e


@app.post("/api/ai/models")
def ai_models(body: AIIn) -> dict:
    cfg = _merge_ai(dict(_ai_cfg()), body)
    try:
        return {"models": ai.list_models(cfg)}
    except ai.AIError as e:
        raise HTTPException(502, str(e)) from e


# ---------------------------------------------------------------- instruments

@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "musescore": export.find_musescore()}


@app.get("/api/instruments")
def list_instruments() -> list[dict]:
    out = []
    for ins in catalogue.all_instruments().values():
        entry = {"id": ins.id, "name": ins.name, "group": ins.group, "staves": ins.staves,
                 "common": "common" in ins.genres, "transpose": ins.transpose[1], "description": ins.description}
        if ins.drums:
            entry["drums"] = [{"pitch": p, "name": d["name"]} for p, d in sorted(ins.drums.items())]
        out.append(entry)
    return out


@app.get("/api/instruments/{ins_id}/preview")
def instrument_preview(ins_id: str):
    """An .mp3 of the instrument playing a short phrase, rendered by MuseScore once and then cached."""
    if ins_id not in catalogue.all_instruments():
        raise HTTPException(404, "no such instrument")
    f = PREVIEW_DIR / f"{ins_id}.mp3"
    if not f.exists():
        if not export.find_musescore():
            raise HTTPException(500, "MuseScore is not installed in this environment")
        with _preview_lock:
            if not f.exists():
                try:
                    preview.render(ins_id, f)
                except Exception as e:
                    raise HTTPException(500, str(e)) from e
    return FileResponse(f, media_type="audio/mpeg", headers={"Cache-Control": "max-age=86400"})


# ---------------------------------------------------------------- projects

@app.get("/api/projects")
def list_projects() -> list[dict]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for d in sorted(DATA_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not d.is_dir():
            continue
        kind, files = _sources(d)
        if not kind:
            continue
        outs = _outputs(d.name, _stem(d.name, kind, files))
        last = _last_job(d.name)
        out.append({"name": d.name, "kind": kind, "file": files[0].name,
                    "converted": any(o["kind"] in ("mscz", "pdf", "mp3") and not o["part"] for o in outs),
                    "busy": _active_job(d.name) is not None,
                    "job": {k: last[k] for k in ("id", "kind", "state", "progress", "error")} if last else None})
    return out


@app.post("/api/projects")
async def upload_project(files: list[UploadFile] = File(...)) -> dict:
    """One .flp, one MusicXML/MIDI file, or a PDF / several photos (pages, in the order picked)."""
    files = [f for f in files if f.filename]
    if not files:
        raise HTTPException(400, "no file")
    exts = [Path(f.filename).suffix.lower() for f in files]
    if any(e not in UPLOAD_EXTS for e in exts):
        raise HTTPException(400, "upload a PDF or photos of sheet music or tab, MusicXML, MIDI, an FL Studio .flp, "
                                 "or a saved Songsterr page")
    if len(files) > 1 and not all(e in SCAN_EXTS for e in exts):
        raise HTTPException(400, "only PDFs and photos can be uploaded several at a time (as the pages of one piece)")
    stem = _safe_name(Path(files[0].filename).stem)
    d = DATA_DIR / stem
    if d.exists():
        shutil.rmtree(d)  # re-uploading replaces the project's input (its output folder stays)
    d.mkdir(parents=True)
    for i, (f, ext) in enumerate(zip(files, exts), 1):
        fname = f"{stem}{ext}" if len(files) == 1 else f"{i:03d}{ext}"  # numbered: pages stay in picked order
        with (d / fname).open("wb") as fh:
            shutil.copyfileobj(f.file, fh)
    return _check_new_project(d, stem, files[0].filename)


def _check_new_project(d: Path, stem: str, label: str) -> dict:
    """Make sure a freshly saved input is usable; remove it again if not."""
    kind, srcs = _sources(d)
    try:
        if not kind:
            raise ValueError("not a file type this app reads")
        if kind == "flp":
            flp.read(srcs[0])
        elif kind == "tab":
            _read_tab_source(srcs[0])
        elif kind == "scan":
            work = _work(stem)
            if work.exists():
                shutil.rmtree(work)  # new pages: drop cached page images and OMR
            sheet.render_pages(d, work)
    except Exception as e:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(400, f"couldn't use {label}: {e}") from e
    return _summary(stem)


# --- import from a link

URL_MAX_BYTES = 80 * 1024 * 1024
CONTENT_EXTS = {
    "application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
    "image/heic": ".heic", "image/tiff": ".tif", "audio/midi": ".mid", "audio/x-midi": ".mid",
    "application/vnd.recordare.musicxml": ".mxl", "application/vnd.recordare.musicxml+xml": ".musicxml",
    "text/html": ".html", "application/json": ".json", "application/xml": ".xml", "text/xml": ".xml",
}


def _public_host(url: str) -> None:
    """Refuse links to this machine or the local network (the server fetches them, not the browser)."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise HTTPException(400, "paste an http(s) link")
    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(u.hostname, None)}
    except socket.gaierror as e:
        raise HTTPException(400, f"can't find {u.hostname}") from e
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise HTTPException(400, "links to this computer or your local network aren't fetched")


class UrlIn(BaseModel):
    url: str


@app.post("/api/projects/from_url")
def project_from_url(body: UrlIn) -> dict:
    """Make a project from a link: a PDF / image / MusicXML / MIDI / .flp file, or a Songsterr tab page.

    A MuseScore.com score page is answered with its MusicXML download link (MuseScore needs you logged in)."""
    import httpx
    from urllib.parse import unquote, urlparse

    url = body.url.strip()
    _public_host(url)
    headers = {"User-Agent": "Mozilla/5.0 (pocket-composer)"}
    try:
        with httpx.Client(headers=headers, timeout=60, follow_redirects=True, max_redirects=5) as client:
            with client.stream("GET", url) as r:
                _public_host(str(r.url))  # where the redirects ended up
                if r.status_code != 200:
                    raise HTTPException(400, f"the link answered {r.status_code}")
                data = bytearray()
                for chunk in r.iter_bytes():
                    data += chunk
                    if len(data) > URL_MAX_BYTES:
                        raise HTTPException(400, "that file is over 80 MB")
                ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                disp = r.headers.get("content-disposition", "")
                final = str(r.url)
    except httpx.HTTPError as e:
        raise HTTPException(400, f"couldn't download the link: {e}") from e

    name = ""
    if "filename=" in disp:
        name = disp.split("filename=")[-1].strip('"; ')
    name = name or unquote(Path(urlparse(final).path).name) or "download"
    ext = Path(name).suffix.lower()
    if ext not in UPLOAD_EXTS:
        ext = CONTENT_EXTS.get(ctype, "")
    if data[:4] == b"%PDF":
        ext = ".pdf"
    elif data[:4] == b"FLhd":
        ext = ".flp"
    elif data[:4] == b"MThd":
        ext = ".mid"
    if ext not in UPLOAD_EXTS:
        raise HTTPException(400, f"that link isn't a music file this app reads ({ctype or 'unknown type'})")
    stem = Path(name).stem if Path(name).suffix else name
    if ext in (".html", ".htm"):
        text = data.decode("utf-8", errors="replace")
        if "songsterr" in final.lower() and '<script id="state"' in text:
            from tabmaker import songsterr

            try:
                meta = songsterr.parse_page(text)
                stem = " - ".join(x for x in (meta.get("artist"), meta.get("title")) if x) or stem
            except Exception:  # fall through to the normal check, which explains what's wrong
                pass
        elif "musescore.com" in final.lower():
            from tabmaker import musescore_page

            info = musescore_page.parse_page(text)
            raise HTTPException(400, info["message"] + (f" Download link: {info['mxlUrl']}" if info.get("mxlUrl") else ""))
        else:
            raise HTTPException(400, "that page isn't a music file. Links work for PDFs, images, MusicXML, MIDI, "
                                     ".flp files and Songsterr tab pages.")
    stem = _safe_name(stem)[:80]
    d = DATA_DIR / stem
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    (d / f"{stem}{ext}").write_bytes(bytes(data))
    return _check_new_project(d, stem, name)


# --- import from the studio library (a folder of FL Studio projects mounted read-only into the container)

LIBRARY_DIR = Path(os.environ.get("LIBRARY_DIR", "/library"))
LIBRARY_EXTS = {".flp", ".mid", ".midi", ".musicxml", ".mxl", ".pdf"}
SKIP_DIRS = {"backup", "@eadir", "#recycle", ".git"}  # FL autosaves, Synology thumbnails, recycle bin


def _library_path(rel: str) -> Path:
    root = LIBRARY_DIR.resolve()
    if not root.is_dir():
        raise HTTPException(404, "no studio folder is set up on this server")
    p = (root / rel.strip("/\\")).resolve()
    if p != root and root not in p.parents:
        raise HTTPException(400, "that's outside the studio folder")
    if not p.exists():
        raise HTTPException(404, "not found")
    return p


def _lib_entry(p: Path, root: Path) -> dict:
    st = p.stat()
    return {"name": p.name, "path": p.relative_to(root).as_posix(), "mtime": int(st.st_mtime),
            **({} if p.is_dir() else {"size": st.st_size})}


@app.get("/api/library")
def library(path: str = "") -> dict:
    """List one folder of the studio library: subfolders (newest first) and the files this app can import."""
    if not LIBRARY_DIR.is_dir():
        return {"enabled": False}
    root = LIBRARY_DIR.resolve()
    d = _library_path(path)
    if not d.is_dir():
        raise HTTPException(400, "not a folder")
    dirs, files = [], []
    for p in d.iterdir():
        try:
            if p.name.startswith("."):
                continue
            if p.is_dir():
                if p.name.lower() not in SKIP_DIRS:
                    dirs.append(_lib_entry(p, root))
            elif p.suffix.lower() in LIBRARY_EXTS:
                files.append(_lib_entry(p, root))
        except OSError:
            continue
    dirs.sort(key=lambda e: -e["mtime"])
    files.sort(key=lambda e: (Path(e["name"]).suffix.lower() != ".flp", -e["mtime"]))
    rel = d.relative_to(root).as_posix()
    return {"enabled": True, "path": "" if rel == "." else rel, "dirs": dirs, "files": files}


_lib_index: dict = {"at": 0.0, "files": []}
_lib_lock = threading.Lock()
LIB_INDEX_TTL = 300  # seconds; walking the whole NAS folder takes several seconds


def _library_index() -> list[dict]:
    """Every importable file in the library (cached for a few minutes)."""
    with _lib_lock:
        if time.time() - _lib_index["at"] < LIB_INDEX_TTL:
            return _lib_index["files"]
        root = _library_path("")
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [n for n in dirnames if n.lower() not in SKIP_DIRS and not n.startswith(".")]
            for n in filenames:
                if Path(n).suffix.lower() in LIBRARY_EXTS:
                    try:
                        e = _lib_entry(Path(dirpath) / n, root)
                    except OSError:
                        continue
                    e["hay"] = e["path"].lower()
                    files.append(e)
        files.sort(key=lambda e: -e["mtime"])
        _lib_index.update(at=time.time(), files=files)
        return files


@app.get("/api/library/search")
def library_search(q: str, limit: int = 150) -> dict:
    """Importable files anywhere in the library whose folder or file name contains every word of q."""
    words = [w for w in q.lower().split() if w]
    if not words:
        return {"files": [], "more": False}
    out = [e for e in _library_index() if all(w in e["hay"] for w in words)]
    return {"files": [{k: v for k, v in e.items() if k != "hay"} for e in out[:limit]], "more": len(out) > limit}


class LibraryIn(BaseModel):
    path: str


@app.post("/api/projects/from_library")
def project_from_library(body: LibraryIn) -> dict:
    """Copy a file from the studio library into a new project (the original is never touched)."""
    src = _library_path(body.path)
    ext = src.suffix.lower()
    if not src.is_file() or ext not in LIBRARY_EXTS:
        raise HTTPException(400, "pick an FL Studio project, MIDI, MusicXML or PDF file")
    stem = _safe_name(src.stem)[:80]
    d = DATA_DIR / stem
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    shutil.copy2(src, d / f"{stem}{ext}")
    return _check_new_project(d, stem, src.name)


@app.post("/api/projects/{name}/pages")
async def add_pages(name: str, files: list[UploadFile] = File(...)) -> dict:
    """Append photos/PDF pages to a sheet-music project (e.g. shot one at a time on a phone)."""
    d = _project_dir(name)
    kind, srcs = _sources(d)
    if kind != "scan":
        raise HTTPException(400, "pages can only be added to a PDF / photo project")
    if _active_job(name):
        raise HTTPException(409, "wait for the running job to finish")
    files = [f for f in files if f.filename]
    exts = [Path(f.filename).suffix.lower() or ".png" for f in files]
    if not files or any(e not in SCAN_EXTS for e in exts):
        raise HTTPException(400, "add photos or PDFs")
    # Number every input so the pages stay in order: existing ones first, then the new ones.
    for i, src in enumerate(srcs, 1):
        want = d / f"{i:03d}{src.suffix.lower()}"
        if src != want:
            src.rename(want)
    for i, (f, ext) in enumerate(zip(files, exts), len(srcs) + 1):
        with (d / f"{i:03d}{ext}").open("wb") as fh:
            shutil.copyfileobj(f.file, fh)
    (d / sheet.PLAN_FILE).unlink(missing_ok=True)  # pages changed: find the parts again
    work = _work(name)
    if work.exists():
        shutil.rmtree(work)
    try:
        sheet.render_pages(d, work)
    except Exception as e:
        raise HTTPException(400, f"couldn't read the new pages: {e}") from e
    return _summary(name)


@app.get("/api/projects/{name}")
def get_project(name: str) -> dict:
    return _summary(name)


@app.delete("/api/projects/{name}")
def delete_project(name: str) -> dict:
    d = _project_dir(name)
    if _active_job(name):
        raise HTTPException(409, "wait for the running job to finish")
    shutil.rmtree(d)
    shutil.rmtree(OUTPUT_DIR / name, ignore_errors=True)
    for jid in [k for k, j in JOBS.items() if j["project"] == name]:
        JOBS.pop(jid, None)
        (JOBS_DIR / f"{jid}.json").unlink(missing_ok=True)
    return {"ok": True}


class MappingIn(BaseModel):
    title: str = ""
    channels: list[dict] = []


@app.put("/api/projects/{name}/mapping")
def save_mapping(name: str, body: MappingIn) -> dict:
    d = _project_dir(name)
    mapping.save(d, mapping.Mapping.from_json(body.model_dump()))
    return _summary(name)


@app.delete("/api/projects/{name}/mapping")
def reset_mapping(name: str) -> dict:
    d = _project_dir(name)
    (d / mapping.FILE_NAME).unlink(missing_ok=True)
    return _summary(name)


@app.post("/api/projects/{name}/suggest")
def suggest(name: str) -> dict:
    """Ask the AI endpoint which MuseScore instrument fits each FL channel (not saved; the UI fills the rows)."""
    s = _summary(name)
    if s["kind"] != "flp":
        raise HTTPException(400, "instrument suggestions are for FL Studio projects")
    try:
        return {"channels": assist.suggest_mapping(ai.for_task(_ai_cfg(), "suggest"), s["channels"],
                                                   _settings()["favorites"])}
    except ai.AIError as e:
        raise HTTPException(502, str(e)) from e


# --- scans: pages and part plan

@app.get("/api/projects/{name}/pages/{n}")
def page_image(name: str, n: int, size: int = 400):
    """A page of a scanned project, as a JPEG thumbnail (size = long side in px)."""
    _project_dir(name)
    src = _work(name) / "pages" / f"p{n:03d}.png"
    if not src.is_file():
        raise HTTPException(404, "no such page")
    size = max(100, min(size, 2000))
    thumb = _work(name) / "thumbs" / f"p{n:03d}-{size}.jpg"
    if not thumb.exists() or thumb.stat().st_mtime < src.stat().st_mtime:
        from PIL import Image

        thumb.parent.mkdir(parents=True, exist_ok=True)
        im = Image.open(src).convert("L")
        im.thumbnail((size, size))
        im.save(thumb, "JPEG", quality=80)
    return FileResponse(thumb, media_type="image/jpeg")


class PartIn(BaseModel):
    name: str
    pages: list[int]
    clef: str | None = None
    fifths: int | None = None
    skip: bool = False


class PlanIn(BaseModel):
    title: str = ""
    parts: list[PartIn] = []


@app.put("/api/projects/{name}/plan")
def save_plan(name: str, body: PlanIn) -> dict:
    d = _project_dir(name)
    old = sheet.load_plan(d)
    plan = sheet.split.Plan(title=body.title.strip() or name,
                            parts=[sheet.split.Part(name=p.name.strip() or "Part", pages=sorted(set(p.pages)),
                                                    clef=p.clef or None, fifths=p.fifths, skip=p.skip)
                                   for p in body.parts if p.pages],
                            score_pages=old.score_pages if old else [])
    sheet.save_plan(d, plan)
    return _summary(name)


class ReadAsIn(BaseModel):
    read_as: str


@app.put("/api/projects/{name}/read_as")
def put_read_as(name: str, body: ReadAsIn) -> dict:
    d = _project_dir(name)
    if body.read_as not in ("notation", "tab"):
        raise HTTPException(400, "read_as is 'notation' or 'tab'")
    sheet.set_read_as(d, body.read_as)
    return _summary(name)


class DetectIn(BaseModel):
    use_ai: bool = True


@app.post("/api/projects/{name}/detect")
def detect(name: str, body: DetectIn) -> dict:
    d = _project_dir(name)
    if _sources(d)[0] != "scan":
        raise HTTPException(400, "part detection is for PDFs and photos")
    if body.use_ai and not ai.configured(_ai_cfg()):
        raise HTTPException(400, "No AI endpoint is set up yet: add one on the Settings page, or use OCR.")
    return _queue_job(name, "detect", {"use_ai": body.use_ai})


# --- converting

class ConvertIn(BaseModel):
    formats: list[str] = ["mscz", "pdf"]
    mapping: MappingIn | None = None
    plan: PlanIn | None = None
    tracks: list[int] | None = None  # tab projects: Songsterr track ids


@app.post("/api/projects/{name}/convert")
def run_convert(name: str, body: ConvertIn) -> dict:
    """Save the choices sent with the request, then queue the conversion as a background job."""
    d = _project_dir(name)
    kind, _files = _sources(d)
    if not export.find_musescore():
        raise HTTPException(500, "MuseScore is not installed in this environment")
    if kind == "scan" and sheet.read_as(d) == "tab" and not ai.configured(_ai_cfg()):
        raise HTTPException(400, "Reading tab from photos needs an AI endpoint: set one up on the Settings page.")
    if kind == "scan" and body.plan is not None and sheet.read_as(d) != "tab":
        save_plan(name, body.plan)
    if kind == "flp" and body.mapping is not None:
        mapping.save(d, mapping.Mapping.from_json(body.mapping.model_dump()))
    if kind == "tab" and body.tracks:
        (d / "tracks.json").write_text(json.dumps(body.tracks), encoding="utf-8")
    job = _queue_job(name, "convert", {"formats": [x for x in body.formats if x in FORMATS]})
    summary = _summary(name)
    summary["job"] = job
    return summary


def _run_job(j: dict, log, progress) -> dict | None:
    """Do a job's work. Everything it needs is read from the project folder and the job's params."""
    name, params = j["project"], j.get("params") or {}
    d = DATA_DIR / name
    if not d.is_dir():
        raise RuntimeError("the piece was deleted")
    kind, files = _sources(d)
    out_dir = OUTPUT_DIR / name
    if j["kind"] == "detect":
        cfg = _ai_cfg() if params.get("use_ai") else None
        plan = sheet.detect_plan(d, _work(name), name, cfg, log)
        return {"parts": [{"part": p.name, "instrument": f"pages {p.pages[0]}–{p.pages[-1]}", "notes": "—"}
                          for p in plan.parts], "failed": []}

    formats = params.get("formats") or ["mscz", "pdf"]
    if kind == "scan" and sheet.read_as(d) == "tab":
        res = sheet.process_tab(d, _work(name), out_dir, name, formats, _ai_cfg(), log, progress)
    elif kind == "scan":
        cfg = _ai_cfg()
        if sheet.load_plan(d) is None:
            use_ai = cfg.get("use_for_parts", True) and ai.configured(cfg)
            sheet.detect_plan(d, _work(name), name, cfg if use_ai else None, log)
        res = sheet.process(d, _work(name), out_dir, name, formats, log, progress)
    elif kind == "flp":
        log("Building the score from the FL Studio project…")
        progress(0.2)
        res = convert(files[0], out_dir, formats, mapping.load(d))
    elif kind == "tab":
        from tabmaker import gpsong, songsterr, songsterr_song

        meta = _read_tab_source(files[0])
        ids = _tab_tracks(d, meta)
        log(f"Getting {len(ids)} track(s)…")
        try:
            if meta["kind"] == "songsterr-json":
                parts = [json.loads(files[0].read_bytes())]
            else:
                parts = songsterr.fetch_parts_sync(meta, ids)
            song = songsterr_song.build_song(parts, meta.get("title", ""), meta.get("artist", ""), meta.get("tempo"))
        except (songsterr.SongsterrError, ValueError, KeyError) as e:
            raise RuntimeError(f"Couldn't build the tab: {e}") from e
        gp = _work(name) / "tab.gp5"
        gp.parent.mkdir(parents=True, exist_ok=True)
        gp.write_bytes(gpsong.to_gp5_bytes(song))
        progress(0.3)
        log("MuseScore: writing the score…")
        res = export_file(gp, out_dir, name, formats, title=meta.get("title") or name)
        res.parts = [{"part": t.name, "instrument": "tab",
                      "notes": sum(len(b.notes) for m in t.measures for v in m.voices for b in v.beats)}
                     for t in song.tracks]
    else:  # MusicXML / MIDI: MuseScore reads it directly
        log("MuseScore: writing the score…")
        progress(0.2)
        res = export_file(files[0], out_dir, name, formats)
        res.parts = [{"part": "score", "instrument": files[0].suffix.lstrip(".").upper(), "notes": "—"}]
    if res.failed:
        log("MuseScore could not write: " + ", ".join(res.failed))
    if not res.parts:
        raise RuntimeError("Nothing to notate: no notes were found (audio clips can't be scored).")
    log(f"Done: {len(res.parts)} part(s).")
    return {"parts": res.parts, "failed": res.failed}


@app.get("/api/projects/{name}/files/{filename:path}")
def get_file(name: str, filename: str):
    out = (OUTPUT_DIR / name).resolve()
    f = (out / filename).resolve()
    if out not in f.parents or "_work" in f.relative_to(out).parts or not f.is_file():
        raise HTTPException(404, "no such file")
    media = {"svg": "image/svg+xml", "pdf": "application/pdf", "mp3": "audio/mpeg",
             "gp5": "application/octet-stream"}.get(f.suffix.lstrip("."))
    inline = f.suffix in (".svg", ".pdf", ".mp3")
    return FileResponse(f, media_type=media, filename=None if inline else f.name)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
