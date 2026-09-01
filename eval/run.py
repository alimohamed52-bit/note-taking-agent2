"""Run the eval suite against a live agent and report pass/fail rates.

    python -m eval.run [--model MODEL] [--only NAME] [--json report.json]

Requires GROQ_API_KEY. Each scenario gets its own throwaway SQLite file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import traceback
from collections import defaultdict

from dotenv import load_dotenv

from note_agent.agent import NoteAgent
from note_agent.llm import GroqLLM
from note_agent.storage import NoteStore

from .scenarios import SCENARIOS

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def run_scenario(scn, llm) -> dict:
    fd, path = tempfile.mkstemp(suffix=".db", prefix=f"eval_{scn.name}_")
    os.close(fd)
    store = NoteStore(path)
    for note in scn.seed:
        store.add_note(**note)

    agent = NoteAgent(store, llm=llm, user_id="default")
    turn_results = []
    error = None

    try:
        for i, turn in enumerate(scn.turns):
            outcome = agent.send(turn.user)
            checks = []
            for check in turn.checks:
                try:
                    passed, detail = check(outcome, store)
                except Exception as exc:  # a check blew up
                    passed, detail = False, f"check raised {exc!r}"
                checks.append({"passed": passed, "detail": detail})
            turn_results.append({
                "user": turn.user,
                "reply": outcome.reply,
                "tools": [{"name": t.name, "args": t.arguments, "status": t.result.get("status")}
                          for t in outcome.tool_calls],
                "checks": checks,
            })
    except Exception:
        error = traceback.format_exc()
    finally:
        store.close()
        try:
            os.remove(path)
        except OSError:
            pass

    # A rate-limit / quota error is an environment problem, not a scenario
    # failure — report it separately so it doesn't look like a regression.
    skipped = bool(error) and ("RateLimited" in error or "rate_limit" in error
                               or "RateLimitError" in error)

    all_checks = [ch for tr in turn_results for ch in tr["checks"]]
    passed = (bool(all_checks) and all(ch["passed"] for ch in all_checks)
              and error is None)
    return {
        "name": scn.name,
        "category": scn.category,
        "passed": passed,
        "skipped": skipped,
        "error": error,
        "turns": turn_results,
        "n_checks": len(all_checks),
        "n_passed": sum(ch["passed"] for ch in all_checks),
    }


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    ap.add_argument("--only", default=None, help="run only scenarios whose name contains this")
    ap.add_argument("--json", default="eval/report.json")
    args = ap.parse_args(argv)

    try:
        llm = GroqLLM(model=args.model)
    except RuntimeError as exc:
        print(f"Error: {exc}")
        return 1

    scenarios = [s for s in SCENARIOS if not args.only or args.only in s.name]
    results = []
    print(f"Running {len(scenarios)} scenarios with model '{llm.model}'\n")

    for scn in scenarios:
        t0 = time.time()
        res = run_scenario(scn, llm)
        results.append(res)
        if res["skipped"]:
            mark = f"{DIM}SKIP{RESET}"
        elif res["passed"]:
            mark = f"{GREEN}PASS{RESET}"
        else:
            mark = f"{RED}FAIL{RESET}"
        print(f"  {mark}  {scn.name:<32} {res['n_passed']}/{res['n_checks']} checks  {DIM}{time.time()-t0:.1f}s{RESET}")
        if res["skipped"]:
            print(f"        {DIM}rate-limited — not counted{RESET}")
        elif not res["passed"]:
            for tr in res["turns"]:
                for ch in tr["checks"]:
                    if not ch["passed"]:
                        print(f"        {DIM}- {ch['detail']}{RESET}")
            if res["error"]:
                print(f"        {DIM}{res['error'].splitlines()[-1]}{RESET}")

    scored = [r for r in results if not r["skipped"]]
    total = len(scored)
    passed = sum(r["passed"] for r in scored)
    skipped = len(results) - total
    by_cat = defaultdict(lambda: [0, 0])
    for r in scored:
        by_cat[r["category"]][0] += r["passed"]
        by_cat[r["category"]][1] += 1

    print(f"\n{'='*50}")
    rate = f"{100*passed/total:.0f}%" if total else "n/a"
    print(f"Overall: {passed}/{total} scenarios passed ({rate})"
          + (f"  ·  {skipped} skipped (rate-limited)" if skipped else ""))
    print("By category:")
    for cat, (p, n) in sorted(by_cat.items()):
        print(f"  {cat:<18} {p}/{n}")

    report = {
        "model": llm.model,
        "overall": {"passed": passed, "total": total,
                    "rate": (passed / total if total else None), "skipped": skipped},
        "by_category": {k: {"passed": v[0], "total": v[1]} for k, v in by_cat.items()},
        "scenarios": results,
    }
    os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nFull report written to {args.json}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
