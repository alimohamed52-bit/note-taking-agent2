"""Thin wrapper around the Groq chat-completions API.

Kept deliberately small so the agent logic in `agent.py` is provider-agnostic:
anything with an OpenAI-style `chat.completions.create` and tool-calling could be
swapped in here.
"""

from __future__ import annotations

import os
import re
import time

DEFAULT_MODEL = "openai/gpt-oss-120b"  # strong tool-use support on Groq


class RateLimited(RuntimeError):
    """Raised when Groq's rate limit is hit and a short retry won't clear it
    (e.g. the per-day token quota). Carries a human-readable hint."""


class GroqLLM:
    def __init__(self, model: str | None = None, api_key: str | None = None,
                 temperature: float = 0.2):
        from groq import Groq

        key = api_key or os.environ.get("GROQ_API_KEY")
        if not key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Put it in a .env file or your environment."
            )
        self.client = Groq(api_key=key)
        self.model = model or os.environ.get("GROQ_MODEL", DEFAULT_MODEL)
        self.temperature = temperature

    # If Groq says "try again in Ns" and N is at least this small, we wait it out
    # once; anything longer (a daily-quota block) is surfaced to the caller.
    _MAX_AUTO_WAIT_S = 20

    def complete(self, messages: list[dict], tools: list[dict] | None = None):
        """Return the raw assistant message object from one API round-trip."""
        from groq import RateLimitError

        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        for attempt in range(3):
            try:
                response = self.client.chat.completions.create(**kwargs)
                return response.choices[0].message
            except RateLimitError as exc:
                wait = _retry_after_seconds(exc)
                if wait is not None and wait <= self._MAX_AUTO_WAIT_S and attempt < 2:
                    time.sleep(wait + 1)
                    continue
                raise RateLimited(
                    "Groq rate limit hit"
                    + (f" — retry in ~{round(wait)}s." if wait else ".")
                    + " This is usually the free-tier daily token cap; wait for it "
                    "to reset, switch models with GROQ_MODEL, or upgrade at "
                    "https://console.groq.com/settings/billing"
                ) from exc


def _retry_after_seconds(exc: Exception) -> float | None:
    """Pull the 'try again in 2m44.5s' hint out of a Groq rate-limit error."""
    m = re.search(r"try again in ((?:\d+m)?[\d.]+s)", str(exc))
    if not m:
        return None
    text = m.group(1)
    mins = re.search(r"(\d+)m", text)
    secs = re.search(r"([\d.]+)s", text)
    return (int(mins.group(1)) * 60 if mins else 0) + (float(secs.group(1)) if secs else 0)
