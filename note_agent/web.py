"""A local web app: chat with the note-taking agent in the browser, with a live
notes panel beside the conversation.

    python -m note_agent.web                       # http://127.0.0.1:5000
    python -m note_agent.web --db demo.db --user alice --port 8000

Needs GROQ_API_KEY (see README). Same NoteAgent as the CLI — this file only adds
an HTTP layer. One agent instance is shared for the process (local, single-user);
"Reset" starts a fresh conversation. Read-only browse routes (`/notes`,
`/note/<id>`, `/api/notes`) are also served for looking at stored notes directly.
"""

from __future__ import annotations

import argparse
import html
import threading

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, request

from .agent import NoteAgent
from .storage import NoteStore

app = Flask(__name__)
app.config["DB_PATH"] = "notes.db"
app.config["USER_ID"] = "default"

_agent: NoteAgent | None = None
_agent_error: str | None = None
_lock = threading.Lock()  # serialise agent + DB access across request threads


def _store() -> NoteStore:
    return NoteStore(app.config["DB_PATH"])


def _get_agent() -> NoteAgent | None:
    """Lazily build the shared agent; remember the error if it can't start."""
    global _agent, _agent_error
    if _agent is None and _agent_error is None:
        try:
            _agent = NoteAgent(_store(), user_id=app.config["USER_ID"])
        except Exception as exc:  # missing API key, bad model, ...
            _agent_error = str(exc)
    return _agent


def _notes_payload() -> list[dict]:
    store = _store()
    try:
        return [
            {"id": n.id, "title": n.title, "tags": n.tags, "body": n.body,
             "event_date": n.event_date,
             "created_at": n.created_at[:16].replace("T", " ")}
            for n in store.all_notes(app.config["USER_ID"])
        ]
    finally:
        store.close()


# --------------------------------------------------------------------- chat UI

