"""Thin wrapper around the Groq chat-completions API.

Kept deliberately small so the agent logic in `agent.py` is provider-agnostic:
anything with an OpenAI-style `chat.completions.create` and tool-calling could be
swapped in here.
"""

from __future__ import annotations

import os

DEFAULT_MODEL = "openai/gpt-oss-120b"  # strong tool-use support on Groq


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

    def complete(self, messages: list[dict], tools: list[dict] | None = None):
        """Return the raw assistant message object from one API round-trip."""
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message
