# Pocket Composer

Turns an FL Studio project (`.flp`) straight into a MuseScore score. You don't need to export MIDI or have FL Studio open. The web UI also turns PDFs and photos of sheet music (musescore-maker v2's pipeline, with an optional AI endpoint), MusicXML and MIDI into scores.

## Web UI (Docker)

```
docker compose up -d --build
```

Then open http://localhost:8090.

- **Upload** a `.flp`, or put it in `data\<name>\<name>.flp`. That folder is mounted into the container.
- **Map channels.** Each channel with notes on the playlist gets one row:
  - Use it or leave it out.
  - Set the part name and pick any of MuseScore's 644 instruments. The picker shows Favorites first, then one collapsed section per MuseScore group. Search opens every group that has a match. ▶ plays a short sample.
  - Choose staves (auto, 1 or 2) for two-staff instruments like piano and synths.
  - For drum instruments, pick a drum sound.
  - Shift by octaves.
- **Merge channels.** Channels with the same part name and instrument become one part. That's how you put two alternating melody channels, or all drum channels, on one staff.
- **Save.** Choices are saved to `data\<name>\mapping.json`. The command line uses that file too.
- **Convert** writes `.mscz`, `.pdf`, `.mxl` or `.mid`, or audio `.mp3` to `output\<name>\` and shows the score pages in the browser.
- **Settings** (⚙, top right): mark favorite instruments. They are saved in `data\settings.json`, so every device sees the same list.
- **Previews.** Instrument samples and score audio are rendered by MuseScore with its built-in MS Basic sounds, so they match what MuseScore plays. An instrument's first preview takes a few seconds. After that it is cached in `output\_previews\`.
- **Following the score.** While the score audio plays, a line moves through the pages and the view scrolls with it. Tap anywhere on a page to jump the audio there. The line uses MuseScore's note-position export (`<name>.spos`), which is written whenever .mp3 is selected.

The image includes MuseScore Studio 4.7.6 for Linux, running headless. To change the version, set the `MUSESCORE_VERSION` and `MUSESCORE_BUILD` build args in the `Dockerfile`.

Local, without Docker: `.venv\Scripts\python -m uvicorn app.main:app --port 8765`.

## Sheet music, MusicXML and MIDI (web UI)

The upload box also takes:

- **PDF or photos of sheet music.** Pick several photos to make the pages of one piece, in the order you pick them. This is musescore-maker v2's pipeline, running in the container:
  1. Each page is rendered at 300 dpi.
  2. **Finding parts** works out which pages belong to which instrument part, and reads the title, clef and key. Your AI endpoint does this when one is set up. Otherwise OCR reads the page headers. You can edit the result in the Parts table (name, pages, clef, key, use or skip). The part name chooses the instrument.
  3. **homr** reads the notes. It is a recognition model that runs locally, takes about 10–15 s per page on CPU, and caches each page.
  4. The clean-up (clef/key repair, multi-bar rests, bar lengths) builds each part.
  5. MuseScore writes the full score and one `.mscz` + `.pdf` per part (in `output\<name>\parts\`).
  These conversions run in the background with a live progress log, so a phone can stay on the page.
- **MusicXML (.musicxml/.mxl/.xml) and MIDI** go straight through MuseScore. You get the same formats, page previews and audio.

## Tabs and Guitar Pro (musescore-maker v1)

- **Photos of a guitar or bass tab.** Upload them like sheet music, then set "These pages are" to *guitar or bass tab*. Your AI endpoint writes out every bar's frets and rhythm as JSON. That becomes a Guitar Pro file, which MuseScore turns into a score, PDF and audio. Use a model that can read images. Long tabs take a few minutes.
- **A saved Songsterr tab page** (Ctrl+S, "Webpage, HTML only") or a Songsterr part `.json`. Tick the tracks you want and they are downloaded from Songsterr's CDN.
- **A saved MuseScore.com page** has no notes in it. The upload tells you its official MusicXML download link instead.
- **Guitar Pro (.gp5)** is an output format for every kind of project. Scans use musescore-maker v2's writer, which knows each part's instrument and tuning. Everything else uses v1's MusicXML converter, which picks strings and frets from the tuning. (musescore-maker's `.gp` output needed Node.js and alphaTab, so it isn't included; Guitar Pro 6–8 open `.gp5`.)

## AI endpoint (Settings → AI endpoint)

Choose a service, paste an API key, then press **List models** and pick a model:

| Service | Endpoint | Notes |
|---|---|---|
| Ollama Cloud | `https://ollama.com` | Ollama API key |
| Ollama on this PC | `http://host.docker.internal:11434` | no key |
| Meta Llama API | `https://api.llama.com/compat/v1` | OpenAI-compatible |
| OpenAI / OpenRouter / any OpenAI-compatible | `.../v1` | |
| Claude | `https://api.anthropic.com` | official `anthropic` SDK; Claude's server-side refusal fallback is on |
| Gemini | `https://generativelanguage.googleapis.com` | |

