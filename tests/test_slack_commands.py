"""Slack command payloads: the formatters behind /veto-status, -budget, -routines.

These are pure functions over plain dicts — no workspace, socket or Slack client —
so the wording, the money, the buttons and the ordering can be checked offline.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from veto import slack_commands as slack  # noqa: E402


def iso(**offset) -> str:
    return (datetime.now(timezone.utc) - timedelta(**offset)).isoformat(timespec="seconds")


def section_texts(payload: dict) -> list[str]:
    texts = []
    for block in payload.get("blocks") or []:
        if block.get("type") == "section":
            if block.get("text"):
                texts.append(block["text"]["text"])
            for field in block.get("fields") or []:
                texts.append(field["text"])
        elif block.get("type") == "context":
            texts.extend(element["text"] for element in block.get("elements") or [])
    return texts


def actions(payload: dict) -> list[dict]:
    return [
        element
        for block in payload.get("blocks") or []
        if block.get("type") == "actions"
        for element in block.get("elements") or []
    ]


class HelpTests(unittest.TestCase):
    def test_help_lists_every_command_but_itself(self) -> None:
        payload = slack.help_payload("https://veto.example")
        text = "\n".join(section_texts(payload))
        for command in slack.COMMANDS:
            if command["command"] == "/veto-help":
                continue
            self.assertIn(command["command"], text)
        self.assertIn("/veto 2 packs", " ".join(
            command["example"] for command in slack.COMMANDS if command["command"] == "/veto"
        ))
        self.assertIn("https://veto.example/app", text)
        self.assertEqual(payload["response_type"], "ephemeral")
        self.assertTrue(payload["text"])

    def test_help_lists_the_console_only_when_it_has_one(self) -> None:
        text = "\n".join(section_texts(slack.help_payload(None)))
        self.assertNotIn("Console ·", text)


class StatusTests(unittest.TestCase):
    def test_empty_status_is_a_clear_all_clear(self) -> None:
        payload = slack.status_payload(
            [],
            next_routine={"name": "Pantry restock", "agent_name": "Pantry runner",
                          "next_run_at": iso(days=-2)},
        )
        text = "\n".join(section_texts(payload))
        self.assertIn("Nothing is waiting on you", text)
        self.assertIn("Pantry restock", text)
        self.assertRegex(text, r"in [12] d")
        self.assertEqual(actions(payload), [])

    def test_pending_rows_carry_amount_buttons_and_age(self) -> None:
        payload = slack.status_payload(
            [
                {"id": "REQ-1055", "agent_name": "Desk kit", "title": "Desk kit",
                 "total_cents": 14599, "line_count": 2, "created_at": iso(minutes=12)},
                {"id": "REQ-1056", "agent_name": "Pantry runner", "title": "Pantry",
                 "total_cents": 1815, "line_count": 1, "created_at": iso(hours=3)},
            ],
            committed_cents=16414,
            capture_owed=1,
        )
        text = "\n".join(section_texts(payload))
        self.assertIn("2 requests wait for a signature", text)
        self.assertIn("$145.99", text)
        self.assertIn("$164.14", text)
        self.assertIn("12 min ago", text)
        self.assertIn("1 approved, capture owed", text)
        buttons = actions(payload)
        self.assertEqual([button["action_id"] for button in buttons],
                         ["veto_approve", "veto_veto", "veto_approve", "veto_veto"])
        self.assertEqual(buttons[0]["value"], "REQ-1055")
        self.assertEqual(buttons[2]["value"], "REQ-1056")

    def test_one_waiting_request_is_grammatical(self) -> None:
        payload = slack.status_payload(
            [{"id": "REQ-1055", "agent_name": "Desk kit", "title": "Monitor",
              "total_cents": 14599, "line_count": 1, "created_at": iso(minutes=1)}],
            committed_cents=14599,
        )
        text = "\n".join(section_texts(payload))
        self.assertIn("1 request waits for a signature", text)
        self.assertIn("1 item ·", text)

    def test_status_builder_keeps_the_fallback_text(self) -> None:
        payload = slack.status_payload([])
        self.assertEqual(payload["text"], "Waiting on you")


class CheckTests(unittest.TestCase):
    def candidate(self, index: int, verdict: str, amount: int = 1_500) -> dict:
        return {
            "index": index,
            "title": f"Paper towels {index}",
            "qty": 1,
            "unit_cents": amount,
            "line_cents": amount,
            "vendor": "walmart.com",
            "category": "pantry",
            "url": f"https://walmart.example/{index}",
            "verdict": verdict,
        }

    def test_the_shortlist_offers_a_button_only_for_chooseable_rows(self) -> None:
        payload = slack.check_payload(
            "abc123",
            query="paper towels",
            agent_name="Pantry runner",
            qty=1,
            candidates=[
                self.candidate(0, "auto"),
                self.candidate(1, "review"),
                self.candidate(2, "block"),
            ],
        )
        text = "\n".join(section_texts(payload))
        self.assertIn("Top 3", text)
        self.assertIn("Paper towels 0", text)
        self.assertIn("fits", text)
        self.assertIn("needs a signature", text)
        self.assertIn("blocked by the rules", text)
        buttons = actions(payload)
        self.assertEqual([button["action_id"] for button in buttons], ["veto_pick", "veto_pick"])
        self.assertEqual([button["value"] for button in buttons], ["abc123:0", "abc123:1"])
        labels = [button["text"]["text"] for button in buttons]
        self.assertEqual(labels, ["Choose", "Choose · needs signature"])

    def test_a_multi_unit_check_prices_the_line_and_says_so(self) -> None:
        candidate = self.candidate(0, "auto", amount=999)
        candidate["qty"] = 2
        candidate["line_cents"] = 1_998
        candidate["unit_cents"] = 999
        payload = slack.check_payload(
            "abc", query="paper towels", agent_name="Pantry runner",
            qty=2, candidates=[candidate],
        )
        text = "\n".join(section_texts(payload))
        self.assertIn("$19.98", text)
        self.assertIn("2 × $9.99", text)
        self.assertIn("2 per item", text)

    def test_no_matches_is_an_honest_empty_state(self) -> None:
        payload = slack.check_payload(
            "abc", query="wagyu", agent_name="Pantry runner", qty=1, candidates=[],
        )
        text = "\n".join(section_texts(payload))
        self.assertIn("No match inside Pantry runner", text)
        self.assertEqual(actions(payload), [])


class BudgetTests(unittest.TestCase):
    def test_budget_shows_workspace_then_agents(self) -> None:
        payload = slack.budget_payload(
            agents=[
                {"name": "Pantry runner", "monthly_cap_cents": 80_000,
                 "spent_cents": 40_873, "committed_cents": 14_599},
                {"name": "Client lunch", "monthly_cap_cents": 0,
                 "spent_cents": 5_000, "committed_cents": 0},
            ],
            budget_cents=285_000,
            settled_cents=40_873,
            committed_cents=14_599,
        )
        text = "\n".join(section_texts(payload))
        self.assertIn("$408.73 settled", text)
        self.assertIn("$2,295.28", text)  # 2850.00 - 408.73 - 145.99
        self.assertIn("Pantry runner", text)
        self.assertIn("$408.73 of $800.00 spent", text)
        self.assertIn("$245.28 left", text)
        self.assertIn("Client lunch", text)
        self.assertIn("$50.00 spent", text)
        self.assertIn("Only captured money counts as settled", text)

    def test_budget_without_a_workspace_budget_omits_left(self) -> None:
        payload = slack.budget_payload(agents=[], budget_cents=0,
                                       settled_cents=1_000, committed_cents=0)
        text = "\n".join(section_texts(payload))
        self.assertNotIn("*Left*", text)


class RoutineTests(unittest.TestCase):
    def test_routines_show_next_run_envelope_and_last_result(self) -> None:
        payload = slack.routines_payload([
            {
                "name": "Pantry restock",
                "agent_name": "Pantry runner",
                "cadence_word": "every week",
                "next_run_at": iso(days=-6),
                "envelope_cents": 25_000,
                "spent_cents": 1_815,
                "last": {"outcome": "captured", "amount_cents": 1_815,
                         "request_id": "REQ-1041", "started_at": iso(days=1)},
            },
        ])
        text = "\n".join(section_texts(payload))
        self.assertIn("1 standing job", text)
        self.assertIn("Pantry restock", text)
        self.assertIn("every week", text)
        self.assertRegex(text, r"in [56] d")
        self.assertIn("$18.15 of $250.00 this month", text)
        self.assertIn("ordered and captured", text)
        self.assertIn("REQ-1041", text)
        self.assertIn("1 d ago", text)

    def test_a_routine_with_no_run_says_so(self) -> None:
        payload = slack.routines_payload([{
            "name": "Desk kit top-up", "agent_name": "Desk kit",
            "cadence_word": "every month", "next_run_at": iso(days=-30),
            "envelope_cents": 0, "spent_cents": 0, "last": None,
        }])
        text = "\n".join(section_texts(payload))
        self.assertIn("No run yet", text)
        self.assertIn("every month", text)

    def test_no_routines_is_an_honest_empty_state(self) -> None:
        text = "\n".join(section_texts(slack.routines_payload([])))
        self.assertIn("No standing jobs yet", text)


class FilterTests(unittest.TestCase):
    def test_match_agents_by_name_tag_or_none(self) -> None:
        agents = [
            {"id": "a1", "name": "Pantry runner", "tag": "pantry"},
            {"id": "a2", "name": "Desk kit", "tag": "equipment"},
        ]
        self.assertEqual(slack.match_agents("pantry", agents), {"a1"})
        self.assertEqual(slack.match_agents("DESK", agents), {"a2"})
        self.assertEqual(slack.match_agents("  ", agents), None)
        self.assertEqual(slack.match_agents("nobody", agents), set())


class FormattingTests(unittest.TestCase):
    def test_money_is_always_two_decimals_and_commas(self) -> None:
        self.assertEqual(slack.money(0), "$0.00")
        self.assertEqual(slack.money(14599), "$145.99")
        self.assertEqual(slack.money(285000), "$2,850.00")
        self.assertIn("min ago", slack.ago(iso(minutes=5)))
        self.assertIn("d ago", slack.ago(iso(days=3)))
        self.assertIn("due now", slack.in_words(iso(minutes=2)))


if __name__ == "__main__":
    unittest.main()
