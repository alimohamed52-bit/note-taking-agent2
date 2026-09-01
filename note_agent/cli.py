"""Command-line chat loop.

    python -m note_agent.cli [--user NAME] [--db PATH]
"""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from .agent import NoteAgent
from .llm import GroqLLM
from .storage import NoteStore

BANNER = """\
Conversational Note-Taking Agent
Type your message and press Enter. Commands: /notes  /reset  /quit
"""


def main(argv=None) -> int:
    # LLMs emit smart quotes / narrow spaces; the Windows console is cp1252 by
    # default and would crash on them. Fall back to replacement chars instead.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    load_dotenv()
    parser = argparse.ArgumentParser(description="Chat with your note-taking agent.")
    parser.add_argument("--user", default="default", help="user id for note scoping")
    parser.add_argument("--db", default="notes.db", help="path to the SQLite database")
    parser.add_argument("--model", default=None, help="override the Groq model id")
    args = parser.parse_args(argv)

    store = NoteStore(args.db)
    try:
        llm = GroqLLM(model=args.model)
    except RuntimeError as exc:
        print(f"Error: {exc}")
        return 1

    agent = NoteAgent(store, llm=llm, user_id=args.user)
    print(BANNER)
    _report_purged(agent.purged_on_start)

    while True:
        try:
            text = input("you › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not text:
            continue
        if text in ("/quit", "/exit"):
            break
        if text == "/reset":
            agent = NoteAgent(store, llm=llm, user_id=args.user)
            print("(conversation reset)\n")
            _report_purged(agent.purged_on_start)
            continue
        if text == "/notes":
            for n in store.all_notes(args.user):
                when = f"({n.event_date}) " if n.event_date else ""
                print(f"  [{n.id}] {when}{n.title}  {', '.join('#' + t for t in n.tags)}")
            print()
            continue

        try:
            turn = agent.send(text)
        except Exception as exc:  # network/API errors shouldn't kill the session
            print(f"agent › (error: {exc})\n")
            continue

        _report_purged(turn.purged)
        for tc in turn.tool_calls:
            print(f"      · {tc.name}({_fmt_args(tc.arguments)}) -> {tc.result.get('status')}")
        print(f"agent › {turn.reply}\n")

    store.close()
    return 0


def _fmt_args(args: dict) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in args.items())


def _report_purged(purged) -> None:
    for n in purged or []:
        print(f"      · auto-removed past note [{n.id}] {n.title} ({n.event_date})")
    if purged:
        print()


if __name__ == "__main__":
    sys.exit(main())
