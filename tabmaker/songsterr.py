"""Read a saved Songsterr tab page and download its track data."""
import json
import os
import re

import httpx

CDN = os.environ.get("SONGSTERR_CDN", "https://dqsljvtekg760.cloudfront.net")
HEADERS = {"User-Agent": "Mozilla/5.0 (songsterr2gp)"}


class SongsterrError(Exception):
    pass


def parse_page(html: str) -> dict:
    """Pull song metadata out of the page's embedded <script id="state"> JSON."""
    m = re.search(r'<script id="state" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        raise SongsterrError("No Songsterr state JSON found. Save the tab page itself (Ctrl+S) and upload that.")
    state = json.loads(m.group(1))
    cur = (state.get("meta") or {}).get("current") or {}
    for key in ("songId", "revisionId", "image", "tracks"):
        if not cur.get(key):
            raise SongsterrError(f"Page state is missing meta.current.{key}")
    return {
        "songId": cur["songId"],
        "revisionId": cur["revisionId"],
        "image": cur["image"],
        "title": cur.get("title", ""),
        "artist": cur.get("artist", ""),
        "defaultPartId": cur.get("partId", cur.get("defaultTrack", 0)),
        "tempo": ((state.get("tempo") or {}).get("tempo") or {}).get("bpm"),
        "tracks": [
            {"partId": t.get("partId", i), "title": t.get("title") or t.get("instrument") or f"Track {i + 1}",
             "instrument": t.get("instrument", ""), "isDrums": bool(t.get("isDrums"))}
            for i, t in enumerate(cur["tracks"])
        ],
    }


def part_url(meta: dict, part_id: int) -> str:
    return f"{CDN}/{meta['songId']}/{meta['revisionId']}/{meta['image']}/{part_id}.json"


async def fetch_parts(meta: dict, part_ids: list[int]) -> list[dict]:
    parts = []
    async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        for pid in part_ids:
            r = await client.get(part_url(meta, pid))
            if r.status_code != 200:
                raise SongsterrError(f"Download of track {pid} failed ({r.status_code}): {part_url(meta, pid)}")
            parts.append(r.json())
    return parts


def fetch_parts_sync(meta: dict, part_ids: list[int]) -> list[dict]:
    parts = []
    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        for pid in part_ids:
            r = client.get(part_url(meta, pid))
            if r.status_code != 200:
                raise SongsterrError(f"Download of track {pid} failed ({r.status_code}): {part_url(meta, pid)}")
            parts.append(r.json())
    return parts
