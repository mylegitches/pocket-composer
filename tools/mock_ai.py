"""A stand-in AI endpoint for testing flp2score's AI plumbing without a real API key.

    python tools/mock_ai.py 9999

Answers in the wire format of all four providers (Ollama /api/chat, OpenAI /chat/completions,
Anthropic /v1/messages, Gemini :generateContent) with canned replies, and logs what it received
(provider, model, number of images) to stdout."""

from __future__ import annotations

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


def reply_for(prompt: str) -> str:
    if m := re.search(r"page (\d+) of (\d+)", prompt):
        n = int(m.group(1))
        inst = "Trumpet 1" if n == 1 else "Alto Sax"
        return json.dumps({"title": "Blue Test", "instrument": inst, "first_page": True, "full_score": False,
                           "has_music": True, "clef": "G2", "fifths": -1 if n == 1 else 2})
    if "Transcribe every measure" in prompt:  # a tab: two bars of a bass riff
        bar = lambda n: {"number": n, "signature": [4, 4], "repeatStart": False, "repeatTimes": 0, "ending": 0,
                         "marker": "", "beats": [{"type": 4, "dots": 0, "tuplet": 0, "rest": False,
                                                  "notes": [{"string": s, "fret": f, "tie": False, "dead": False,
                                                             "staccato": False, "accent": False, "slide": False,
                                                             "hammer": False}]} for s, f in ((3, 3), (3, 5), (2, 3), (2, 5))]}
        return json.dumps({"title": "Mock Riff", "artist": "Test", "instrument": "Electric Bass (finger)", "tempo": 100,
                           "tuning": [43, 38, 33, 28], "measures": [bar(1), bar(2)]})
    if "FL Studio channels" in prompt:
        chans = [int(c) for c in re.findall(r"- channel (\d+):", prompt)]
        return "```json\n" + json.dumps({"channels": [
            {"channel": c, "instrument": "violin" if i == 0 else "contrabass", "part": f"AI part {i + 1}",
             "include": True} for i, c in enumerate(chans)]}) + "\n```"
    return "OK"


class H(BaseHTTPRequestHandler):
    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/tags"):
            return self._send({"models": [{"name": "mock-vision:latest"}]})
        if self.path.startswith("/v1/models") and "anthropic-version" in self.headers:
            return self._send({"data": [{"type": "model", "id": "claude-mock", "display_name": "Mock",
                                         "created_at": "2026-01-01T00:00:00Z"}], "has_more": False,
                               "first_id": "claude-mock", "last_id": "claude-mock"})
        if self.path.startswith("/v1/models"):
            return self._send({"data": [{"id": "mock-gpt"}]})
        if self.path.startswith("/v1beta/models"):
            return self._send({"models": [{"name": "models/gemini-mock", "supportedGenerationMethods": ["generateContent"]}]})
        self.send_error(404)

    def do_POST(self):
        d = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        p = self.path
        if p.endswith("/api/chat"):
            msg = d["messages"][-1]
            text, imgs, who = msg["content"], len(msg.get("images", [])), "ollama"
            out = {"message": {"role": "assistant", "content": reply_for(text)}, "done": True}
        elif p.endswith("/chat/completions"):
            parts = d["messages"][-1]["content"]
            text = " ".join(x.get("text", "") for x in parts)
            imgs, who = sum(x["type"] == "image_url" for x in parts), "openai"
            out = {"choices": [{"message": {"role": "assistant", "content": reply_for(text)}}]}
        elif p.startswith("/v1/messages"):
            parts = d["messages"][-1]["content"]
            text = " ".join(x.get("text", "") for x in parts)
            imgs, who = sum(x["type"] == "image" for x in parts), f"anthropic(fallbacks={d.get('fallbacks')})"
            out = {"id": "msg_mock", "type": "message", "role": "assistant", "model": d["model"],
                   "content": [{"type": "text", "text": reply_for(text)}], "stop_reason": "end_turn",
                   "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}
        elif ":generateContent" in p:
            parts = d["contents"][-1]["parts"]
            text = " ".join(x.get("text", "") for x in parts)
            imgs, who = sum("inline_data" in x for x in parts), "gemini"
            out = {"candidates": [{"content": {"parts": [{"text": reply_for(text)}]}}]}
        else:
            return self.send_error(404)
        print(f"{who} model={d.get('model', p)} images={imgs} auth={bool(self.headers.get('Authorization') or self.headers.get('x-api-key') or self.headers.get('x-goog-api-key'))}",
              flush=True)
        self._send(out)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", int(sys.argv[1]) if len(sys.argv) > 1 else 9999), H).serve_forever()
