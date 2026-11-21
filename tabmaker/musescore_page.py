"""Read a saved MuseScore.com score page.

The page has no notes in it; the score itself comes from MuseScore's own download
(which needs you to be logged in). We pull the official MusicXML download link out of
the page so you can grab it and upload the .mxl here.
"""
import html as htmllib
import json
import re


def is_musescore_page(text: str) -> bool:
    return "musescore.com" in text[:200000] and "type_download_list" in text


def parse_page(text: str) -> dict:
    h = htmllib.unescape(text)
    title = re.search(r'"@type":\s*"MusicComposition".*?"name":\s*"([^"]*)"', h, re.S)
    links = {}
    m = re.search(r'"type_download_list":(\[.*?\])', h)
    if m:
        try:
            links = {d["type"]: d["url"] for d in json.loads(m.group(1))}
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    return {
        "source": "musescore",
        "title": title.group(1).strip() if title else "",
        "mxlUrl": links.get("mxl"),
        "midUrl": links.get("mid"),
        "message": "MuseScore.com pages don't contain the notes. Download the MusicXML (.mxl) "
                   "from the link (log in to MuseScore first), then upload that file here.",
    }
