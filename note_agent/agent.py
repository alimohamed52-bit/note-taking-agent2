"""The conversational agent: message history + tool-calling loop.

`NoteAgent.send(user_text)` runs one user turn to completion, which may involve
several tool calls, and returns an `AgentTurn` with the final reply plus a
record of every tool call made (used by the CLI, the UI, and the eval harness).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

from .llm import GroqLLM
from .storage import NoteStore
from .tools import TOOL_SCHEMAS, ToolExecutor

MAX_TOOL_ITERATIONS = 6  # guard against loops

SYSTEM_PROMPT = """You are a note-taking assistant. The user manages personal \
notes entirely by chatting with you. Today's date is {today}.

You have tools: create_note, search_notes, get_note, list_notes, update_note, \
delete_note. Always use a tool to read or change notes — never invent note \
contents or ids.

Rules:
1. DISAMBIGUATION. Before updating or deleting a note that the user referred to \
by description, call search_notes. If more than one note plausibly matches, list \
the candidates and ask which one — do NOT guess.
2. CONFIRMATION. Deleting a note, or significantly changing one (new title, \
replacing the body, removing tags), requires explicit user confirmation. The \
tool will return status "confirmation_required" with a preview; relay it, wait \
for a clear yes, then call the tool again with confirm=true. If the user \
declines, drop it and confirm nothing changed.
3. DATES. Convert relative dates ("last week", "yesterday") to YYYY-MM-DD using \
today's date before calling search_notes.
4. FOLLOW-UPS. Resolve references like "that note" / "the second one" from the \
conversation so far. Deadlines, dates, and extra details go in the note body \
(use append_body) unless the user explicitly says "tag".
5. EMPTY RESULTS. If a search returns nothing, say so plainly and suggest an \
alternative (broaden the query, try a different tag, list all notes).
6. Be concise and conversational. Confirm what you did, including the note id.
7. For summarise / compare / contradiction questions, call search_notes with \
include_body=true and reason over the returned notes."""


@dataclass
class ToolCallRecord:
    name: str
    arguments: dict
    result: dict


@dataclass
class AgentTurn:
    reply: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)

    def called(self, name: str) -> list[ToolCallRecord]:
        return [tc for tc in self.tool_calls if tc.name == name]


class NoteAgent:
    def __init__(self, store: NoteStore, llm: GroqLLM | None = None,
                 user_id: str = "default"):
        self.executor = ToolExecutor(store, user_id)
        self.llm = llm or GroqLLM()
        self.messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT.format(today=date.today().isoformat())}
        ]

    def send(self, user_text: str) -> AgentTurn:
        self.messages.append({"role": "user", "content": user_text})
        made: list[ToolCallRecord] = []

        for _ in range(MAX_TOOL_ITERATIONS):
            msg = self.llm.complete(self.messages, tools=TOOL_SCHEMAS)
            self.messages.append(self._msg_to_dict(msg))

            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                return AgentTurn(reply=msg.content or "", tool_calls=made)

            for call in tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = self.executor.run(call.function.name, args)
                made.append(ToolCallRecord(call.function.name, args, result))
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result),
                })

        # Ran out of iterations — ask the model to wrap up in plain language.
        self.messages.append({
            "role": "user",
            "content": "Summarise the outcome for me in plain language now, without calling more tools.",
        })
        msg = self.llm.complete(self.messages)
        self.messages.append({"role": "assistant", "content": msg.content or ""})
        return AgentTurn(reply=msg.content or "", tool_calls=made)

    @staticmethod
    def _msg_to_dict(msg) -> dict:
        """Normalise the SDK message object into a plain dict for the history."""
        out: dict = {"role": "assistant", "content": msg.content or ""}
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            out["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {
                        "name": c.function.name,
                        "arguments": c.function.arguments or "{}",
                    },
                }
                for c in tool_calls
            ]
        return out
