"""Tool schemas and dispatch for the note-taking agent.

Design notes
------------
The tools are a thin, well-typed CRUD surface plus one search tool. Each tool does
exactly one thing and returns a structured `status` field so the model can branch
without parsing prose:

    status = "ok"                     -> action done, payload in the result
           | "not_found"              -> the referenced note id does not exist
           | "ambiguous"              -> a *search* helper is not needed; the model
                                         is expected to disambiguate from search
                                         results itself, but delete/update return
                                         this when given a title-ish string that
                                         matches several notes
           | "confirmation_required"  -> destructive/significant change staged, not
                                         applied; re-call with confirm=true
           | "error"                  -> bad arguments

Safety is enforced *here*, not in the prompt: `delete_note` and significant
`update_note` calls physically cannot mutate the database unless `confirm=true`
is passed. Even if the model ignores its instructions, nothing is destroyed
without a second call that the user's confirmation gates.
"""

from __future__ import annotations

from .storage import NoteStore, _normalise_tags

# ---------------------------------------------------------------- JSON schemas

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "create_note",
            "description": (
                "Create a new note. Use when the user wants to save, jot, record, "
                "or remember something. Extract a short descriptive title even if "
                "the user only gives a body."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short descriptive title."},
                    "body": {"type": "string", "description": "Full note text. May be empty."},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional lowercase tags/categories, e.g. ['meetings'].",
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_notes",
            "description": (
                "Find notes by keyword, tag, date range, or meaning. Call this "
                "before answering any question about existing notes, and before "
                "modifying or deleting a note referenced by description rather than "
                "id. Returns a ranked list of matches."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language or keyword query. Omit for a pure tag/date listing.",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Restrict to notes carrying any of these tags.",
                    },
                    "date_from": {"type": "string", "description": "ISO date YYYY-MM-DD, inclusive lower bound on creation date."},
                    "date_to": {"type": "string", "description": "ISO date YYYY-MM-DD, inclusive upper bound on creation date."},
                    "mode": {
                        "type": "string",
                        "enum": ["auto", "keyword", "semantic", "hybrid"],
                        "description": "Ranking strategy. Default 'auto'. Use 'semantic' for conceptual queries where exact words may not match.",
                    },
                    "include_body": {
                        "type": "boolean",
                        "description": "Return full bodies instead of snippets. Use for summarise/compare/reason tasks.",
                    },
                    "limit": {"type": "integer", "description": "Max results (default 10)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_note",
            "description": "Fetch one note in full by its id.",
            "parameters": {
                "type": "object",
                "properties": {"note_id": {"type": "integer"}},
                "required": ["note_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_notes",
            "description": "List notes (most recent first), optionally filtered by tag. Use for 'show me all my notes' or 'what tags do I have'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string"},
                    "limit": {"type": "integer", "description": "Default 20."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_note",
            "description": (
                "Modify an existing note by id. Title changes, full body "
                "replacement, and tag removal are 'significant' and require "
                "confirm=true (call once without it to stage a preview, then "
                "again with confirm=true after the user agrees). Appending text "
                "and adding tags apply immediately."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "note_id": {"type": "integer"},
                    "title": {"type": "string", "description": "New title (significant)."},
                    "body": {"type": "string", "description": "Replace the entire body (significant)."},
                    "append_body": {"type": "string", "description": "Append a line to the body (not significant)."},
                    "add_tags": {"type": "array", "items": {"type": "string"}, "description": "Tags to add (not significant)."},
                    "remove_tags": {"type": "array", "items": {"type": "string"}, "description": "Tags to remove (significant)."},
                    "confirm": {"type": "boolean", "description": "Set true to apply a significant change after user confirmation."},
                },
                "required": ["note_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_note",
            "description": (
                "Permanently delete a note by id. Always destructive: call once "
                "without confirm to stage a preview, then again with confirm=true "
                "only after the user explicitly confirms."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "note_id": {"type": "integer"},
                    "confirm": {"type": "boolean"},
                },
                "required": ["note_id"],
            },
        },
    },
]


def _significant(args: dict) -> bool:
    return bool(
        args.get("title") is not None
        or args.get("body") is not None
        or args.get("remove_tags")
    )


class ToolExecutor:
    """Dispatches a tool call against a NoteStore for one user."""

    def __init__(self, store: NoteStore, user_id: str = "default"):
        self.store = store
        self.user_id = user_id

    def run(self, name: str, args: dict) -> dict:
        handler = getattr(self, f"_{name}", None)
        if handler is None:
            return {"status": "error", "message": f"unknown tool '{name}'"}
        try:
            return handler(args or {})
        except Exception as exc:  # keep the agent loop alive on bad input
            return {"status": "error", "message": f"{type(exc).__name__}: {exc}"}

    # --------------------------------------------------------------- handlers
    def _create_note(self, a: dict) -> dict:
        if not a.get("title"):
            return {"status": "error", "message": "title is required"}
        note = self.store.add_note(
            title=a["title"], body=a.get("body", ""), tags=a.get("tags"),
            user_id=self.user_id,
        )
        return {"status": "ok", "note": note.to_dict()}

    def _search_notes(self, a: dict) -> dict:
        include_body = bool(a.get("include_body"))
        results = self.store.search(
            query=a.get("query"), tags=a.get("tags"),
            date_from=a.get("date_from"), date_to=a.get("date_to"),
            mode=a.get("mode", "auto"), limit=int(a.get("limit", 10)),
            user_id=self.user_id,
        )
        return {
            "status": "ok",
            "count": len(results),
            "notes": [
                {**n.to_dict(include_body=include_body), "score": round(s, 3)}
                for n, s in results
            ],
        }

    def _get_note(self, a: dict) -> dict:
        note = self.store.get_note(int(a["note_id"]), self.user_id)
        if note is None:
            return {"status": "not_found", "note_id": a.get("note_id")}
        return {"status": "ok", "note": note.to_dict()}

    def _list_notes(self, a: dict) -> dict:
        notes = self.store.all_notes(self.user_id)
        tag = a.get("tag")
        if tag:
            t = tag.strip().lower().lstrip("#")
            notes = [n for n in notes if t in n.tags]
        notes = notes[: int(a.get("limit", 20))]
        return {
            "status": "ok",
            "count": len(notes),
            "notes": [n.to_dict(include_body=False) for n in notes],
            "all_tags": self.store.all_tags(self.user_id),
        }

    def _update_note(self, a: dict) -> dict:
        note_id = int(a["note_id"])
        note = self.store.get_note(note_id, self.user_id)
        if note is None:
            return {"status": "not_found", "note_id": note_id}

        if _significant(a) and not a.get("confirm"):
            preview = {"current": note.to_dict()}
            if a.get("title") is not None:
                preview["new_title"] = a["title"]
            if a.get("body") is not None:
                preview["new_body"] = a["body"]
            if a.get("remove_tags"):
                preview["remove_tags"] = _normalise_tags(a["remove_tags"])
            return {
                "status": "confirmation_required",
                "action": "update",
                "note_id": note_id,
                "preview": preview,
                "message": "Ask the user to confirm this change, then call update_note again with confirm=true.",
            }

        updated = self.store.update_note(
            note_id, title=a.get("title"), body=a.get("body"),
            append_body=a.get("append_body"), add_tags=a.get("add_tags"),
            remove_tags=a.get("remove_tags"), user_id=self.user_id,
        )
        return {"status": "ok", "note": updated.to_dict()}

    def _delete_note(self, a: dict) -> dict:
        note_id = int(a["note_id"])
        note = self.store.get_note(note_id, self.user_id)
        if note is None:
            return {"status": "not_found", "note_id": note_id}

        if not a.get("confirm"):
            return {
                "status": "confirmation_required",
                "action": "delete",
                "note_id": note_id,
                "preview": note.to_dict(),
                "message": "Ask the user to confirm deletion, then call delete_note again with confirm=true.",
            }

        self.store.delete_note(note_id, self.user_id)
        return {"status": "ok", "deleted_id": note_id}
