"""Assertion helpers for the eval harness.

A check is a callable ``(turn, store) -> (passed: bool, detail: str)``.

How we measure "did the agent interpret intent correctly?"
---------------------------------------------------------
Intent is operationalised as *the tool call the agent makes*. For each scenario
turn we assert on some combination of:
  * which tool was called and with which key arguments (`tool_called`,
    `tool_called_any`)
  * which tool was NOT called (`tool_not_called`) — e.g. it must not delete on an
    ambiguous reference
  * the resulting database state (`db_count`, `db_note_matches`, `db_no_note_matches`)
  * whether the reply asks the user to clarify (`reply_is_question`) or to
    confirm a destructive action (`asks_to_confirm`)
  * whether the reply surfaces the right information (`reply_contains_any`)
Wording is never asserted on directly — only normalised substring presence.
"""

from __future__ import annotations

# Typographic characters an LLM commonly emits, folded to plain ASCII so that
# substring checks don't fail on a curly apostrophe or a narrow no-break space.
_FOLD = {
    0x2019: "'", 0x2018: "'", 0x201C: '"', 0x201D: '"',
    0x2014: "-", 0x2013: "-", 0x202F: " ", 0x00A0: " ",
}


def _norm(text) -> str:
    if not isinstance(text, str):
        return ""
    return text.translate(_FOLD).lower()


def _contains(actual, expected) -> bool:
    if isinstance(expected, str):
        return _norm(expected) in _norm(actual)
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


def tool_called_any(*names: str, **arg_match):
    """Pass if any of `names` was called (optionally matching arg_match)."""
    def check(turn, store):
        for name in names:
            ok, _ = tool_called(name, **arg_match)(turn, store)
            if ok:
                return True, f"{name} called"
        got = [c.name for c in turn.tool_calls]
        return False, f"expected one of {names}, got {got}"
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


def asks_to_confirm():
    """Reply seeks explicit go-ahead for a destructive action."""
    cues = ("?", "confirm", "yes/no", "y/n", "are you sure", "go ahead", "proceed")
    def check(turn, store):
        low = _norm(turn.reply)
        ok = any(c in low for c in cues)
        return ok, "reply asks for confirmation" if ok else f"expected a confirmation prompt, got: {turn.reply!r}"
    return check


def reply_contains_any(*subs: str):
    def check(turn, store):
        low = _norm(turn.reply)
        ok = any(_norm(s) in low for s in subs)
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
