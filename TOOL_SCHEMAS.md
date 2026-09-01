# Tool Schema Documentation

The agent is given six tools. The machine-readable JSON Schemas live in
[`note_agent/tools.py`](note_agent/tools.py) (`TOOL_SCHEMAS`); this document is the
human-readable contract.

## Design rationale

- **One tool per action, plus one search tool.** CRUD verbs map 1:1 to
  `create_note` / `get_note` / `update_note` / `delete_note`. Reading is split
  into `search_notes` (ranked, fuzzy, filtered) and `list_notes` (plain
  enumeration) because "what did I say about X" and "show me all my notes" are
  different intents with different return shapes.
- **Structured `status` on every result.** The model branches on a field, not on
  prose: `ok`, `not_found`, `confirmation_required`, `error`.
- **Safety lives in the tool, not the prompt.** `delete_note` and *significant*
  `update_note` calls cannot mutate the database unless `confirm=true` is passed.
  The first call stages a preview; a second call applies it. Even a
  misbehaving model cannot destroy data without a second, user-gated call.
- **Confirmation is scoped to real risk.** Appending text or adding a tag is
  reversible and low-stakes, so it applies immediately. Replacing the body,
  changing the title, or removing tags is "significant" and gated.
- **Every note is dated, and never in the past.** `create_note` requires
  `date`; a missing date returns `date_required` (the agent asks the user) and a
  past date returns `error`. Enforced in the tool, so the rule holds even if the
  model forgets it. The agent resolves relative dates ("Tuesday", "next week")
  from an explicit calendar injected into its system prompt.

## Common return fields

| Field | Type | Meaning |
|---|---|---|
| `status` | string | `ok` · `not_found` · `confirmation_required` · `date_required` · `error` |
| `note` | object | `{id, title, body, tags[], event_date, created_at, updated_at}` |
| `message` | string | present on `error` / `confirmation_required` / `date_required` |

Timestamps are UTC ISO-8601. `event_date` is the `YYYY-MM-DD` the note is *for*
(distinct from `created_at`). Tags are always stored lowercase, de-duplicated,
`#` stripped.

---

## `create_note`

Create a new note.

| Param | Type | Req | Notes |
|---|---|---|---|
| `title` | string | ✓ | Short descriptive title. The model infers one if the user only dictates a body. |
| `date` | string | ✓ | `YYYY-MM-DD` the note is for, resolved from the calendar in the system prompt. Must be **today or later**. |
| `body` | string | | Full text. May be empty. |
| `tags` | string[] | | Categories/labels. The agent infers 1–3 from content when the user gives none. |

**Returns:**
- `{status: "ok", note}` on success
- `{status: "date_required", message}` if `date` is missing — the agent must ask the user for one
- `{status: "error", message}` if `date` is malformed or in the past

## `search_notes`

Rank notes by keyword, tag, date, and/or meaning. Call before answering any
question about existing notes and before acting on a note referenced by
description.

| Param | Type | Req | Notes |
|---|---|---|---|
| `query` | string | | NL or keyword text. Omit for a pure tag/date listing. |
| `tags` | string[] | | Keep notes carrying **any** of these tags. |
| `date_from` | string | | `YYYY-MM-DD`, inclusive, on creation date. |
| `date_to` | string | | `YYYY-MM-DD`, inclusive. |
| `mode` | string | | `auto` (default) · `keyword` · `semantic` · `hybrid`. `auto` = hybrid when embeddings are available, else keyword. |
| `include_body` | boolean | | Return full bodies instead of 200-char snippets (for summarise/compare). |
| `limit` | integer | | Default 10. |

**Returns:** `{status: "ok", count, notes: [{id, title, snippet|body, tags, created_at, score}]}`
ordered best-first. `score` is the (normalised) rank score.

## `get_note`

Fetch one note in full.

| Param | Type | Req |
|---|---|---|
| `note_id` | integer | ✓ |

**Returns:** `{status: "ok", note}` or `{status: "not_found", note_id}`.

## `list_notes`

Enumerate notes, newest first, optionally by tag.

| Param | Type | Req | Notes |
|---|---|---|---|
| `tag` | string | | Exact tag filter. |
| `limit` | integer | | Default 20. |

**Returns:** `{status: "ok", count, notes: [{id, title, snippet, tags, ...}], all_tags: [...]}`.

## `update_note`

Modify a note by id.

| Param | Type | Req | Significant? |
|---|---|---|---|
| `note_id` | integer | ✓ | — |
| `title` | string | | **yes** |
| `body` | string | | **yes** (replaces whole body) |
| `append_body` | string | | no (adds a line) |
| `add_tags` | string[] | | no |
| `remove_tags` | string[] | | **yes** |
| `date` | string | | no — set/replace `event_date` (`YYYY-MM-DD`, today or later) |
| `confirm` | boolean | | set `true` to apply a significant change |

**Returns:**
- non-significant change → `{status: "ok", note}` (applied)
- significant change, no `confirm` → `{status: "confirmation_required", action: "update", preview: {current, new_title?, new_body?, remove_tags?}}`
- significant change, `confirm: true` → `{status: "ok", note}`
- bad id → `{status: "not_found"}`

## `delete_note`

Permanently delete a note. Always destructive.

| Param | Type | Req |
|---|---|---|
| `note_id` | integer | ✓ |
| `confirm` | boolean | set `true` only after explicit user confirmation |

**Returns:**
- no `confirm` → `{status: "confirmation_required", action: "delete", preview: note}`
- `confirm: true` → `{status: "ok", deleted_id}`
- bad id → `{status: "not_found"}`
