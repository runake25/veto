"""Veto-check tests: the shortlist, the fit flags and the pick tokens.

No network: extraction and the scoped search are injected. The cache is
process-local, so the tests clear it and exercise expiry explicitly.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from veto import check  # noqa: E402


def agent() -> dict:
    return {
        "id": "agt_desk",
        "workspace_id": "ws_1",
        "name": "Desk kit",
        "categories": ["stationery"],
        "vendors": ["staples.com"],
    }


def extract_pen(qty: int = 1):
    return lambda text: {
        "items": [{"name": "pens", "qty": qty, "query": "ballpoint pens"}]
    }


def search_five(agent_dict, query, qty):
    rows = []
    for index in range(7):
        unit = 500 + index * 100
        verdict = "auto" if index < 3 else ("review" if index < 5 else "block")
        rows.append({
            "sku": f"sku-{index}",
            "title": f"Pens model {index}",
            "qty": qty,
            "unit_cents": unit,
            "line_cents": unit * qty,
            "vendor": "staples.com",
            "category": "stationery",
            "brand": "Veto",
            "image": "",
            "url": f"https://staples.example/pens-{index}",
            "source": "channel3",
            "fits": {"category": verdict != "block", "per_order": True,
                     "per_unit": verdict != "block", "threshold": verdict == "auto"},
            "verdict": verdict,
            "reasons": [] if verdict == "auto" else [{"rule": "approval_threshold"}],
        })
    return {
        "agent": agent_dict,
        "query": query,
        "qty": qty,
        "source": "channel3",
        "filtered": {"irrelevant": 2, "category": 0},
        "results": rows,
    }


class CandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        check.clear()

    def test_keeps_five_and_carries_the_quantity(self) -> None:
        result = check.top_candidates(
            agent(), "2 boxes of pens",
            extract_fn=extract_pen(2), search_fn=search_five,
        )
        self.assertEqual(result["query"], "ballpoint pens")
        self.assertEqual(result["qty"], 2)
        self.assertEqual(len(result["candidates"]), check.MAX_CANDIDATES)
        first = result["candidates"][0]
        self.assertEqual(first["title"], "Pens model 0")
        self.assertEqual(first["qty"], 2)
        self.assertEqual(first["line_cents"], 1_000)
        self.assertEqual(first["verdict"], "auto")
        self.assertTrue(first["fits"]["category"])
        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["source"], "channel3")

    def test_keeps_each_verdict_and_reason(self) -> None:
        result = check.top_candidates(agent(), "pens", extract_fn=extract_pen(),
                                      search_fn=search_five)
        verdicts = [row["verdict"] for row in result["candidates"]]
        self.assertEqual(verdicts, ["auto", "auto", "auto", "review", "review"])
        self.assertEqual(result["candidates"][3]["reasons"][0]["rule"],
                         "approval_threshold")

    def test_a_message_with_no_intent_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            check.top_candidates(agent(), "hello there",
                                 extract_fn=lambda text: {"items": []},
                                 search_fn=search_five)

    def test_a_check_uses_only_the_first_item(self) -> None:
        def extract_two(text):
            return {"items": [{"name": "pens", "qty": 1},
                              {"name": "envelopes", "qty": 1}]}

        result = check.top_candidates(agent(), "pens and envelopes",
                                      extract_fn=extract_two, search_fn=search_five)
        self.assertEqual(result["qty"], 1)
        self.assertEqual(result["checked"], 2)
        self.assertTrue(result["candidates"])


class RememberTests(unittest.TestCase):
    def setUp(self) -> None:
        check.clear()

    def remember(self) -> tuple[str, dict]:
        return check.remember(agent(), "2 boxes of pens",
                              extract_fn=extract_pen(2), search_fn=search_five)

    def test_a_pick_resolves_to_the_priced_line(self) -> None:
        check_id, result = self.remember()
        picked = check.resolve(check_id, 1)
        self.assertIsNotNone(picked)
        self.assertEqual(picked["agent"]["id"], "agt_desk")
        self.assertEqual(picked["text"], "2 boxes of pens")
        line = picked["line"]
        self.assertEqual(line["title"], "Pens model 1")
        self.assertEqual(line["qty"], 2)
        self.assertEqual(line["line_cents"], 1_200)
        self.assertEqual(line["vendor"], "staples.com")
        self.assertEqual(line["source"], "channel3")

    def test_an_unknown_check_or_index_is_not_a_pick(self) -> None:
        self.assertIsNone(check.resolve("nope", 0))
        check_id, _ = self.remember()
        self.assertIsNone(check.resolve(check_id, 99))
        self.assertIsNone(check.resolve(check_id, "x"))

    def test_a_check_expires(self) -> None:
        check_id, _ = self.remember()
        check.prune(now=10**12)
        self.assertIsNone(check.resolve(check_id, 0))
        self.assertEqual(check._CHECKS, {})

    def test_clear_drops_everything(self) -> None:
        self.remember()
        check.clear()
        self.assertEqual(check._CHECKS, {})


if __name__ == "__main__":
    unittest.main()
