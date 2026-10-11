"""One function: ask an AI model for JSON. Works with Claude or any OpenAI-compatible API."""
import json
import re

import httpx

from . import config


class LLMUnavailable(Exception):
    pass


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            raise ValueError("model did not return JSON")
        return json.loads(m.group(0))


def complete_json(system: str, user: str) -> dict:
    """Returns a dict. Raises LLMUnavailable if no AI is configured, or another error if the call fails."""
    if not config.llm_enabled():
        raise LLMUnavailable("no AI provider configured")
    if config.LLM_PROVIDER == "anthropic":
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": config.ANTHROPIC_API_KEY,
                     "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": config.ANTHROPIC_MODEL, "max_tokens": 900, "system": system,
                  "messages": [{"role": "user", "content": user}]},
            timeout=60,
        )
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")
    else:  # openai-compatible
        r = httpx.post(
            f"{config.LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
            json={"model": config.LLM_MODEL, "temperature": 0.3,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
            timeout=60,
        )
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
    return _extract_json(text)
