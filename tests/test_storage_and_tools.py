"""Offline tests for the storage and tool layers — no API key needed.

    python -m pytest        (or)        python tests/test_storage_and_tools.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta

TODAY = date.today().isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()
TOMORROW = (date.today() + timedelta(days=1)).isoformat()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from note_agent.storage import NoteStore
from note_agent.tools import ToolExecutor


def fresh():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = NoteStore(path)
    return store, ToolExecutor(store), path


def test_create_and_get():
    store, tx, path = fresh()
    try:
        r = tx.run("create_note", {"title": "Standup", "body": "Mondays",
                                   "tags": ["Meetings", "work"], "date": TODAY})
        assert r["status"] == "ok"
        nid = r["note"]["id"]
        got = tx.run("get_note", {"note_id": nid})["note"]
        assert got["tags"] == ["meetings", "work"]
        assert got["event_date"] == TODAY
    finally:
        store.close(); os.remove(path)


def test_create_note_requires_a_date():
    store, tx, path = fresh()
    try:
        r = tx.run("create_note", {"title": "Buy milk"})
        assert r["status"] == "date_required"
        assert len(store.all_notes()) == 0, "nothing saved without a date"
    finally:
        store.close(); os.remove(path)


def test_create_note_rejects_past_dates():
    store, tx, path = fresh()
    try:
        r = tx.run("create_note", {"title": "Late", "date": YESTERDAY})
        assert r["status"] == "error" and "past" in r["message"].lower()
        assert len(store.all_notes()) == 0
        # today and future are fine
        assert tx.run("create_note", {"title": "Now", "date": TODAY})["status"] == "ok"
        assert tx.run("create_note", {"title": "Soon", "date": TOMORROW})["status"] == "ok"
    finally:
        store.close(); os.remove(path)


def test_delete_requires_confirmation():
    store, tx, path = fresh()
    try:
        nid = tx.run("create_note", {"title": "Temp", "date": TODAY})["note"]["id"]
        staged = tx.run("delete_note", {"note_id": nid})
        assert staged["status"] == "confirmation_required"
        assert store.get_note(nid) is not None, "note must survive an unconfirmed delete"
        done = tx.run("delete_note", {"note_id": nid, "confirm": True})
        assert done["status"] == "ok"
        assert store.get_note(nid) is None
    finally:
        store.close(); os.remove(path)


def test_update_significant_vs_safe():
    store, tx, path = fresh()
    try:
        nid = tx.run("create_note", {"title": "Note", "body": "original", "date": TODAY})["note"]["id"]
        # Safe: appending applies immediately.
        r = tx.run("update_note", {"note_id": nid, "append_body": "line two"})
        assert r["status"] == "ok" and "line two" in r["note"]["body"]
        # Significant: body replacement needs confirm.
        r = tx.run("update_note", {"note_id": nid, "body": "rewritten"})
        assert r["status"] == "confirmation_required"
        assert store.get_note(nid).body != "rewritten"
        r = tx.run("update_note", {"note_id": nid, "body": "rewritten", "confirm": True})
        assert r["status"] == "ok" and store.get_note(nid).body == "rewritten"
    finally:
        store.close(); os.remove(path)


def test_search_keyword_and_filters():
    store, tx, path = fresh()
    try:
        tx.run("create_note", {"title": "API redesign", "body": "add rate limiting", "tags": ["work"], "date": TODAY})
        tx.run("create_note", {"title": "Groceries", "body": "milk and eggs", "tags": ["home"], "date": TODAY})
        res = tx.run("search_notes", {"query": "rate limiting API"})
        assert res["notes"][0]["title"] == "API redesign"
        tagged = tx.run("search_notes", {"tags": ["home"]})
        assert tagged["count"] == 1 and tagged["notes"][0]["title"] == "Groceries"
        assert tx.run("search_notes", {"query": "nonexistent topic"})["count"] == 0
    finally:
        store.close(); os.remove(path)


def test_user_isolation():
    store, _, path = fresh()
    try:
        store.add_note("Alice note", user_id="alice")
        store.add_note("Bob note", user_id="bob")
        assert len(store.all_notes("alice")) == 1
        assert store.all_notes("alice")[0].title == "Alice note"
        assert ToolExecutor(store, "bob").run("get_note", {"note_id": 1})["status"] == "not_found"
    finally:
        store.close(); os.remove(path)


def test_purge_past_notes():
    store, _, path = fresh()
    try:
        store.add_note("Stale", event_date=YESTERDAY)
        store.add_note("Today", event_date=TODAY)
        store.add_note("Future", event_date=TOMORROW)
        store.add_note("Undated")  # no event_date -> never purged

        removed = store.purge_past_notes()
        assert [n.title for n in removed] == ["Stale"]
        titles = {n.title for n in store.all_notes()}
        assert titles == {"Today", "Future", "Undated"}
        assert store.purge_past_notes() == []  # idempotent
    finally:
        store.close(); os.remove(path)


def test_not_found_paths():
    store, tx, path = fresh()
    try:
        assert tx.run("get_note", {"note_id": 999})["status"] == "not_found"
        assert tx.run("delete_note", {"note_id": 999})["status"] == "not_found"
        assert tx.run("update_note", {"note_id": 999, "title": "x"})["status"] == "not_found"
    finally:
        store.close(); os.remove(path)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
