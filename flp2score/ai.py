"""One small interface over the AI providers people actually use.

    ask(cfg, system, prompt, images=[png bytes...]) -> reply text
    ask_json(...)                                  -> parsed JSON object
    list_models(cfg)                               -> model ids

Providers:
  ollama     Ollama's own API: Ollama Cloud (https://ollama.com + API key) or a local Ollama
  openai     anything OpenAI-compatible: OpenAI, Meta's Llama API, OpenRouter, Groq, LM Studio...
  anthropic  Claude, through the official anthropic SDK
  gemini     Google Gemini's REST API

The config is a plain dict: {"provider", "base_url", "model", "api_key"}.
"""

from __future__ import annotations

import base64
import json
import re

import httpx

TIMEOUT = httpx.Timeout(900.0, connect=15.0)  # a whole tab can take minutes

# Presets shown in the settings page. Model is left to the user (or picked from "List models").
PRESETS = [
    {"id": "ollama-cloud", "label": "Ollama Cloud", "provider": "ollama", "base_url": "https://ollama.com",
     "key": True, "model": "kimi-k3.1-pro"},
    {"id": "ollama-local", "label": "Ollama on this PC", "provider": "ollama",
     "base_url": "http://host.docker.internal:11434", "key": False},
    {"id": "llama-api", "label": "Meta Llama API", "provider": "openai",
     "base_url": "https://api.llama.com/compat/v1", "key": True},
    {"id": "openai", "label": "OpenAI", "provider": "openai", "base_url": "https://api.openai.com/v1", "key": True},
    {"id": "anthropic", "label": "Claude (Anthropic)", "provider": "anthropic", "base_url": "https://api.anthropic.com",
     "key": True, "model": "claude-opus-5-5"},
    {"id": "gemini", "label": "Google Gemini", "provider": "gemini",
     "base_url": "https://generativelanguage.googleapis.com", "key": True},
    {"id": "openrouter", "label": "OpenRouter", "provider": "openai", "base_url": "https://openrouter.ai/api/v1",
     "key": True},
    {"id": "custom", "label": "Other OpenAI-compatible", "provider": "openai", "base_url": "", "key": True},
]
PROVIDERS = {"ollama", "openai", "anthropic", "gemini"}
# The jobs the AI does; each can use its own model (settings "models": {task: model}, "" = the main model).
TASKS = {
    "parts": "Reading sheet-music pages (title, instrument, clef, key) — needs vision",
    "tab": "Reading guitar/bass tab photos into frets and rhythm — needs vision, long answers",
    "suggest": "Suggesting MuseScore instruments for FL Studio channels — text only",
}
# Claude models that take the server-side refusal fallback (see _anthropic).
_FALLBACK_MODELS = {"claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


class AIError(Exception):
    pass


def _local(url: str) -> bool:
    return any(h in url for h in ("localhost", "127.0.0.1", "host.docker.internal", "[::1]"))


def configured(cfg: dict | None) -> bool:
    """Ready to use: provider, URL and model set, and a key unless the endpoint is on this machine."""
    return bool(cfg and cfg.get("provider") in PROVIDERS and cfg.get("model") and cfg.get("base_url")
                and (cfg.get("api_key") or _local(cfg["base_url"])))


def for_task(cfg: dict, task: str) -> dict:
    """The config with the model chosen for this task (falls back to the main model)."""
    model = (cfg.get("models") or {}).get(task) or cfg.get("model", "")
    return {**cfg, "model": model}


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _mime(data: bytes) -> str:
    return "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"


def _check(r: httpx.Response, who: str) -> dict:
    if r.status_code >= 400:
        try:
            body = r.json()
            msg = body.get("error", body)
            if isinstance(msg, dict):
                msg = msg.get("message") or json.dumps(msg)[:300]
        except ValueError:
            msg = r.text[:300]
        raise AIError(f"{who} returned {r.status_code}: {msg}")
    return r.json()


def _bearer(cfg: dict) -> dict:
    return {"Authorization": f"Bearer {cfg['api_key']}"} if cfg.get("api_key") else {}


# ---------------------------------------------------------------- providers

def _ollama(cfg, system, prompt, images, want_json, max_tokens):
    msg = {"role": "user", "content": prompt}
    if images:
        msg["images"] = [_b64(i) for i in images]
    body = {"model": cfg["model"], "stream": False, "options": {"temperature": 0, "num_predict": max_tokens},
            "messages": [{"role": "system", "content": system}, msg]}
    if want_json:
        body["format"] = "json"
    r = httpx.post(cfg["base_url"].rstrip("/") + "/api/chat", json=body, headers=_bearer(cfg), timeout=TIMEOUT)
    return _check(r, "Ollama")["message"]["content"]


def _openai(cfg, system, prompt, images, want_json, max_tokens):
    content = [{"type": "text", "text": prompt}] + [
        {"type": "image_url", "image_url": {"url": f"data:{_mime(i)};base64,{_b64(i)}"}} for i in images]
    body = {"model": cfg["model"], "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
    r = httpx.post(cfg["base_url"].rstrip("/") + "/chat/completions", json=body, headers=_bearer(cfg),
                   timeout=TIMEOUT)
    d = _check(r, "The AI endpoint")
    msg = d["choices"][0]["message"]["content"]
    if isinstance(msg, list):  # some servers return content parts
        msg = "".join(p.get("text", "") for p in msg)
    return msg or ""


def _anthropic(cfg, system, prompt, images, want_json, max_tokens):
    import anthropic

    client = anthropic.Anthropic(api_key=cfg.get("api_key") or None, base_url=cfg["base_url"].rstrip("/"),
                                 timeout=900.0)
    content = [{"type": "image", "source": {"type": "base64", "media_type": _mime(i), "data": _b64(i)}}
               for i in images] + [{"type": "text", "text": prompt}]
    kwargs = dict(model=cfg["model"], max_tokens=max_tokens, system=system,
                  messages=[{"role": "user", "content": content}])
    if cfg["model"] in _FALLBACK_MODELS:
        # If the model declines, the API retries on a fallback model in the same call.
        kwargs.update(betas=["server-side-fallback-2026-07-01"], extra_body={"fallbacks": "default"})
    try:
        # Streamed, so long answers (a whole tab) don't hit HTTP timeouts.
        with client.beta.messages.stream(**kwargs) as stream:
            resp = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise AIError("Claude rejected the API key.")
    except anthropic.NotFoundError:
        raise AIError(f"Claude has no model called {cfg['model']!r}.")
    except anthropic.RateLimitError:
        raise AIError("Claude rate limit hit. Try again in a minute.")
    except anthropic.APIStatusError as e:
        raise AIError(f"Claude API error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        raise AIError("Couldn't reach the Claude API.")
    if resp.stop_reason == "refusal":
        raise AIError("Claude declined this request.")
    if resp.stop_reason == "max_tokens":
        raise AIError("The answer was too long for one request. Try fewer pages at a time.")
    return "".join(b.text for b in resp.content if b.type == "text")


def _gemini(cfg, system, prompt, images, want_json, max_tokens):
    parts = [{"text": prompt}] + [{"inline_data": {"mime_type": _mime(i), "data": _b64(i)}} for i in images]
    body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0}}
    if want_json:
        body["generationConfig"]["responseMimeType"] = "application/json"
    model = cfg["model"].removeprefix("models/")
    r = httpx.post(f"{cfg['base_url'].rstrip('/')}/v1beta/models/{model}:generateContent", json=body,
                   headers={"x-goog-api-key": cfg.get("api_key", "")}, timeout=TIMEOUT)
    d = _check(r, "Gemini")
    cands = d.get("candidates") or []
    if not cands:
        raise AIError(f"Gemini returned no answer ({d.get('promptFeedback', {}).get('blockReason', 'no reason given')})")
    return "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))


_CALL = {"ollama": _ollama, "openai": _openai, "anthropic": _anthropic, "gemini": _gemini}


def ask(cfg: dict, system: str, prompt: str, images: list[bytes] | None = None, want_json: bool = False,
        max_tokens: int = 16000) -> str:
    if not configured(cfg):
        if cfg and cfg.get("model") and not cfg.get("api_key"):
            raise AIError("The AI endpoint has no API key saved. Add it on the Settings page.")
        raise AIError("No AI endpoint is set up. Add one on the Settings page.")
    try:
        return _CALL[cfg["provider"]](cfg, system, prompt, images or [], want_json, max_tokens)
    except httpx.HTTPError as e:
        raise AIError(f"Couldn't reach {cfg['base_url']}: {e}") from e
    except (KeyError, IndexError, TypeError) as e:
        raise AIError(f"Unexpected answer from the AI endpoint ({e})") from e


def parse_json(text: str):
    """The first JSON object or array in a reply (models like to wrap it in ```json fences or prose)."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except ValueError:
        pass
    for open_, close in (("{", "}"), ("[", "]")):
        a, b = text.find(open_), text.rfind(close)
        if a != -1 and b > a:
            try:
                return json.loads(text[a:b + 1])
            except ValueError:
                continue
    raise AIError("The AI's answer wasn't valid JSON: " + text[:200])


def ask_json(cfg: dict, system: str, prompt: str, images: list[bytes] | None = None, max_tokens: int = 16000):
    return parse_json(ask(cfg, system, prompt + "\n\nAnswer with JSON only.", images, want_json=True,
                          max_tokens=max_tokens))


def list_models(cfg: dict) -> list[str]:
    base = cfg.get("base_url", "").rstrip("/")
    p = cfg.get("provider")
    try:
        if p == "ollama":
            d = _check(httpx.get(base + "/api/tags", headers=_bearer(cfg), timeout=30), "Ollama")
            # Ollama lists what each model can do; a model that can't chat ("completion") is no use here.
            return sorted(f"{name} · vision" if "vision" in caps else name
                          for m in d.get("models", [])
                          if (name := m.get("name") or m.get("model"))
                          and "completion" in (caps := m.get("capabilities") or ["completion"]))
        if p == "openai":
            d = _check(httpx.get(base + "/models", headers=_bearer(cfg), timeout=30), "The AI endpoint")
            return sorted(m["id"] for m in d.get("data", []))
        if p == "anthropic":
            import anthropic

            client = anthropic.Anthropic(api_key=cfg.get("api_key") or None, base_url=base, timeout=30.0)
            return [m.id for m in client.models.list()]
        if p == "gemini":
            d = _check(httpx.get(base + "/v1beta/models", headers={"x-goog-api-key": cfg.get("api_key", "")},
                                 params={"pageSize": 200}, timeout=30), "Gemini")
            return sorted(m["name"].removeprefix("models/") for m in d.get("models", [])
                          if "generateContent" in m.get("supportedGenerationMethods", []))
    except httpx.HTTPError as e:
        raise AIError(f"Couldn't reach {base}: {e}") from e
    except Exception as e:  # anthropic SDK errors
        if isinstance(e, AIError):
            raise
        raise AIError(str(e)) from e
    raise AIError("Pick a provider first.")
