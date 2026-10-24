"""Model routing tests: the model picks the agent, deterministic guards keep it honest.

No network: every routing call gets an injected ``llm``. The one record-level test
injects the pipeline's extract/search/choose functions and a fake settlement, so
nothing here spends money or calls a model.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_requests import (  # noqa: E402 - shared fixtures, same test package
    FakeSettlement,
    agent_fields,
    choose_first,
    extract_one,
    search_one,
)

from veto import requests as veto_requests  # noqa: E402
from veto import routing as veto_routing  # noqa: E402
from veto.policy import RULE_KINDS  # noqa: E402
from veto.store import Store  # noqa: E402

AGENTS = [
    {
        "id": "agt_pantry",
        "name": "Pantry runner",
        "tag": "pantry",
        "categories": ["pantry"],
        "vendors": ["amazon.com"],
        "monthly_cap_cents": 80_000,
        "channel": "#pantry",
    },
    {
        "id": "agt_desk",
        "name": "Desk kit",
        "tag": "equipment",
        "categories": ["equipment"],
        "vendors": ["walmart.com"],
        "monthly_cap_cents": 120_000,
        "channel": "#studio",
    },
]

DEFAULT_AGENT = {"id": "agt_pantry", "name": "Pantry runner", "tag": "pantry"}


class FakeStore:
    def __init__(self, agents: list[dict]) -> None:
        self._agents = agents

    def list_agents(self, workspace_id: str) -> list[dict]:
        return list(self._agents)


def llm_returning(**payload):
    calls: list[tuple[str, str]] = []

    def llm(system: str, prompt: str) -> str:
        calls.append((system, prompt))
        return json.dumps(payload)

    llm.calls = calls  # type: ignore[attr-defined]
    return llm


def llm_raising(exc: Exception | None = None):
    def llm(system: str, prompt: str) -> str:
        raise exc or RuntimeError("model down")

    return llm


class ChooseAgentTests(unittest.TestCase):
    def test_model_pick_is_returned(self) -> None:
        llm = llm_returning(agent_id="agt_desk", confidence=0.82, reason="studio equipment")
        decision = veto_routing.choose_agent(AGENTS, "a monitor for the studio", llm=llm)
        self.assertEqual(decision["agent_id"], "agt_desk")
        self.assertEqual(decision["agent_name"], "Desk kit")
        self.assertEqual(decision["source"], "model")
        self.assertAlmostEqual(decision["confidence"], 0.82)
        self.assertEqual(decision["reason"], "studio equipment")

    def test_prompt_offers_scope_not_channels(self) -> None:
        llm = llm_returning(agent_id="agt_pantry", confidence=0.9, reason="pantry")
        veto_routing.choose_agent(AGENTS, "2 packs of bamboo toilet paper", llm=llm)
        system, prompt = llm.calls[0]
        self.assertIn("agent_id", system)
        payload = json.loads(prompt)
        self.assertEqual(payload["request"], "2 packs of bamboo toilet paper")
        self.assertEqual([row["id"] for row in payload["agents"]],
                         ["agt_pantry", "agt_desk"])
        self.assertEqual(payload["agents"][0]["categories"], ["pantry"])
        # a channel is where the message was typed; it must not be a routing input
        self.assertNotIn("#pantry", prompt)
        self.assertNotIn("channel", prompt)

    def test_unknown_id_is_never_accepted(self) -> None:
        decision = veto_routing.choose_agent(
            AGENTS, "2 packs of toilet paper",
            llm=llm_returning(agent_id="agt_elsewhere", confidence=0.99, reason="other workspace"),
        )
        self.assertIsNone(decision["agent_id"])
        self.assertEqual(decision["source"], "invalid")

    def test_malformed_answer_is_invalid(self) -> None:
        decision = veto_routing.choose_agent(
            AGENTS, "anything", llm=lambda system, prompt: "not json at all"
        )
        self.assertEqual(decision["source"], "invalid")
        self.assertIsNone(decision["agent_id"])

    def test_model_error_is_invalid(self) -> None:
        decision = veto_routing.choose_agent(AGENTS, "anything", llm=llm_raising())
        self.assertEqual(decision["source"], "invalid")
        self.assertIsNone(decision["agent_id"])

    def test_single_agent_short_circuits_without_a_model_call(self) -> None:
        decision = veto_routing.choose_agent(
            [AGENTS[0]], "2 packs of toilet paper", llm=llm_raising()
        )
        self.assertEqual(decision["agent_id"], "agt_pantry")
        self.assertEqual(decision["source"], "single")

    def test_empty_workspace_is_none(self) -> None:
        decision = veto_routing.choose_agent([], "anything", llm=llm_raising())
        self.assertEqual(decision["source"], "none")
        self.assertIsNone(decision["agent_id"])

    def test_reason_is_trimmed_and_confidence_clamped(self) -> None:
        decision = veto_routing.choose_agent(
            AGENTS, "pens",
            llm=llm_returning(agent_id="agt_desk", confidence=7, reason="x" * 400),
        )
        self.assertEqual(len(decision["reason"]), veto_routing.MAX_REASON)
        self.assertEqual(decision["confidence"], 1.0)
        low = veto_routing.choose_agent(
            AGENTS, "pens",
            llm=llm_returning(agent_id="agt_desk", confidence=-3, reason="pens"),
        )
        self.assertEqual(low["confidence"], 0.0)


class RouteRequestTests(unittest.TestCase):
    def test_model_choice_is_used_and_named(self) -> None:
        store = FakeStore(AGENTS)
        workspace = {"id": "ws_1", "default_agent_id": "agt_pantry"}
        agent, decision = veto_routing.route_request(
            store, workspace, "a monitor for the studio",
            llm=llm_returning(agent_id="agt_desk", confidence=0.8, reason="equipment"),
        )
        self.assertEqual(agent["id"], "agt_desk")
        self.assertEqual(decision["agent_name"], "Desk kit")
        self.assertEqual(decision["source"], "model")

    def test_fallback_is_the_workspace_default_never_a_channel(self) -> None:
        store = FakeStore(AGENTS)
        workspace = {"id": "ws_1", "default_agent_id": "agt_pantry"}
        agent, decision = veto_routing.route_request(
            store, workspace, "a monitor for the studio",
            llm=llm_returning(agent_id="agt_ghost", confidence=0.9, reason="nonsense"),
        )
        self.assertEqual(agent["id"], "agt_pantry")
        self.assertEqual(decision["source"], "default")

    def test_fallback_without_a_default_uses_the_first_agent(self) -> None:
        store = FakeStore(AGENTS)
        agent, decision = veto_routing.route_request(
            store, {"id": "ws_1", "default_agent_id": None}, "a monitor",
            llm=llm_raising(),
        )
        self.assertEqual(agent["id"], "agt_pantry")
        self.assertEqual(decision["source"], "default")

    def test_trail_note_says_what_happened(self) -> None:
        model_note = veto_routing.trail_note({
            "agent_name": "Desk kit", "source": "model",
            "reason": "equipment", "confidence": 0.82,
        })
        self.assertEqual(model_note["kind"], "route")
        self.assertEqual(model_note["label"], "Routed to Desk kit")
        self.assertIn("the model read the request", model_note["detail"])
        self.assertIn("82%", model_note["detail"])

        fallback = veto_routing.trail_note({
            "agent_name": "Pantry runner", "source": "default",
            "reason": "the model did not answer clearly", "confidence": 0.0,
        })
        self.assertIn("fallback to the workspace default", fallback["detail"])


class RecordRouteEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Store(Path(self._tmp.name) / "veto.db")
        self.addCleanup(self.store.close)
        self.workspace = self.store.create_workspace("Bracken & Field")
        self.agent = self.store.create_agent(
            self.workspace["id"],
            agent_fields(),
            vendors=["amazon.com"],
            categories=["pantry"],
            rules=list(RULE_KINDS),
            overrides=[],
        )

    def test_route_event_is_the_first_step_in_the_trail(self) -> None:
        note = veto_routing.trail_note({
            "agent_name": "Pantry runner", "source": "model",
            "reason": "pantry supplies", "confidence": 0.9,
        })
        record = veto_requests.create(
            self.store,
            workspace=self.workspace,
            agent=self.agent,
            text="2 packs of bamboo toilet paper",
            origin="slack",
            channel="#ops",
            settlement=FakeSettlement(),
            route=note,
            extract_fn=extract_one(),
            search_fn=search_one(),
            choose_fn=choose_first,
        )
        events = record["events"]
        self.assertEqual(events[0]["kind"], "route")
        self.assertEqual(events[0]["label"], "Routed to Pantry runner")
        self.assertIn("the model read the request", events[0]["detail"])
        self.assertIn("capture", [event["kind"] for event in events])
        # the channel is provenance, not routing
        self.assertEqual(record["channel"], "#ops")
        self.assertEqual(record["status"], "captured")


if __name__ == "__main__":
    unittest.main()
