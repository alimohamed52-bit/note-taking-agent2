"""A minimal read-only website for browsing notes.

    python -m note_agent.web            # http://127.0.0.1:5000
    python -m note_agent.web --db demo.db --user alice --port 8000

Creating, editing and deleting notes is the agent's job (CLI / Streamlit chat);
this site is just a fast way to look at what's stored — list, full-text search,
tag filter, and a page per note. It reads the same SQLite database.
"""

from __future__ import annotations

import argparse
import html

from flask import Flask, abort, request

from .storage import NoteStore

app = Flask(__name__)
app.config["DB_PATH"] = "notes.db"
app.config["USER_ID"] = "default"


def _store() -> NoteStore:
    # One connection per request — SQLite connections aren't thread-safe and
    # Flask's dev server is threaded.
    return NoteStore(app.config["DB_PATH"])


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --bg: #f7f7f8; --card: #fff; --text: #1a1a1a; --muted: #6b7280;
    --border: #e5e7eb; --accent: #2563eb; --pill: #eef2ff; --pill-text: #3730a3;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #0f1115; --card: #1a1d23; --text: #e8e8e8; --muted: #9aa0a6;
      --border: #2a2e37; --accent: #6ea8fe; --pill: #23263a; --pill-text: #b9c2ff;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }}
  .wrap {{ max-width: 900px; margin: 0 auto; padding: 32px 20px 64px; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 4px; }}
  .sub {{ color: var(--muted); margin-bottom: 24px; }}
  form.search {{ display: flex; gap: 8px; margin-bottom: 20px; }}
  input[type=search] {{
    flex: 1; padding: 10px 14px; border: 1px solid var(--border);
    border-radius: 8px; background: var(--card); color: var(--text); font-size: 1rem;
  }}
  button {{
    padding: 10px 16px; border: 0; border-radius: 8px; background: var(--accent);
    color: #fff; font-size: 1rem; cursor: pointer;
  }}
  .tags {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 24px; }}
  a.pill, span.pill {{
    display: inline-block; padding: 3px 10px; border-radius: 999px;
    background: var(--pill); color: var(--pill-text); font-size: .8rem;
    text-decoration: none;
  }}
  a.pill.active {{ background: var(--accent); color: #fff; }}
  .card {{
    background: var(--card); border: 1px solid var(--border); border-radius: 12px;
    padding: 18px 20px; margin-bottom: 14px;
  }}
  .card h2 {{ font-size: 1.1rem; margin: 0 0 6px; }}
  .card h2 a {{ color: var(--text); text-decoration: none; }}
  .card h2 a:hover {{ color: var(--accent); }}
  .card .meta {{ color: var(--muted); font-size: .82rem; margin-bottom: 10px; }}
  .card .body {{ white-space: pre-wrap; }}
  .empty {{ color: var(--muted); padding: 40px 0; text-align: center; }}
  .back {{ display: inline-block; margin-bottom: 20px; color: var(--accent); text-decoration: none; }}
  .score {{ float: right; color: var(--muted); font-size: .75rem; }}
</style>
</head>
<body><div class="wrap">
{content}
</div></body>
</html>"""


def _esc(s: str) -> str:
    return html.escape(s or "")


def _tag_bar(all_tags: list[str], active: str | None, q: str) -> str:
    qs = f"?q={html.escape(q, quote=True)}" if q else ""
    pills = [f'<a class="pill{" active" if not active else ""}" href="/{qs}">all</a>']
    for t in all_tags:
        cls = "pill active" if t == active else "pill"
        pills.append(f'<a class="{cls}" href="/?tag={html.escape(t, quote=True)}">{_esc(t)}</a>')
    return f'<div class="tags">{"".join(pills)}</div>' if all_tags else ""


def _card(note, score: float | None = None) -> str:
    when = note.created_at[:16].replace("T", " ")
    tags = " ".join(f'<span class="pill">{_esc(t)}</span>' for t in note.tags)
    sc = f'<span class="score">score {score:.2f}</span>' if score is not None else ""
    return f"""<div class="card">
      {sc}<h2><a href="/note/{note.id}">{_esc(note.title)}</a></h2>
      <div class="meta">#{note.id} · {when}{" · " + tags if tags else ""}</div>
      <div class="body">{_esc(note.body[:400])}{"…" if len(note.body) > 400 else ""}</div>
    </div>"""


@app.route("/")
def index():
    store = _store()
    try:
        q = request.args.get("q", "").strip()
        tag = request.args.get("tag", "").strip() or None
        all_tags = store.all_tags(app.config["USER_ID"])

        if q or tag:
            results = store.search(
                query=q or None, tags=[tag] if tag else None,
                mode="auto", limit=100, user_id=app.config["USER_ID"],
            )
        else:
            results = [(n, None) for n in store.all_notes(app.config["USER_ID"])]

        header = (
            f'<h1>Notes</h1><div class="sub">{len(results)} '
            f'{"match" if (q or tag) else "note"}{"es" if (q or tag) and len(results) != 1 else ("s" if len(results) != 1 else "")}'
            f'{" for " + chr(39) + _esc(q) + chr(39) if q else ""}'
            f'{" tagged " + _esc(tag) if tag else ""}'
            f' · user <code>{_esc(app.config["USER_ID"])}</code></div>'
        )
        search_box = (
            f'<form class="search" method="get" action="/">'
            f'<input type="search" name="q" placeholder="Search notes…" value="{html.escape(q, quote=True)}">'
            f'<button type="submit">Search</button></form>'
        )
        cards = "".join(_card(n, s) for n, s in results) or \
            '<div class="empty">No notes found. Try a broader search or a different tag.</div>'
        content = header + search_box + _tag_bar(all_tags, tag, q) + cards
        return PAGE.format(title="Notes", content=content)
    finally:
        store.close()


@app.route("/note/<int:note_id>")
def note_page(note_id: int):
    store = _store()
    try:
        note = store.get_note(note_id, app.config["USER_ID"])
        if note is None:
            abort(404)
        when = note.created_at[:16].replace("T", " ")
        updated = note.updated_at[:16].replace("T", " ")
        tags = " ".join(f'<span class="pill">{_esc(t)}</span>' for t in note.tags) or \
            '<span class="sub">no tags</span>'
        content = f"""<a class="back" href="/">← all notes</a>
          <div class="card">
            <h1>{_esc(note.title)}</h1>
            <div class="meta">#{note.id} · created {when}{" · updated " + updated if updated != when else ""}</div>
            <div class="tags" style="margin:12px 0">{tags}</div>
            <div class="body">{_esc(note.body)}</div>
          </div>"""
        return PAGE.format(title=note.title, content=content)
    finally:
        store.close()


@app.route("/api/notes")
def api_notes():
    """JSON view of all notes, for scripting."""
    store = _store()
    try:
        return {"notes": [n.to_dict() for n in store.all_notes(app.config["USER_ID"])]}
    finally:
        store.close()


@app.errorhandler(404)
def not_found(_):
    return PAGE.format(
        title="Not found",
        content='<a class="back" href="/">← all notes</a><div class="empty">That note doesn\'t exist.</div>',
    ), 404


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Browse your notes in a web page.")
    ap.add_argument("--db", default="notes.db")
    ap.add_argument("--user", default="default")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    args = ap.parse_args(argv)

    app.config["DB_PATH"] = args.db
    app.config["USER_ID"] = args.user
    print(f"Serving notes from {args.db!r} (user {args.user!r}) at http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
