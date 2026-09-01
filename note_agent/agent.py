"""The conversational agent: message history + tool-calling loop.

`NoteAgent.send(user_text)` runs one user turn to completion, which may involve
several tool calls, and returns an `AgentTurn` with the final reply plus a
record of every tool call made (used by the CLI, the UI, and the eval harness).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta

from .llm import GroqLLM
from .storage import NoteStore
from .tools import TOOL_SCHEMAS, ToolExecutor

MAX_TOOL_ITERATIONS = 6  # guard against loops

SYSTEM_PROMPT = """You are a note-taking assistant. The user manages personal \
notes entirely by chatting with you.

{date_context}

You have tools: create_note, search_notes, get_note, list_notes, update_note, \
delete_note. Always use a tool to read or change notes — never invent note \
contents or ids.

Rules:
0. TITLE & TAGS. When creating a note, infer a short descriptive title and 1-3 \
concise lowercase tags (e.g. work, meetings, travel, finance, personal) from the \
content. If any existing tag fits, reuse it instead of coining a new one: \
{known_tags}. If the user names tags explicitly, use exactly those. Do NOT ask \
permission for the title or tags — create the note, then state the title and \
tags you chose so the user can correct them afterwards. (The date is the one \
thing you must ask about when missing — see rule 3a.)
1. DISAMBIGUATION. Before updating or deleting a note that the user referred to \
by description, call search_notes. If more than one note plausibly matches, list \
the candidates and ask which one — do NOT guess.
2. CONFIRMATION. Deleting a note, or significantly changing one (new title, \
replacing the body, removing tags), requires explicit user confirmation. The \
tool will return status "confirmation_required" with a preview; relay it, wait \
for a clear yes, then call the tool again with confirm=true. If the user \
declines, drop it and confirm nothing changed.
3. DATES. Never compute weekdays or date arithmetic yourself — you get it wrong. \
Use ONLY the calendar above to turn "Tuesday" / "next week" / "yesterday" into a \
YYYY-MM-DD date, for both note bodies and search_notes filters.
3a. EVERY NOTE NEEDS A DATE. create_note requires `date`. If the user stated or \
implied one ("today", "tomorrow", "on Friday", "next week", an explicit date), \
resolve it from the calendar and pass it. If they gave NO date at all, ask "What \
date is this note for?" and wait — do not assume today, do not call create_note \
yet. Notes may only be dated today or later; if a resolved date is in the past, \
tell the user and ask for a valid one.
4. FOLLOW-UPS. Resolve references like "that note" / "the second one" from the \
conversation so far. Deadlines, dates, and extra details go in the note body \
(use append_body) unless the user explicitly says "tag".
5. EMPTY RESULTS. If a search returns nothing, say so plainly and suggest an \
alternative (broaden the query, try a different tag, list all notes).
5a. AUTO-CLEANUP. Notes are automatically deleted once their date is in the \
past — only today's and future notes are kept. If the user asks where an old \
note went, explain this. This cleanup is automatic; you never call delete_note \
for it.
6. Be concise and conversational. Confirm what you did, including the note id.
7. For summarise / compare / contradiction questions, call search_notes with \
include_body=true and reason over the returned notes."""


def _date_context() -> str:
    """A small explicit calendar so the model never does weekday math itself."""
    today = date.today()
    lines = [
        f"Today is {today:%A}, {today.isoformat()}.",
        f"Yesterday was {today - timedelta(days=1):%A}, {(today - timedelta(days=1)).isoformat()}.",
        f"One week ago was {(today - timedelta(days=7)).isoformat()}.",
        "The next 7 days (use these for a bare weekday name like \"Tuesday\"):",
    ]
    for i in range(1, 8):
        d = today + timedelta(days=i)
        lines.append(f"  {d:%A} = {d.isoformat()}")
    lines.append('"next <weekday>" means the following week\'s occurrence '
                 '(add 7 days to the one listed above).')
    return "\n".join(lines)


@dataclass
class ToolCallRecord:
    name: str
    arguments: dict
    result: dict


@dataclass
class AgentTurn:
    reply: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    purged: list = field(default_factory=list)  # notes auto-removed because their date passed

    def called(self, name: str) -> list[ToolCallRecord]:
        return [tc for tc in self.tool_calls if tc.name == name]


class NoteAgent:
    def __init__(self, store: NoteStore, llm: GroqLLM | None = None,
                 user_id: str = "default"):
        self.executor = ToolExecutor(store, user_id)
        self.llm = llm or GroqLLM()
        known = store.all_tags(user_id)
        prompt = SYSTEM_PROMPT.format(
            date_context=_date_context(),
            known_tags=", ".join(known) if known else "(none yet)",
        )
        self.messages: list[dict] = [{"role": "system", "content": prompt}]
        # Sweep out notes whose date already passed, once, when the agent starts.
        self.purged_on_start = self.executor.store.purge_past_notes()

    def _purge(self) -> list:
        """Remove notes whose event_date is now in the past; tell the model so it
        can mention it if relevant."""
        purged = self.executor.store.purge_past_notes()
        if purged:
            listing = "; ".join(f'#{n.id} "{n.title}" ({n.event_date})' for n in purged)
            self.messages.append({
                "role": "system",
                "content": (f"{len(purged)} note(s) were automatically removed because "
                            f"their date has passed: {listing}. Mention this to the user "
                            f"if it is relevant to their message."),
            })
        return purged

    def send(self, user_text: str) -> AgentTurn:
        purged = self._purge()
        self.messages.append({"role": "user", "content": user_text})
        made: list[ToolCallRecord] = []

        for _ in range(MAX_TOOL_ITERATIONS):
            msg = self.llm.complete(self.messages, tools=TOOL_SCHEMAS)
            self.messages.append(self._msg_to_dict(msg))

            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                return AgentTurn(reply=msg.content or "", tool_calls=made, purged=purged)

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
        return AgentTurn(reply=msg.content or "", tool_calls=made, purged=purged)

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