**Test connection** sends one short request. For sheet music, pick a model that can read images.

What the AI does:
- Reads each page for the title, instrument, whether it is a full score, and clef and key. Those answers become the part list.
- **✨ Suggest instruments** on FL Studio projects picks a MuseScore instrument and part name for each channel. It prefers your favorites. The suggestions are only saved when you Save or Convert.

The notes themselves always come from homr, not the AI. Chat models are much less reliable at reading dense notation note by note.

The key is stored in `data\settings.json` on the server and is never sent back to the browser. The web UI has no login, so anyone on your network who can open it can run jobs on your key.

`tools/mock_ai.py` is a stand-in endpoint that speaks all four APIs, for testing without a key.

## Command line

```
convert.bat                          # every .flp under .\data
convert.bat data\rac\rac.flp         # one project
convert.bat data --formats mscz,pdf,mxl,mid
```

Results go to `output\<project>\<project>.{musicxml,mscz,pdf,...}`.

Setup (once): `py -3.12 -m venv .venv` then `.venv\Scripts\pip install -r requirements.txt`. MuseScore 4 must be installed. Set `MUSESCORE` if it isn't in the default location.

## How it works (`flp2score/`)

1. **`flp.py`** reads the `.flp` event stream: tempo, time signature, channel names, the notes in each pattern, and the playlist clips. Tested on FL 21.0, 21.2 and 26.1. FL 26 uses a bigger playlist record (88 bytes instead of 60), and the reader detects which one a file uses.
2. **`arrange.py`** places every pattern clip on the timeline, so each channel gets one note list for the whole song. Muted clips are skipped and identical stacked clips are counted once.
3. **`instruments.py`** guesses a MuseScore instrument for each channel from its name. **`mapping.py`** stores what you choose instead. **`catalogue.py`** loads the MuseScore instrument list, `catalogue.json`, which `tools/build_catalogue.py` builds from MuseScore's `instruments.xml`. For example, "DSK-Cello" becomes Violoncello, "GMS-Reese" and "808 Modulated" become Bass Synthesizer, and "808 HiHat" or "808 CH" become a closed hi-hat in the drum kit. Anything it doesn't recognise becomes Saw Synthesizer.
4. **`score.py`** builds the notation with music21:
   - Timing snaps to 16ths, or to 8th-note triplets in beats that are clearly triplets. Each beat uses one grid, so rhythms stay readable.
   - Single-staff parts are trimmed so one note ends where the next begins. Two-staff instruments (piano, synths) use one staff when the notes fit on one, and split at middle C when they don't.
   - The key signature is the one that needs the fewest accidentals.
   - Black keys are spelled against the notes the piece actually uses.
   - Drum channels are laid out using the chosen MuseScore drum kit. Transposing instruments (bass, E♭/B♭ horns) are written at written pitch, with their transposition.
5. **`export.py`** runs MuseScore's command line to write `.mscz`, `.pdf` and the other formats.

## Checking a conversion

```
convert.bat data --formats mscz,pdf,mid
.venv\Scripts\python tools\roundtrip.py data
```

`roundtrip.py` compares the notes MuseScore plays back with the notes in the `.flp`, after the same timing clean-up. It reports any missing or extra notes for each project.

## Limits

- Only piano-roll notes become notation. Audio clips (recordings, stems, samples on the playlist) are ignored.
- Automation, pitch slides, and per-note details like fine pitch, pan and release are ignored.
- Drum sounds come from channel names. Multi-sound kits like FPC are assumed to use the General MIDI drum map. Hand-drum packs are mapped in pitch order onto conga and bongo sounds.
- Each channel is read as one part. Tempo changes and time-signature changes inside the song are not read yet.
