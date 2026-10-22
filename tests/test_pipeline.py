"""Pipeline tests: extraction fallback, scoped search, dry-run simulate.

Every network touch is injected or patched out, so the suite runs offline.
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from veto import pipeline
from veto.policy import RULE_KINDS
from veto.store import Store


def agent(**overrides) -> dict:
    base = {
        "id": "agt_00000001",
        "workspace_id": "ws_000000000001",
        "name": "Pantry runner",
        "tag": "pantry",
        "channel": "#pantry",
        "status": "active",
        "monthly_cap_cents": 80_000,
        "per_order_cap_cents": 30_000,
        "per_unit_cap_cents": 1_500,
        "approval_threshold_cents": 5_000,
        "vendors": ["amazon.com", "grove.co"],
        "categories": ["pantry"],
        "rules": list(RULE_KINDS),
        "overrides": [],
    }
    base.update(overrides)
    return base


def line(**overrides) -> dict:
    base = {
        "sku": "sku-1",
        "title": "Bamboo toilet paper, 24 rolls",
        "qty": 1,
        "unit_cents": 1_000,
        "line_cents": 1_000,
        "vendor": "amazon.com",
        "category": "pantry",
        "brand": "",
        "image": "",
        "url": "",
        "source": "local",
    }
    base.update(overrides)
    return base


def fake_extract(items: list[dict], source: str = "fallback") -> callable:
    return lambda text: {"items": items, "source": source}


def fake_search(lines_by_query: dict) -> callable:
    calls: list[tuple[str, int]] = []

    def search(agent_dict, query, qty):
        calls.append((query, qty))
        template = lines_by_query.get(query)
        if template is None:
            return []
        found = dict(template)
        found["qty"] = qty
        found["line_cents"] = found["unit_cents"] * qty
        found.setdefault("source", "local")
        return [{"line": found, "source": found["source"]}]

    search.calls = calls  # type: ignore[attr-defined]
    return search


CHANNEL3_PRODUCT = {
    "id": "c3-product-1",
    "title": "Bamboo Toilet Paper, 24 Rolls",
    "category": {"slug": "household-paper", "title": "Household Paper", "path": []},
    "brands": [{"name": "Bamboo"}],
    "images": [{"url": "https://img.example/paper.jpg", "is_main_image": True}],
    "offers": [{
        "domain": "amazon.com",
        "url": "https://amazon.com/dp/x",
        "availability": "InStock",
        "price": {"price": 14.5},
    }],
}


class FakeChannel3:
    def __init__(self, available: bool = True, products: list | None = None):
        self.available = available
        self.products = products or []
        self.calls: list[dict] = []

    def search(self, query, limit=6, max_price_cents=None, website_ids=None, user_id=None):
        self.calls.append({
            "query": query, "limit": limit, "max_price_cents": max_price_cents,
            "website_ids": website_ids,
        })
        return self.products


class ExtractTests(unittest.TestCase):
    def test_llm_extraction_used_when_available(self) -> None:
        with mock.patch.object(
            pipeline.legacy_engine, "deepseek",
            return_value={"items": [{"name": "toilet paper", "qty": 2}]},
        ):
            extracted = pipeline.extract_items("2 toilet paper")
        self.assertEqual(extracted["source"], "llm")
        self.assertEqual(extracted["items"], [{"name": "toilet paper", "qty": 2}])

    def test_falls_back_when_llm_unavailable(self) -> None:
        with mock.patch.object(
            pipeline.legacy_engine, "deepseek", side_effect=RuntimeError("offline")
        ):
            extracted = pipeline.extract_items("2 boxes of capsules and 3 packs of paper towels")
        self.assertEqual(extracted["source"], "fallback")
        self.assertEqual(extracted["items"][0]["qty"], 2)
        self.assertEqual(extracted["items"][1]["qty"], 3)
        self.assertIn("capsules", extracted["items"][0]["name"])
        self.assertIn("paper towels", extracted["items"][1]["name"])

    def test_falls_back_when_llm_returns_bad_json_shape(self) -> None:
        with mock.patch.object(pipeline.legacy_engine, "deepseek", return_value={"nope": 1}):
            extracted = pipeline.extract_items("coffee beans")
        self.assertEqual(extracted["source"], "fallback")
        self.assertEqual(extracted["items"][0]["name"], "coffee beans")

    def test_fallback_handles_command_prefix_and_filler(self) -> None:
        items = pipeline.fallback_extract("/veto coffee capsules and toilet paper")
        self.assertEqual([item["name"] for item in items], ["coffee capsules", "toilet paper"])
        self.assertEqual([item["qty"] for item in items], [1, 1])
        items = pipeline.fallback_extract("we're out of toilet paper")
        self.assertEqual(items, [{"name": "toilet paper", "qty": 1}])
        items = pipeline.fallback_extract("2 standing mats for the studio")
        self.assertEqual(items[0]["qty"], 2)
        self.assertIn("standing mats", items[0]["name"])

    def test_raises_only_when_nothing_is_extracted(self) -> None:
        with self.assertRaises(ValueError):
            pipeline.extract_items("")
        with self.assertRaises(ValueError):
            pipeline.extract_items("nothing")


class SearchTests(unittest.TestCase):
    def test_channel3_results_are_ranked_and_converted(self) -> None:
        fake = FakeChannel3(products=[CHANNEL3_PRODUCT])
        with mock.patch.object(pipeline, "Channel3", return_value=fake):
            candidates = pipeline.search_candidates(agent(), "toilet paper", qty=2)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["source"], "channel3")
        found = candidate["line"]
        self.assertEqual(found["source"], "channel3")
        self.assertEqual(found["title"], "Bamboo Toilet Paper, 24 Rolls")
        self.assertEqual(found["qty"], 2)
        self.assertEqual(found["unit_cents"], 1_450)
        self.assertEqual(found["line_cents"], 2_900)
        self.assertEqual(found["category"], "pantry")
        self.assertEqual(found["vendor"], "amazon.com")
        call = fake.calls[0]
        self.assertEqual(call["max_price_cents"], 30_000)
        self.assertEqual(call["website_ids"], ["amazon.com", "grove.co"])

    def test_irrelevant_channel3_hits_fall_back_to_local(self) -> None:
        machine = {
            **CHANNEL3_PRODUCT,
            "id": "c3-machine",
            "title": "Vertuo Coffee Machine",
        }
        fake = FakeChannel3(products=[machine])
        with mock.patch.object(pipeline, "Channel3", return_value=fake):
            candidates, filtered = pipeline._candidate_pool(agent(), "toilet paper", 1)
        self.assertEqual(filtered["irrelevant"], 1)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source"], "local")
        self.assertIn("toilet paper", candidates[0]["line"]["title"].lower())

    def test_local_catalogue_when_channel3_unavailable(self) -> None:
        fake = FakeChannel3(available=False)
        with mock.patch.object(pipeline, "Channel3", return_value=fake):
            candidates = pipeline.search_candidates(agent(), "toilet paper")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source"], "local")
        self.assertEqual(candidates[0]["line"]["unit_cents"], 1_450)
        self.assertEqual(fake.calls, [])

    def test_search_ceiling_is_bounded_by_agent_and_hard_cap(self) -> None:
        self.assertEqual(pipeline._search_ceiling_cents(agent(per_order_cap_cents=30_000)), 30_000)
        self.assertEqual(pipeline._search_ceiling_cents(agent(per_order_cap_cents=None)),
                         pipeline.HARD_PRICE_CEILING_CENTS)
        self.assertEqual(pipeline._search_ceiling_cents(agent(per_order_cap_cents=500_000)),
                         pipeline.HARD_PRICE_CEILING_CENTS)

    def test_scoped_search_annotates_results_and_keeps_category_misses(self) -> None:
        candidate = {"line": line(), "source": "channel3"}
        pool_result = ([candidate], {"irrelevant": 2, "category": 0})
        with mock.patch.object(pipeline, "_candidate_pool", return_value=pool_result):
            result = pipeline.scoped_search(agent(), "toilet paper")
        self.assertEqual(result["source"], "channel3")
        self.assertEqual(result["qty"], 1)
        self.assertEqual(result["scoped_by"],
                         {"vendors": ["amazon.com", "grove.co"], "max_price_cents": 30_000})
        self.assertEqual(result["filtered"], {"irrelevant": 2, "category": 0})
        found = result["results"][0]
        self.assertEqual(found["fits"],
                         {"category": True, "per_unit": True,
                          "per_order": True, "threshold": True})
        self.assertEqual(found["verdict"], "auto")
        self.assertEqual(found["reasons"], [])

        mismatch = {"line": line(category="equipment"), "source": "channel3"}
        with mock.patch.object(
            pipeline, "_candidate_pool",
            return_value=([mismatch], {"irrelevant": 0, "category": 1}),
        ):
            result = pipeline.scoped_search(agent(), "standing mat")
        found = result["results"][0]
        self.assertEqual(len(result["results"]), 1)  # not dropped
        self.assertFalse(found["fits"]["category"])
        self.assertEqual(found["verdict"], "block")
        self.assertEqual(found["reasons"][0]["rule"], "category_allowlist")

    def test_scoped_search_fits_honours_caps_and_overrides(self) -> None:
        review_line = {"line": line(unit_cents=2_000, line_cents=2_000),
                       "source": "local"}
        with mock.patch.object(pipeline, "_candidate_pool",
                               return_value=([review_line], {"irrelevant": 0, "category": 0})):
            result = pipeline.scoped_search(agent(), "toilet paper")
        found = result["results"][0]
        self.assertFalse(found["fits"]["per_unit"])
        self.assertEqual(found["verdict"], "review")

        loosened = agent(overrides=[{"category": "pantry", "kind": "per_unit_cap",
                                     "value_cents": 2_500}])
        with mock.patch.object(pipeline, "_candidate_pool",
                               return_value=([review_line], {"irrelevant": 0, "category": 0})):
            result = pipeline.scoped_search(loosened, "toilet paper")
        found = result["results"][0]
        self.assertTrue(found["fits"]["per_unit"])
        self.assertEqual(found["verdict"], "auto")

    def test_scoped_search_empty_pool(self) -> None:
        with mock.patch.object(pipeline, "_candidate_pool",
                               return_value=([], {"irrelevant": 0, "category": 0})):
            result = pipeline.scoped_search(agent(), "unicorn")
        self.assertEqual(result["source"], "none")
        self.assertEqual(result["results"], [])


class SimulateTests(unittest.TestCase):
    def run_simulate(self, text="toilet paper", agent_dict=None, items=None,
                     lines_by_query=None, search=None) -> dict:
        if search is None:
            mapping = {"toilet paper": line()} if lines_by_query is None else lines_by_query
            search = fake_search(mapping)
        return pipeline.simulate(
            agent_dict or agent(),
            text,
            extract_fn=fake_extract(items or [{"name": "toilet paper", "qty": 1}]),
            search_fn=search,
        )

    def test_auto_dry_run_shape(self) -> None:
        result = self.run_simulate()
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["total_cents"], 1_000)
        self.assertEqual(result["items"], [{"name": "toilet paper", "qty": 1}])
        self.assertEqual(len(result["lines"]), 1)
        self.assertEqual(result["decision"]["outcome"], "auto")
        self.assertEqual(result["decision"]["reasons"], [])
        self.assertEqual([event["kind"] for event in result["events"]],
                         ["extract", "catalogue", "price", "decision"])
        self.assertIsInstance(result["elapsed_ms"], int)
        self.assertEqual(result["source"], "local")

    def test_review_outcome(self) -> None:
        result = self.run_simulate(
            agent_dict=agent(per_unit_cap_cents=None),
            lines_by_query={"toilet paper": line(unit_cents=6_000, line_cents=6_000)},
        )
        self.assertEqual(result["decision"]["outcome"], "review")
        self.assertEqual(result["decision"]["reasons"][0]["rule"], "approval_threshold")

    def test_block_outcome(self) -> None:
        result = self.run_simulate(
            lines_by_query={"toilet paper": line(vendor="staples.com")},
        )
        self.assertEqual(result["decision"]["outcome"], "block")
        self.assertEqual(result["decision"]["reasons"][0]["rule"], "vendor_allowlist")

    def test_unresolved_item_becomes_catalogue_block(self) -> None:
        result = self.run_simulate(lines_by_query={})
        self.assertEqual(result["lines"], [])
        self.assertEqual(result["total_cents"], 0)
        self.assertEqual(result["decision"]["outcome"], "block")
        reason = result["decision"]["reasons"][0]
        self.assertEqual(reason["rule"], "catalogue")
        self.assertIn("toilet paper", reason["message"])
        self.assertEqual(result["source"], "local")

    def test_quantities_flow_into_lines_and_total(self) -> None:
        search = fake_search({"a": line(title="A", unit_cents=1_000),
                              "b": line(title="B", unit_cents=1_000)})
        result = self.run_simulate(
            items=[{"name": "a", "qty": 2}, {"name": "b", "qty": 3}],
            search=search,
        )
        self.assertEqual([found["qty"] for found in result["lines"]], [2, 3])
        self.assertEqual(result["total_cents"], 5_000)
        self.assertEqual(search.calls, [("a", 2), ("b", 3)])
        # 5000 is exactly at the threshold, so it is not over it.
        self.assertEqual(result["decision"]["outcome"], "auto")

    def test_catalogue_failure_is_unresolved_not_a_crash(self) -> None:
        def broken_search(agent_dict, query, qty):
            raise RuntimeError("catalogue exploded")

        result = self.run_simulate(search=broken_search)
        self.assertEqual(result["decision"]["outcome"], "block")
        self.assertEqual(result["decision"]["reasons"][0]["rule"], "catalogue")

    def test_mixed_sources_report_channel3(self) -> None:
        def search(agent_dict, query, qty):
            if query == "toilet paper":
                return [{"line": dict(line(), source="channel3"), "source": "channel3"}]
            return [{"line": dict(line(sku="sku-2"), source="local"), "source": "local"}]

        result = self.run_simulate(
            items=[{"name": "toilet paper", "qty": 1},
                   {"name": "coffee", "qty": 1}],
            search=search,
        )
        self.assertEqual(result["source"], "channel3")
        self.assertEqual(len(result["lines"]), 2)

    def test_empty_text_and_empty_extraction_raise(self) -> None:
        with self.assertRaises(ValueError):
            pipeline.simulate(agent(), "", extract_fn=fake_extract([]))
        with self.assertRaises(ValueError):
            pipeline.simulate(agent(), "toilet paper", extract_fn=fake_extract([]))

    def test_simulate_persists_nothing_and_never_builds_an_engine(self) -> None:
        store = Store(":memory:")
        try:
            workspace = store.create_workspace("Acme")
            pipeline_agent = store.create_agent(workspace["id"], {"name": "Pantry"})
            with mock.patch.object(
                pipeline.legacy_engine.Engine, "__init__", side_effect=AssertionError("paypal")
            ):
                result = pipeline.simulate(
                    pipeline_agent,
                    "toilet paper",
                    extract_fn=fake_extract([{"name": "toilet paper", "qty": 1}]),
                    search_fn=fake_search({"toilet paper": line()}),
                )
            self.assertTrue(result["dry_run"])
            self.assertEqual(store.count_agents(workspace["id"]), 1)
            self.assertEqual(
                store.one("SELECT COUNT(*) AS n FROM requests")["n"], 0
            )
            self.assertNotIn("paypal", json.dumps(result).lower())
        finally:
            store.close()


class ResolveAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.workspace = self.store.create_workspace("Acme")

    def make_agent(self, name: str, status: str = "active") -> dict:
        return self.store.create_agent(
            self.workspace["id"], {"name": name, "status": status}
        )

    def test_no_agents_raises_no_agent(self) -> None:
        with self.assertRaisesRegex(LookupError, "no_agent"):
            pipeline.resolve_agent(self.store, self.workspace["id"])

    def test_falls_back_to_first_active_agent(self) -> None:
        paused = self.make_agent("Paused", status="paused")
        active = self.make_agent("Active")
        resolved = pipeline.resolve_agent(self.store, self.workspace["id"])
        self.assertEqual(resolved["id"], active["id"])
        self.assertNotEqual(resolved["id"], paused["id"])

    def test_workspace_default_wins(self) -> None:
        first = self.make_agent("First")
        second = self.make_agent("Second")
        self.store.update_workspace(self.workspace["id"], default_agent_id=second["id"])
        self.assertEqual(
            pipeline.resolve_agent(self.store, self.workspace["id"])["id"], second["id"]
        )
        self.assertEqual(
            pipeline.resolve_agent(self.store, self.workspace["id"], first["id"])["id"],
            first["id"],
        )

    def test_explicit_unknown_or_cross_workspace_agent_raises(self) -> None:
        pipeline_agent = self.make_agent("Known")
        other = self.store.create_workspace("Other")
        with self.assertRaisesRegex(LookupError, "agent_not_found"):
            pipeline.resolve_agent(self.store, self.workspace["id"], "agt_deadbeef")
        with self.assertRaisesRegex(LookupError, "agent_not_found"):
            pipeline.resolve_agent(self.store, other["id"], pipeline_agent["id"])


if __name__ == "__main__":
    unittest.main()
