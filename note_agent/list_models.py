"""Print the Groq models the current API key can access.

    python -m note_agent.list_models

Useful because Groq's catalogue changes and access varies by account — if the
default model 404s, pick one from this list and set GROQ_MODEL.
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        print("GROQ_API_KEY is not set (put it in .env).")
        return 1

    from groq import Groq

    models = sorted(m.id for m in Groq(api_key=key).models.list().data)
    print("Models available to this key:\n")
    for m in models:
        print(f"  {m}")
    print("\nSet one with GROQ_MODEL=<id> in .env, or pass --model to the CLI.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
