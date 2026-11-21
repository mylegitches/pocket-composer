"""Convert Songsterr part JSON into a PyGuitarPro song."""
import guitarpro as gp

from .gpsong import add_track, duration, make_song

STD_TUNING = {4: [43, 38, 33, 28], 5: [43, 38, 33, 28, 23], 6: [64, 59, 55, 50, 45, 40], 7: [64, 59, 55, 50, 45, 40, 35]}


def _fill(track: gp.Track, part: dict):
    for i, m in enumerate(part.get("measures", [])[: len(track.measures)]):
        meas = track.measures[i]
        for vi, voice in enumerate(m.get("voices", [])[: len(meas.voices)]):
            v = meas.voices[vi]
            for b in voice.get("beats", []):
                notes = [n for n in b.get("notes", []) if not n.get("rest")]
                beat = gp.Beat(v, duration=duration(int(b.get("type", 4)), int(b.get("dots", 0)), b.get("tuplet")),
                               status=gp.BeatStatus.normal if notes and not b.get("rest") else gp.BeatStatus.rest)
                if beat.status == gp.BeatStatus.normal:
                    beat.effect.palmMute = bool(b.get("palmMute"))
                    for n in notes:
                        kind = gp.NoteType.tie if n.get("tie") else gp.NoteType.dead if n.get("dead") else gp.NoteType.normal
                        note = gp.Note(beat, value=int(n.get("fret", 0)), string=int(n.get("string", 0)) + 1, type=kind)
                        e = note.effect
                        e.staccato = bool(n.get("staccato") or b.get("staccato"))
                        e.ghostNote = bool(n.get("ghost"))
                        e.hammer = bool(n.get("hp"))
                        e.vibrato = bool(n.get("vibrato"))
                        e.letRing = bool(n.get("letRing") or b.get("letRing"))
                        e.palmMute = bool(n.get("palmMute") or b.get("palmMute"))
                        e.accentuatedNote = bool(n.get("accentuated") or b.get("accentuated"))
                        if n.get("slide"):
                            e.slides = [gp.SlideType.legatoSlideTo if n["slide"] == "legato" else gp.SlideType.shiftSlideTo]
                        beat.notes.append(note)
                v.beats.append(beat)


def build_song(parts: list[dict], title: str = "", artist: str = "", tempo: int | None = None) -> gp.Song:
    if not parts:
        raise ValueError("No tracks to convert")
    autos = (parts[0].get("automations") or {}).get("tempo") or []
    sig, headers = (4, 4), []
    for m in parts[0].get("measures", []):  # bar structure comes from the first track
        sig = tuple(m.get("signature") or sig)
        headers.append({"sig": sig, "repeatOpen": m.get("repeatStart"), "repeatClose": max(1, int(m["repeatClose"]) - 1) if m.get("repeatClose") else None,
                        "alts": m.get("alternateEnding"), "marker": (m.get("marker") or {}).get("text")})
    song = make_song(title, artist, autos[0]["bpm"] if autos else tempo or 120, headers)

    for part in parts:
        drums = part.get("instrumentId") == 1024 or "drum" in str(part.get("instrument", "")).lower()
        tuning = part.get("tuning") or STD_TUNING.get(part.get("strings", 6), STD_TUNING[6])
        track = add_track(song, part.get("name") or part.get("instrument"), tuning,
                          int(part.get("instrumentId") or 0) if not drums else 0, drums)
        track.fretCount = int(part.get("frets", 24))
        track.offset = int(part.get("capo", 0) or 0)
        _fill(track, part)

    for a in autos[1:]:  # later tempo changes
        try:
            beat = song.tracks[0].measures[a["measure"]].voices[0].beats[0]
        except (IndexError, KeyError):
            continue
        beat.effect.mixTableChange = gp.MixTableChange(tempo=gp.MixTableItem(value=int(a["bpm"])))
    return song
