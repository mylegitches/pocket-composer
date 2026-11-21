<p align="center"><img src="app/static/icon.svg" width="120" alt="Pocket Composer: a music note popping out of a pocket"></p>

# Pocket Composer

**Turn any music into a MuseScore score.** Give it a photo of a lead sheet, a scanned PDF of a band chart, a picture of a guitar tab, a link to a tab page, a MusicXML or MIDI file, or an FL Studio project. Pocket Composer gives you back an editable MuseScore project (`.mscz`), plus a PDF, audio, MusicXML, MIDI and Guitar Pro. It runs in Docker, opens in any browser (phones too), and engraves with a headless copy of MuseScore Studio 4.

```
docker compose up -d --build        # then open http://localhost:8090
```

## What you can put in

| Input | How it gets in | What happens |
|---|---|---|
| 📷 **Photos or scans of sheet music** (JPEG, PNG, HEIC, WebP, TIFF; pick several for several pages) | **Camera**, **Photos** (your phone's library), **Files**, drag and drop, or paste an image | Pages are split into instrument parts and the notes are read by a recognition model. You get a full score plus one file per part. |
| 📄 **PDF of sheet music** (band charts, lead sheets, parts) | upload or link | Same as photos, one page at a time. |
| 🎸 **Photos of a guitar or bass tab** | upload, then choose *tab* | Your AI model writes out every bar's frets and rhythm, which becomes Guitar Pro and then a score. |
| 🔗 **Songsterr tab page** | paste the link, or upload the saved page | Pick tracks. They're downloaded and turned into Guitar Pro and a score. |
| 🎼 **MusicXML** (`.musicxml`, `.mxl`, `.xml`) | upload or link | Engraved straight away. Add Guitar Pro if you like. |
| 🎹 **MIDI** (`.mid`) | upload or link | Imported and engraved by MuseScore. |
| 🎛️ **FL Studio project** (`.flp`) | upload or link | Read directly; no MIDI export needed. Choose a MuseScore instrument for each channel, then convert. |

A **MuseScore.com score page** has no notes in it, so pasting one gets you its official MusicXML download link to fetch (you need to be logged in) and upload.

## What you get out

- **`.mscz`**: a MuseScore project you can open and edit.
- **`.pdf`**: the engraved score. Scans with several parts also get one PDF and one `.mscz` per part.
- **audio (`.mp3`)**: played in the browser while a yellow highlighter strip follows along in the score. Tap anywhere on the score to jump there.
- **`.mxl`** (Sibelius, Finale, Dorico), **`.mid`** and **Guitar Pro `.gp5`** (Guitar Pro 5–8, TuxGuitar).
- Page previews right in the browser.

## The web app

- **Projects** in the sidebar: take a photo, pick from your phone's photo library or your files, drop files onto the page, paste a link, or paste an image from the clipboard (a screenshot, or "Copy image" on a phone) anywhere on the page. With a sheet-music project open, a pasted image or **+ Add pages** adds pages to it, so you can photograph a chart one page at a time.
- **Sheet music and tabs**: a Parts table shows which pages belong to which instrument, with clef and key. Page thumbnails are outlined in each part's colour. Fix anything, then **Convert**. Long jobs run in the background with a live progress log.
- **FL Studio**: one row per channel. Pick from all 644 MuseScore instruments, with ★ favorites on top and collapsible groups. Press ▶ to hear an instrument in MuseScore's own sounds. Channels with the same part name and instrument are merged onto one staff. **✨ Suggest instruments** lets your AI model choose.
- **Background jobs**: every conversion runs on the server, one at a time, in a queue. You can close the page, switch pieces or use another device. The project list shows each piece's status live (waiting, working %, failed, ✓ scored). Jobs are saved to disk, so after a restart unfinished ones pick up where they left off (pages already read stay cached). Waiting jobs can be cancelled.
- **Full scores** (several instruments on every system) are read as one part when that's all there is. The recognition model reads the main pitched staves, such as a piano's grand staff; percussion staves are left out.
- **Settings**: favorite instruments and the AI endpoint.

Everything you choose is saved on the server, so the phone and the computer see the same projects and settings.

## AI endpoint (bring your own)

Reading notes doesn't need AI. **homr**, an optical music recognition model, runs inside the container and reads the notes on sheet-music pages. An AI model does the jobs that need judgement:

| Job | Needs |
|---|---|
| Reading sheet-music pages: title, which instrument each page is for, full-score pages, clef, key | a vision model |
| Reading guitar/bass tab photos into frets and rhythm | a vision model that gives long answers |
| Suggesting MuseScore instruments for FL Studio channels | any model |

Without an AI model, OCR finds the parts in sheet music and everything else still works; only tab photos need one. In **Settings → AI endpoint**, choose a service:

| Service | Endpoint |
|---|---|
| Ollama Cloud | `https://ollama.com` + API key |
| Ollama on your computer | `http://host.docker.internal:11434` |
| Meta Llama API | `https://api.llama.com/compat/v1` |
| OpenAI, OpenRouter, or anything OpenAI-compatible | `…/v1` |
| Claude | `https://api.anthropic.com` (official SDK, with Claude's refusal fallback switched on) |
| Gemini | `https://generativelanguage.googleapis.com` |

**List models** shows which models can read images ("· vision"). You can set one main model, or a different model for each job. Each job has a **Test** button that sends a sample page with a known answer and scores the reply. On Ollama Cloud (November 2026), `kimi-k3.1-pro` scored 100% on all three tests. It read the sample page in 4 s and the 16-note bass tab in about 45 s.

The API key is stored in `data/settings.json` on the server (git-ignored) and is never sent back to the browser. The app has no login, so only run it on a network you trust.

## How it works

```
photos / PDF ──► pages (300 dpi) ──► parts (AI or OCR) ──► homr OMR ──► clean-up ──┐
tab photos ───► AI reads frets + rhythm ──► Guitar Pro ───────────────────────────┤
Songsterr link / page ──► track download ──► Guitar Pro ──────────────────────────┤
FL Studio .flp ──► patterns + playlist ──► your instrument mapping ──► music21 ────┼──► MuseScore 4 ──► .mscz .pdf .mp3 .mxl .mid .gp5
MusicXML / MIDI ──────────────────────────────────────────────────────────────────┘
```

| Package | What it does |
|---|---|
| `musescore_maker/` | The sheet-music scanner: page rendering, part detection by OCR, homr OMR, clean-up (clef/key repair, multi-bar rests, bar lengths) and the Guitar Pro writer for scanned parts. |
| `tabmaker/` | Tabs: the Songsterr reader, MusicXML → Guitar Pro (string and fret picking), and the tab-photo reader that uses your AI model. |
| `flp2score/` | FL Studio reader and score builder, the MuseScore instrument catalogue, instrument previews, the AI layer (`ai.py`, `assist.py`, `aitest.py`) and conversion/export. |
| `app/` | FastAPI server and the web UI (plain HTML, CSS and JS). |

### FL Studio details

- `.flp` files are read directly: tempo, time signature, channel names, pattern notes and playlist clips. Tested on FL 21 and FL 26.
- Each channel becomes one note list for the whole song. Muted clips are skipped.
- Timing snaps to 16ths, or 8th-note triplets where a beat is clearly in triplets. The key signature is the one needing the fewest accidentals, and black keys are spelled to fit the music.
- Drum channels are laid out on the chosen MuseScore drum kit. Transposing instruments are written at written pitch.
- `tools/roundtrip.py` checks a conversion by comparing MuseScore's playback with the notes in the `.flp`.

## Running it

**Docker (recommended).** `docker compose up -d --build` builds an image (about 3 GB) with MuseScore Studio 4.7.6, homr's models and Python. It serves port 8090. `data/` (your inputs and settings) and `output/` (results) are mounted from this folder. To browse a folder of FL Studio projects from the app, mount it read-only at `/library` (see the commented line in `docker-compose.yml`): a **🎛️ Studio** button then appears next to Files, with folder browsing and search, and importing copies the file in without touching the original. To use another MuseScore version, change `MUSESCORE_VERSION` and `MUSESCORE_BUILD` in the `Dockerfile`.

**Without Docker** (Windows; MuseScore 4 installed):

```
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --port 8765
```

FL Studio projects can also be converted from the command line: `convert.bat data\song\song.flp --formats mscz,pdf`.

**Testing without an AI key:** `tools/mock_ai.py` is a stand-in endpoint that speaks the Ollama, OpenAI, Claude and Gemini APIs.

## Limits

- Recognising notes is never perfect: proof-read scans in MuseScore. Pitches, rhythms, keys and clefs are usually right. Chord symbols, lyrics, dynamics, repeats and endings, and drum notation often aren't.
- Tab photos are only as good as the AI model reading them. Use **Test** to compare models.
- FL Studio: only piano-roll notes become notation. Audio clips, automation and tempo changes within the song are ignored.
- Songsterr downloads use Songsterr's public data files and may stop working if Songsterr changes them.
- Guitar Pro output is `.gp5`. The newer `.gp` format isn't written.
