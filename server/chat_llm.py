"""Groq JSON completions for the patient-scoped chat pipeline."""

from __future__ import annotations

import json
import os
import re

import httpx
from dotenv import load_dotenv


load_dotenv()

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


def complete_json(*, model: str, system: str, user: str, schema: dict, name: str) -> dict:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set.")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": name, "strict": False, "schema": schema},
        },
    }
    try:
        response = httpx.post(
            GROQ_CHAT_URL,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60,
        )
        response.raise_for_status()
        result = response.json()
    except httpx.HTTPStatusError as exc:
        try:
            error = exc.response.json()
            code = error.get("error", {}).get("code") or error.get("error_code")
        except (ValueError, AttributeError, TypeError):
            code = None
        detail = f", {code}" if isinstance(code, str) and re.fullmatch(r"[\w-]{1,80}", code) else ""
        raise RuntimeError(f"Groq chat request failed (HTTP {exc.response.status_code}{detail}).") from exc
    except httpx.RequestError as exc:
        raise RuntimeError("Groq chat service is unavailable.") from exc

    try:
        content = result["choices"][0]["message"]["content"]
        if not content:
            raise ValueError("empty content")
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("response is not an object")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError("Groq chat returned an invalid JSON response.") from exc
    return parsed
