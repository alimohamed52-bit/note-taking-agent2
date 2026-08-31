"""Assertion helpers for the eval harness.

A check is a callable ``(turn, store) -> (passed: bool, detail: str)``.

How we measure "did the agent interpret intent correctly?"
---------------------------------------------------------
Intent is operationalised as *the tool call the agent makes*. For each scenario
turn we assert on some combination of:
  * which tool was called and with which key arguments (`tool_called`)
  * which tool was NOT called (`tool_not_called`) — e.g. it must not delete on an
    ambiguous reference
  * the resulting database state (`db_count`, `db_note_matches`, `db_no_note_matches`)
  * whether the reply is a clarifying question (`reply_is_question`)
  * whether the reply surfaces the right information (`reply_contains_any`)
This keeps scoring objective and independent of exact wording.
"""

from __future__ import annotations


def _contains(actual, expected) -> bool:
    if isinstance(expected, str):
        return isinstance(actual, str) and expected.lower() in actual.lower()
    if isinstance(expected, (list, tuple, set)):
        actual_set = {str(x).lower() for x in (actual or [])}
        return {str(x).lower() for x in expected}.issubset(actual_set)
    return actual == expected


def tool_called(name: str, **arg_match):
    def check(turn, store):
        calls = turn.called(name)
        if not calls:
            return False, f"expected a {name} call, got {[c.name for c in turn.tool_calls]}"
        if not arg_match:
            return True, f"{name} called"
        for c in calls:
            if all(_contains(c.arguments.get(k), v) for k, v in arg_match.items()):
                return True, f"{name} called with {arg_match}"
        return False, f"{name} called but not with {arg_match}; saw {[c.arguments for c in calls]}"
    return check


def tool_not_called(name: str):
    def check(turn, store):
        calls = turn.called(name)
        if calls:
            return False, f"{name} should not have been called; args={[c.arguments for c in calls]}"
        return True, f"{name} correctly not called"
    return check


def confirmed_before_write(name: str):
    """The staged (no-confirm) call must precede any confirm=true call of `name`."""
    def check(turn, store):
        seq = [bool(c.arguments.get("confirm")) for c in turn.called(name)]
        if not seq:
            return True, f"no {name} calls"
        if seq[0] is True:
            return False, f"{name} called with confirm=true before any confirmation step"
        return True, f"{name} staged before confirming"
    return check


def reply_is_question():
    def check(turn, store):
        ok = "?" in turn.reply
        return ok, "reply asks a question" if ok else f"expected a clarifying question, got: {turn.reply!r}"
    return check


def reply_contains_any(*subs: str):
    def check(turn, store):
        low = turn.reply.lower()
        ok = any(s.lower() in low for s in subs)
        return ok, "reply mentions expected content" if ok else f"reply missing any of {subs}: {turn.reply!r}"
    return check


def db_count(expected: int, user_id: str = "default"):
    def check(turn, store):
        n = len(store.all_notes(user_id))
        return n == expected, f"expected {expected} notes, found {n}"
    return check


def db_note_matches(predicate, user_id: str = "default", desc: str = ""):
    def check(turn, store):
        ok = any(predicate(n) for n in store.all_notes(user_id))
        return ok, f"a note matches {desc}" if ok else f"no note matches {desc}"
    return check


def db_no_note_matches(predicate, user_id: str = "default", desc: str = ""):
    def check(turn, store):
        ok = not any(predicate(n) for n in store.all_notes(user_id))
        return ok, f"no note matches {desc} (correct)" if ok else f"a note still matches {desc}"
    return check