INDEX_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Note-Taking Agent</title>
<style>
  :root {
    --bg:#f5f5f7; --panel:#fff; --text:#1a1a1a; --muted:#6b7280; --border:#e3e3e8;
    --accent:#2563eb; --user:#2563eb; --agent:#f0f0f3; --pill:#eef2ff; --pill-tx:#3730a3;
    --tool:#8a8f98;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#0f1115; --panel:#181b20; --text:#e8e8e8; --muted:#9aa0a6; --border:#2a2e37;
      --accent:#6ea8fe; --user:#2f6fed; --agent:#23262e; --pill:#23263a; --pill-tx:#b9c2ff;
      --tool:#787e88;
    }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
    font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  .app { display:flex; height:100vh; }
  .chat { flex:1; display:flex; flex-direction:column; min-width:0; }
  .side { width:300px; border-left:1px solid var(--border); background:var(--panel);
    overflow-y:auto; padding:16px; }
  header { padding:14px 20px; border-bottom:1px solid var(--border); background:var(--panel);
    display:flex; align-items:center; justify-content:space-between; }
  header h1 { font-size:1rem; margin:0; }
  header button { font-size:.8rem; padding:6px 12px; border:1px solid var(--border);
    background:transparent; color:var(--muted); border-radius:6px; cursor:pointer; }
  #log { flex:1; overflow-y:auto; padding:20px; display:flex; flex-direction:column; gap:14px; }
  .msg { max-width:680px; padding:10px 14px; border-radius:12px; white-space:pre-wrap;
    word-wrap:break-word; }
  .msg.user { align-self:flex-end; background:var(--user); color:#fff; }
  .msg.agent { align-self:flex-start; background:var(--agent); }
  .tools { align-self:flex-start; font-size:.75rem; color:var(--tool); font-family:
    ui-monospace,SFMono-Regular,Menlo,monospace; max-width:680px; }
  .composer { display:flex; gap:8px; padding:14px 20px; border-top:1px solid var(--border);
    background:var(--panel); }
  .composer input { flex:1; padding:11px 14px; border:1px solid var(--border);
    border-radius:8px; background:var(--bg); color:var(--text); font-size:1rem; }
  .composer button { padding:11px 18px; border:0; border-radius:8px; background:var(--accent);
    color:#fff; font-size:1rem; cursor:pointer; }
  .composer button:disabled { opacity:.5; cursor:default; }
  .side h2 { font-size:.8rem; text-transform:uppercase; letter-spacing:.04em;
    color:var(--muted); margin:0 0 10px; }
  .note { border:1px solid var(--border); border-radius:9px; padding:10px 12px; margin-bottom:9px; }
  .note b { display:block; font-size:.9rem; margin-bottom:3px; }
  .note .m { color:var(--muted); font-size:.72rem; }
  .pill { display:inline-block; padding:2px 8px; border-radius:999px; background:var(--pill);
    color:var(--pill-tx); font-size:.7rem; margin:3px 3px 0 0; }
  .empty { color:var(--muted); font-size:.85rem; }
  .banner { background:#7f1d1d; color:#fff; padding:8px 20px; font-size:.85rem; }
</style></head><body>
<div class="app">
  <div class="chat">
    <header>
      <h1>🗒️ Note-Taking Agent <span style="color:var(--muted);font-weight:400">· {user}</span></h1>
      <button onclick="resetChat()">Reset</button>
    </header>
    {banner}
    <div id="log"></div>
    <form class="composer" onsubmit="return send(event)">
      <input id="box" autocomplete="off" placeholder="Message your notes…" autofocus>
      <button id="btn" type="submit">Send</button>
    </form>
  </div>
  <aside class="side">
    <h2>Notes (<span id="count">0</span>)</h2>
    <div id="notes"></div>
  </aside>
</div>
<script>
const log = document.getElementById('log');
const box = document.getElementById('box');
const btn = document.getElementById('btn');

function bubble(cls, text) {
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.textContent = text;
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
  return d;
}
function toolLine(calls) {
  if (!calls.length) return;
  const d = document.createElement('div');
  d.className = 'tools';
  d.textContent = calls.map(c => `${c.name}(${JSON.stringify(c.args)}) → ${c.status}`).join('\\n');
  log.appendChild(d);
}
function renderNotes(notes) {
  document.getElementById('count').textContent = notes.length;
  const box = document.getElementById('notes');
  if (!notes.length) { box.innerHTML = '<div class="empty">No notes yet.</div>'; return; }
  box.innerHTML = notes.map(n =>
    `<div class="note"><b>#${n.id} ${escapeHtml(n.title)}</b>
     <div class="m">${n.event_date ? '📅 ' + escapeHtml(n.event_date) : n.created_at}</div>
     ${n.tags.map(t => `<span class="pill">${escapeHtml(t)}</span>`).join('')}</div>`
  ).join('');
}
function escapeHtml(s){return s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}

async function send(e) {
  e.preventDefault();
  const text = box.value.trim();
  if (!text) return false;
  bubble('user', text);
  box.value = '';
  btn.disabled = true;
  const thinking = bubble('agent', '…');
  try {
    const r = await fetch('/chat', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({message: text})});
    const data = await r.json();
    thinking.remove();
    if (data.error) { bubble('agent', '⚠️ ' + data.error); }
    else {
      toolLine(data.tool_calls || []);
      bubble('agent', data.reply || '(no reply)');
      renderNotes(data.notes || []);
    }
  } catch (err) {
    thinking.remove();
    bubble('agent', '⚠️ ' + err);
  }
  btn.disabled = false;
  box.focus();
  return false;
}
async function resetChat() {
  await fetch('/reset', {method:'POST'});
  log.innerHTML = '';
  loadNotes();
}
async function loadNotes() {
  const r = await fetch('/api/notes');
  renderNotes((await r.json()).notes.map(n => ({...n, created_at: (n.created_at||'').slice(0,16).replace('T',' ')})));
}
loadNotes();
</script>
</body></html>"""


@app.route("/")
def index():
    _get_agent()
    banner = f'<div class="banner">{html.escape(_agent_error)}</div>' if _agent_error else ""
    return INDEX_HTML.replace("{user}", html.escape(app.config["USER_ID"])) \
                     .replace("{banner}", banner)


@app.route("/chat", methods=["POST"])
def chat():
    message = (request.get_json(silent=True) or {}).get("message", "").strip()
    if not message:
        return jsonify(error="empty message"), 400

    with _lock:
        agent = _get_agent()
        if agent is None:
            return jsonify(error=_agent_error or "agent unavailable")
        try:
            turn = agent.send(message)
        except Exception as exc:
            return jsonify(error=f"{type(exc).__name__}: {exc}")
        tool_calls = [
            {"name": tc.name, "args": tc.arguments, "status": tc.result.get("status")}
            for tc in turn.tool_calls
        ]
        notes = _notes_payload()

    return jsonify(reply=turn.reply, tool_calls=tool_calls, notes=notes)


@app.route("/reset", methods=["POST"])
def reset():
    global _agent
    with _lock:
        if _agent is not None:
            _agent.executor.store.close()
        _agent = None
        _get_agent()
    return jsonify(ok=True)


# ----------------------------------------------------------- read-only browsing

@app.route("/api/notes")
def api_notes():
    store = _store()
    try:
        return jsonify(notes=[n.to_dict() for n in store.all_notes(app.config["USER_ID"])])
    finally:
        store.close()


@app.route("/note/<int:note_id>")
def note_page(note_id: int):
    store = _store()
    try:
        note = store.get_note(note_id, app.config["USER_ID"])
        if note is None:
            abort(404)
        tags = "".join(f'<span class="pill">{html.escape(t)}</span>' for t in note.tags)
        return f"""<!doctype html><meta charset=utf-8>
        <title>{html.escape(note.title)}</title>
        <body style="font-family:system-ui;max-width:680px;margin:40px auto;padding:0 20px">
        <a href="/">← chat</a><h1>{html.escape(note.title)}</h1>
        <p style="color:#888">#{note.id}{" · 📅 " + html.escape(note.event_date) if note.event_date else ""} · created {note.created_at[:16].replace("T", " ")}</p>
        <p>{tags}</p><pre style="white-space:pre-wrap;font:inherit">{html.escape(note.body)}</pre>
        </body>"""
    finally:
        store.close()


def main(argv=None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Chat with your notes in the browser.")
    ap.add_argument("--db", default="notes.db")
    ap.add_argument("--user", default="default")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    args = ap.parse_args(argv)

    app.config["DB_PATH"] = args.db
    app.config["USER_ID"] = args.user
    url = f"http://{args.host}:{args.port}"
    print(f"\n  Note-Taking Agent web UI  →  {url}\n  (db: {args.db}, user: {args.user})\n")
    # threaded=False keeps SQLite access single-threaded; the lock is a belt-and-braces.
    app.run(host=args.host, port=args.port, debug=False, threaded=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
