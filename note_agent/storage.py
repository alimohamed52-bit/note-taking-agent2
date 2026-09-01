"""SQLite persistence layer for notes.

Why SQLite (over a JSON file):
  * Notes are queried by structured facets — tag, date range, keyword — which map
    naturally to SQL WHERE clauses and indexes. A JSON file would mean loading and
    filtering everything in Python on every turn.
  * It gives us atomic writes and a real concurrency story for free, which matters
    once the CLI and the Streamlit app touch the same database.
  * `user_id` scoping (bonus: multi-user isolation) is one indexed column.
  * It is still a single self-contained file with zero setup — the lightweight
    storage the brief asks for.

The embedding vector for each note is cached in the row as raw float32 bytes so we
never have to re-embed on read.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import numpy as np

from . import embeddings

DEFAULT_DB_PATH = "notes.db"

# Minimum cosine similarity for a note to count as a semantic match. BGE-small
# puts unrelated short texts around 0.45-0.53 and genuine matches at 0.58+, so
# this floor keeps "no results" queries genuinely empty instead of returning
# every note with a mediocre score.
SEM_FLOOR = 0.58

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL DEFAULT 'default',
    title       TEXT    NOT NULL,
    body        TEXT    NOT NULL DEFAULT '',
    tags        TEXT    NOT NULL DEFAULT '',   -- comma-separated, normalised
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    event_date  TEXT,                           -- YYYY-MM-DD the note is "for"
    embedding   BLOB                            -- float32 bytes, nullable
);
CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalise_tags(tags) -> list[str]:
    """Accept a list or a comma/space string; return a clean lowercase list."""
    if not tags:
        return []
    if isinstance(tags, str):
        parts = re.split(r"[,\n]", tags)
    else:
        parts = list(tags)
    seen, out = set(), []
    for p in parts:
        t = str(p).strip().lower().lstrip("#")
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


@dataclass
class Note:
    id: int
    title: str
    body: str
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    event_date: str = ""

    def to_dict(self, include_body: bool = True) -> dict:
        d = {
            "id": self.id,
            "title": self.title,
            "tags": self.tags,
            "event_date": self.event_date,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_body:
            d["body"] = self.body
        else:
            d["snippet"] = self.body[:200] + ("…" if len(self.body) > 200 else "")
        return d


class NoteStore:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        # check_same_thread=False: the Flask dev server may touch the store from
        # a worker thread. All web access is serialised behind a lock in web.py,
        # and the CLI / Streamlit are single-threaded, so this stays safe.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created."""
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(notes)")}
        if "event_date" not in cols:
            self._conn.execute("ALTER TABLE notes ADD COLUMN event_date TEXT")

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _row_to_note(row: sqlite3.Row) -> Note:
        return Note(
            id=row["id"],
            title=row["title"],
            body=row["body"],
            tags=[t for t in row["tags"].split(",") if t],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            event_date=(row["event_date"] or "") if "event_date" in row.keys() else "",
        )

    def _embedding_text(self, title: str, body: str, tags: list[str]) -> str:
        return f"{title}\n{body}\n{' '.join(tags)}".strip()

    def _compute_embedding(self, title, body, tags) -> bytes | None:
        vec = embeddings.embed_one(self._embedding_text(title, body, tags))
        return None if vec is None else vec.astype(np.float32).tobytes()

    # --------------------------------------------------------------------- CRUD
    def add_note(self, title: str, body: str = "", tags=None,
                 user_id: str = "default", event_date: str | None = None) -> Note:
        title = title.strip()
        body = (body or "").strip()
        tag_list = _normalise_tags(tags)
        ts = _now()
        emb = self._compute_embedding(title, body, tag_list)
        cur = self._conn.execute(
            "INSERT INTO notes (user_id, title, body, tags, created_at, updated_at, event_date, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, title, body, ",".join(tag_list), ts, ts, event_date or None, emb),
        )
        self._conn.commit()
        return self.get_note(cur.lastrowid, user_id)

    def get_note(self, note_id: int, user_id: str = "default") -> Note | None:
        row = self._conn.execute(
            "SELECT * FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id)
        ).fetchone()
        return self._row_to_note(row) if row else None

    def all_notes(self, user_id: str = "default") -> list[Note]:
        rows = self._conn.execute(
            "SELECT * FROM notes WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
        return [self._row_to_note(r) for r in rows]

    def all_tags(self, user_id: str = "default") -> list[str]:
        tags = set()
        for r in self._conn.execute(
            "SELECT tags FROM notes WHERE user_id = ?", (user_id,)
        ):
            tags.update(t for t in r["tags"].split(",") if t)
        return sorted(tags)

    def update_note(self, note_id: int, *, title=None, body=None, append_body=None,
                    add_tags=None, remove_tags=None, set_tags=None, event_date=None,
                    user_id: str = "default") -> Note | None:
        note = self.get_note(note_id, user_id)
        if note is None:
            return None

        if event_date is not None:
            note.event_date = event_date
        if title is not None:
            note.title = title.strip()
        if body is not None:
            note.body = body.strip()
        if append_body:
            note.body = (note.body + "\n" + append_body.strip()).strip()

        if set_tags is not None:
            note.tags = _normalise_tags(set_tags)
        else:
            current = list(note.tags)
            for t in _normalise_tags(add_tags):
                if t not in current:
                    current.append(t)
            for t in _normalise_tags(remove_tags):
                if t in current:
                    current.remove(t)
            note.tags = current

        ts = _now()
        emb = self._compute_embedding(note.title, note.body, note.tags)
        self._conn.execute(
            "UPDATE notes SET title=?, body=?, tags=?, event_date=?, updated_at=?, embedding=? "
            "WHERE id=? AND user_id=?",
            (note.title, note.body, ",".join(note.tags), note.event_date or None,
             ts, emb, note_id, user_id),
        )
        self._conn.commit()
        return self.get_note(note_id, user_id)

    def delete_note(self, note_id: int, user_id: str = "default") -> bool:
        cur = self._conn.execute(
            "DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def purge_past_notes(self, today: str | None = None) -> list[Note]:
        """Delete every note whose event_date is strictly before `today`
        (default: the local current date), across all users. Notes with no
        event_date are never touched. Returns the notes that were deleted so the
        caller can report them.
        """
        cutoff = today or date.today().isoformat()
        rows = self._conn.execute(
            "SELECT * FROM notes WHERE event_date IS NOT NULL AND event_date < ? "
            "ORDER BY event_date",
            (cutoff,),
        ).fetchall()
        if not rows:
            return []
        deleted = [self._row_to_note(r) for r in rows]
        self._conn.execute(
            "DELETE FROM notes WHERE event_date IS NOT NULL AND event_date < ?",
            (cutoff,),
        )
        self._conn.commit()
        return deleted

    # ------------------------------------------------------------------- search
    def search(self, query: str | None = None, tags=None, date_from: str | None = None,
               date_to: str | None = None, mode: str = "auto", limit: int = 10,
               user_id: str = "default") -> list[tuple[Note, float]]:
        """Return (note, score) tuples, best first.

        mode: "keyword" | "semantic" | "hybrid" | "auto".
        "auto" uses hybrid when an embedding model is available, else keyword.
        Filters (tags, date range) are always applied first as a hard filter.
        """
        candidates = self.all_notes(user_id)

        tag_filter = _normalise_tags(tags)
        if tag_filter:
            candidates = [n for n in candidates
                          if any(t in n.tags for t in tag_filter)]
        if date_from:
            candidates = [n for n in candidates if n.created_at[:10] >= date_from[:10]]
        if date_to:
            candidates = [n for n in candidates if n.created_at[:10] <= date_to[:10]]

        if not query:
            # Pure filter query — newest first.
            return [(n, 1.0) for n in candidates[:limit]]

        if mode == "auto":
            mode = "hybrid" if embeddings.is_available() else "keyword"

        kw = {n.id: s for n, s in self._keyword_scores(query, candidates)}
        by_id = {n.id: n for n in candidates}

        sem = None
        if mode in ("semantic", "hybrid"):
            sem_pairs = self._semantic_scores(query, candidates, user_id)
            if sem_pairs is not None:
                sem = {n.id: s for n, s in sem_pairs}
            elif mode == "semantic":
                mode = "keyword"  # model unavailable; fall back
            else:
                mode = "keyword"

        ranked: list[tuple[Note, float]] = []
        if mode == "keyword":
            ranked = [(by_id[i], s) for i, s in kw.items() if s > 0]
        elif mode == "semantic":
            ranked = [(by_id[i], s) for i, s in sem.items() if s >= SEM_FLOOR]
        else:  # hybrid: a note qualifies on a keyword hit OR a strong semantic hit
            kw_hi = max(kw.values()) if kw else 0.0
            for i, note in by_id.items():
                k, s = kw.get(i, 0.0), sem.get(i, 0.0)
                if k <= 0 and s < SEM_FLOOR:
                    continue
                k_norm = k / kw_hi if kw_hi > 0 else 0.0
                ranked.append((note, 0.5 * k_norm + 0.5 * s))

        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked[:limit]

    def _keyword_scores(self, query, notes) -> list[tuple[Note, float]]:
        terms = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 1]
        out = []
        for n in notes:
            title_l, body_l = n.title.lower(), n.body.lower()
            tags_l = " ".join(n.tags)
            score = 0.0
            for term in terms:
                score += 3 * title_l.count(term)
                score += 1 * body_l.count(term)
                score += 2 * tags_l.count(term)
            out.append((n, score))
        return out

    def _semantic_scores(self, query, notes, user_id) -> list[tuple[Note, float]] | None:
        qvec = embeddings.embed_one(query, is_query=True)
        if qvec is None or not notes:
            return None if qvec is None else [(n, 0.0) for n in notes]
        rows = {
            r["id"]: r["embedding"]
            for r in self._conn.execute(
                "SELECT id, embedding FROM notes WHERE user_id = ?", (user_id,)
            )
        }
        out = []
        for n in notes:
            raw = rows.get(n.id)
            if not raw:
                out.append((n, 0.0))
                continue
            vec = np.frombuffer(raw, dtype=np.float32)
            out.append((n, float(np.dot(vec, qvec))))
        return out
