# Conversational Note-Taking Agent

A chat-based system for managing personal notes entirely through natural
language. Add, search, modify, delete, and reason over notes by talking to it —
from a terminal or a small web page.

```
you › Save a note about the team standup — we moved it to Tuesdays, tag it meetings.
      · create_note(title='Team standup', body='Standup moved to Tuesdays.', tags=['meetings']) -> ok
agent › Saved note #1 "Team standup" tagged #meetings.

you › actually add that the new time is 10am to that note
      · update_note(note_id=1, append_body='New time: 10am.') -> ok
agent › Updated note #1 — added "New time: 10am." to the body.

you › delete the standup note
      · delete_note(note_id=1) -> confirmation_required
agent › You want to delete note #1 "Team standup"? That can't be undone. Confirm?

you › yes
      · delete_note(note_id=1, confirm=True) -> ok
agent › Deleted note #1.
```

---

## Setup

Requires **Python 3.10+**.

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env        # then edit .env and add your GROQ_API_KEY
```

### Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GROQ_API_KEY` | **yes** | — | Groq API key (get one at <https://console.groq.com>). |
| `GROQ_MODEL` | no | `openai/gpt-oss-120b` | Any Groq model with tool-calling support. |

The `.env` file is loaded automatically.

---

## Running

### CLI (chat loop)

```bash
python -m note_agent.cli
# options: --user alice   --db mynotes.db   --model openai/gpt-oss-120b
```

In-chat commands: `/notes` (list), `/reset` (new conversation), `/quit`.
Each turn prints the tool calls it made (indented `·` lines) so you can see the
agent's reasoning without reading the source.

### Web UI (Streamlit)

```bash
streamlit run streamlit_app.py
```

Opens a browser chat window. The sidebar shows your notes live and lets you
switch user id / database file. Same agent as the CLI — this file is only UI.

### Web app (Flask) — chat + live notes panel

```bash
python -m note_agent.web
# options: --db demo.db  --user alice  --host 0.0.0.0  --port 8000
```

Then open **<http://127.0.0.1:5000>**. Chat with the agent in the browser with a
notes panel that updates after every turn. Also serves a per-note page
(`/note/<id>`) and a JSON feed (`/api/notes`). Same `NoteAgent` as the CLI; one
conversation per server process, "Reset" starts a new one.

---

## Evaluation harness

15 scripted conversational scenarios (happy paths + edge cases) run against a
live agent, asserting on **which tool was called with which arguments**, the
**resulting database state**, and whether the agent **asked for clarification**
when it should have.

```bash
python -m eval.run                       # all scenarios
python -m eval.run --only delete         # subset
python -m eval.run --model openai/gpt-oss-120b
```

Output: a per-scenario PASS/FAIL line, an overall pass rate, a breakdown by
category (add / search / modify / delete / disambiguation / reasoning /
multi-turn / error-handling), and a full JSON trace at `eval/report.json`.

**How intent is measured.** "Did the agent understand the request?" is
operationalised as: did it call the *right tool* with the *right key arguments*,
and did the *world end up in the right state*? Wording is never asserted on
directly — only presence of expected substrings for question-answering
scenarios. See [`eval/checks.py`](eval/checks.py) for the check definitions and
[`eval/scenarios.py`](eval/scenarios.py) for the scenarios.

### Offline unit tests (no API key)

Storage and tool logic — including the confirmation gate and user isolation —
are tested without the LLM:

```bash
python tests/test_storage_and_tools.py      # or: python -m pytest
```

---

## Design decisions

### Architecture

```
note_agent/
  storage.py     SQLite persistence + search (keyword / semantic / hybrid)
  embeddings.py  local embedding model, degrades gracefully to keyword-only
  tools.py       6 tool JSON schemas + ToolExecutor (safety enforced here)
  llm.py         Groq chat wrapper (provider-agnostic seam)
  agent.py       message history + tool-calling loop + system prompt
  cli.py         terminal chat loop
  web.py         Flask web app: browser chat + live notes panel
streamlit_app.py web chat UI
eval/            scenario suite + runner + report
tests/           offline unit tests
```

The agent owns conversation history and the tool loop; `ToolExecutor` owns the
data and the safety rules. The LLM wrapper is a thin seam — swapping Groq for
another OpenAI-style provider touches one file.

### Persistence: SQLite

Notes are queried by structured facets (tag, date range, keyword), which map
directly to SQL and indexes — a JSON file would mean loading and filtering
everything in Python every turn. SQLite also gives atomic writes (the CLI and
Streamlit app can share a database), one-column `user_id` scoping, and cached
embedding blobs, while staying a single zero-setup file. The schema is one
table; see [`note_agent/storage.py`](note_agent/storage.py).

### Required behaviours

| Behaviour | How |
|---|---|
| **Intent disambiguation** | System prompt requires `search_notes` before acting on a described note; if >1 plausible match, the agent lists candidates and asks. Verified by `update_ambiguous_asks`, `delete_ambiguous_asks`. |
| **Confirmation on destructive actions** | Enforced in `tools.py`: `delete_note` / significant `update_note` return `confirmation_required` and cannot write without a second `confirm=true` call. Not prompt-dependent. |
| **Multi-turn awareness** | Full message history is replayed each turn; tool results (with ids) stay in context so "that note" / "the second one" resolve. Verified by `multiturn_append`. |
| **Graceful errors** | Empty searches return `count: 0`; the prompt requires saying so and suggesting an alternative. Tool exceptions are caught and returned as `status: "error"` so the loop survives. Verified by `search_empty_graceful`. |
| **Reasoning over notes** | `search_notes(include_body=true)` returns full bodies; the model summarises / compares / finds contradictions. Verified by `summarise_by_tag`, `detect_contradiction`. |

### LLM: Groq

Chosen for speed and free-tier access. The default model
`openai/gpt-oss-120b` has reliable tool-calling. Groq's model catalogue changes
often and varies by account — run `python -m note_agent.list_models` (or check
<https://console.groq.com/docs/models>) and set `GROQ_MODEL` to any chat model
your key can see. Per the brief, the model choice isn't the point — the
integration is isolated in [`note_agent/llm.py`](note_agent/llm.py).

### Bonus: semantic search

Groq has no embeddings endpoint, so notes are embedded **locally** with
[`fastembed`](https://github.com/qdrant/fastembed) using
**BAAI/bge-small-en-v1.5** (384-dim). Why this model: it runs on ONNX Runtime
(CPU, no PyTorch — small install), the download is ~130 MB, and it sits near the
top of the MTEB retrieval benchmark for its size class. Embeddings are computed
on write and cached as float32 blobs in the note row. `search_notes` supports
`keyword`, `semantic`, and `hybrid` (0.5/0.5 normalised blend) modes; `auto`
picks hybrid when the model is available. If `fastembed` isn't installed or the
model can't download, everything falls back to keyword search with a printed
notice — nothing breaks. Verified by `semantic_search`.

### Bonus: multi-user isolation

Every table row carries a `user_id`; every storage call is scoped by it. The CLI
takes `--user`, the Streamlit app has a user-id field. **Auth strategy (stubbed):**
the user id is currently taken on trust from the client. In a real deployment
you'd put an auth proxy in front (session cookie / OAuth / API key → verified
`user_id`), and the storage layer would need no changes — it already treats
`user_id` as the isolation boundary. Verified by `test_user_isolation`.

---

## Notes / limitations

- The agent relies on the model to re-call a tool with `confirm=true` after the
  user agrees. The *safety* guarantee (no silent writes) holds regardless; a
  model that never confirms just means the change doesn't happen.
- Semantic re-embedding is synchronous on write — fine for personal-scale note
  counts, not for bulk import.
- Conversation history grows unbounded within a session; a production build
  would window or summarise it.
