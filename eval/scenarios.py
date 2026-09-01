"""Conversational scenarios: happy paths + edge cases.

Each Scenario seeds a fresh database, then plays its turns through a real agent.
`checks` on a turn are evaluated after that turn completes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from . import checks as c

TODAY = date.today().isoformat()
TOMORROW = (date.today() + timedelta(days=1)).isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


@dataclass
class Turn:
    user: str
    checks: list[Callable] = field(default_factory=list)


@dataclass
class Scenario:
    name: str
    category: str
    turns: list[Turn]
    seed: list[dict] = field(default_factory=list)


def _has(*words):
    return lambda n: all(w.lower() in (n.title + " " + n.body).lower() for w in words)


SCENARIOS: list[Scenario] = [
    Scenario(
        "add_basic", "add",
        [Turn(
            "Save a note for today about the team standup — we agreed to move it "
            "to Tuesdays, tag it as meetings.",
            [c.tool_called("create_note", tags=["meetings"], date=TODAY),
             c.db_count(1),
             c.db_note_matches(_has("tuesday"), desc="body mentions Tuesday"),
             c.db_note_matches(lambda n: n.event_date == TODAY, desc="dated today")],
        )],
    ),
    Scenario(
        "add_no_date_asks", "add",
        [
            Turn("Remember that the wifi password at the office is hunter2green.",
                 [c.tool_not_called("create_note"),  # no date given -> must ask
                  c.reply_is_question(),
                  c.db_count(0)]),
            Turn("it's for today",
                 [c.tool_called("create_note", date=TODAY), c.db_count(1)]),
        ],
    ),
    Scenario(
        "add_rejects_past_date", "add",
        [Turn("Log a note dated yesterday about the server outage.",
              [c.db_count(0),  # past date must not be saved
               c.reply_contains_any("past", "today or later", "can't", "cannot", "future")])],
    ),
    Scenario(
        "multiturn_append", "multi-turn",
        [
            Turn("Save a note titled 'Q3 planning', for today, about drafting the roadmap.",
                 [c.tool_called("create_note")]),
            Turn("Actually, add a deadline of September 15th to that last note.",
                 [c.tool_called("update_note", note_id=1),  # resolved "that last note"
                  c.db_note_matches(
                      lambda n: "roadmap" in n.body.lower()
                      and ("15" in n.body or any("15" in t for t in n.tags)),
                      desc="original note kept and the deadline recorded on it")]),
        ],
    ),
    Scenario(
        "search_keyword", "search",
        [Turn("What did I write about the API?",
              [c.tool_called("search_notes"),
               c.reply_contains_any("rate limit", "api", "v2")])],
        seed=[
            {"title": "API redesign", "body": "Move to v2 and add rate limiting.", "tags": ["work"]},
            {"title": "Grocery list", "body": "Milk, eggs, bread.", "tags": ["home"]},
        ],
    ),
    Scenario(
        "search_by_tag", "search",
        [Turn("Show me everything I've tagged urgent.",
              # listing by tag is a valid intent for either tool
              [c.tool_called_any("search_notes", "list_notes"),
               c.reply_contains_any("passport", "deploy")])],
        seed=[
            {"title": "Renew passport", "body": "Expires next month.", "tags": ["urgent", "personal"]},
            {"title": "Hotfix deploy", "body": "Ship the auth patch.", "tags": ["urgent", "work"]},
            {"title": "Book club", "body": "Read chapter 4.", "tags": ["personal"]},
        ],
    ),
    Scenario(
        "search_empty_graceful", "error-handling",
        [Turn("What did I note about quarterly tax filings?",
              [c.tool_called("search_notes"),
               c.tool_not_called("create_note"),
               c.reply_contains_any("no notes", "couldn't find", "nothing", "didn't find", "no matching")])],
        seed=[{"title": "Gym schedule", "body": "Legs on Monday.", "tags": ["health"]}],
    ),
    Scenario(
        "update_significant_needs_confirm", "modify",
        [
            Turn("Update my standup note to say the meeting is now on Wednesdays.",
                 [c.tool_called("update_note"),
                  c.confirmed_before_write("update_note"),
                  c.asks_to_confirm()]),
            Turn("Yes, go ahead.",
                 [c.db_note_matches(_has("wednesday"), desc="body updated to Wednesday")]),
        ],
        seed=[{"title": "Team standup", "body": "Standup is on Mondays at 9am.", "tags": ["meetings"]}],
    ),
    Scenario(
        "update_add_tag_no_confirm", "modify",
        [Turn("Add a 'finance' tag to my budget note.",
              [c.tool_called("update_note", add_tags=["finance"]),
               c.db_note_matches(lambda n: "finance" in n.tags, desc="finance tag present")])],
        seed=[{"title": "Monthly budget", "body": "Rent, utilities, savings.", "tags": ["personal"]}],
    ),
    Scenario(
        "update_ambiguous_asks", "disambiguation",
        [Turn("Update my project note to add a note about the client call.",
              [c.reply_is_question(),
               c.tool_not_called("update_note") ])],
        seed=[
            {"title": "Project Apollo", "body": "Kickoff next week.", "tags": ["work"]},
            {"title": "Project Zephyr", "body": "Waiting on design.", "tags": ["work"]},
        ],
    ),
    Scenario(
        "delete_basic_confirm", "delete",
        [
            Turn("Delete the note about the old office address.",
                 [c.tool_called("delete_note"),
                  c.confirmed_before_write("delete_note"),
                  c.asks_to_confirm(),
                  c.db_count(2)]),
            Turn("Yes, delete it.",
                 [c.db_count(1),
                  c.db_no_note_matches(_has("221b baker"), desc="old address note gone")]),
        ],
        seed=[
            {"title": "Old office address", "body": "221B Baker Street.", "tags": ["work"]},
            {"title": "New office address", "body": "10 Downing Street.", "tags": ["work"]},
        ],
    ),
    Scenario(
        "delete_declined", "delete",
        [
            Turn("Delete my standup note.",
                 [c.tool_called("delete_note"), c.asks_to_confirm()]),
            Turn("No, actually keep it.",
                 [c.db_count(1),
                  c.db_note_matches(_has("standup"), desc="note preserved")]),
        ],
        seed=[{"title": "Team standup", "body": "Mondays at 9.", "tags": ["meetings"]}],
    ),
    Scenario(
        "delete_ambiguous_asks", "disambiguation",
        [Turn("Delete the meeting note.",
              [c.reply_is_question(),
               c.tool_not_called("delete_note"),
               c.db_count(2)])],
        seed=[
            {"title": "Standup meeting", "body": "Mondays.", "tags": ["meetings"]},
            {"title": "Client meeting", "body": "Thursday 2pm.", "tags": ["meetings"]},
        ],
    ),
    Scenario(
        "summarise_by_tag", "reasoning",
        [Turn("Summarise everything I've tagged as urgent.",
              [c.tool_called("search_notes"),
               c.reply_contains_any("passport"),
               c.reply_contains_any("invoice", "server")])],
        seed=[
            {"title": "Passport", "body": "Renew before travel on Oct 3.", "tags": ["urgent"]},
            {"title": "Invoice", "body": "Send invoice #42 to Acme.", "tags": ["urgent"]},
            {"title": "Server", "body": "Disk almost full on prod.", "tags": ["urgent"]},
        ],
    ),
    Scenario(
        "detect_contradiction", "reasoning",
        [Turn("Do I have any notes that contradict each other?",
              [c.tool_called("search_notes"),
               c.reply_contains_any("standup", "contradict", "conflict", "tuesday", "thursday")])],
        seed=[
            {"title": "Standup day", "body": "We moved standup to Tuesdays.", "tags": ["meetings"]},
            {"title": "Standup reminder", "body": "Don't forget standup is on Thursdays now.", "tags": ["meetings"]},
        ],
    ),
    Scenario(
        "semantic_search", "search",
        [Turn("Find my notes related to project payments and deadlines.",
              [c.tool_called("search_notes"),
               c.reply_contains_any("venue", "deposit", "invoice")])],
        seed=[
            {"title": "Venue booking", "body": "Bring cash to secure the venue deposit by Friday.", "tags": ["events"]},
            {"title": "Lunch spots", "body": "Try the ramen place downtown.", "tags": ["food"]},
        ],
    ),
]
