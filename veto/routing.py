"""Model routing: which agent owns a plain-language purchase request.

The Slack channel is where a /veto was typed, not who should answer it. When an
entry point does not name an agent, the model reads the request against the
workspace's agents (what each may buy, where it may pay, its tag and cap) and
returns one id from that list.

Safety is deterministic, not model-shaped:
  * only ids from the workspace's own agent list can be returned;
  * a malformed answer, a model error or an unknown id falls back to the
    workspace default agent (never to a channel match);
  * a workspace with one agent short-circuits without a model call.

Tests inject ``llm``; production uses ``legacy_engine.deepseek``.
"""
from __future__ import annotations

import json

from veto import engine as legacy_engine

MAX_REASON = 160
MAX_TEXT = 400
MAX_LIST = 8

SYSTEM = """You route a purchase request to the one Veto agent whose spending charter should judge it.

You get the request text and the workspace's agents. For each agent you get:
  id, name, tag, categories (what it may buy; empty means any category),
  merchants (where it may pay; empty means it currently cannot buy anything),
  monthly_cap_cents (its budget).

Choose the single agent whose scope fits what is being bought:
- food, drinks, cleaning and pantry supplies -> the pantry agent;
- monitors, keyboards, furniture, studio equipment -> the equipment agent;
- pens, paper, stationery or facilities supplies -> the agent covering those;
- client meals, hospitality and travel extras -> the hospitality agent.
Judge by the products and by which agent is allowed to buy them, never by where the
message was typed. Prefer an agent with approved merchants when one fits.
Use only an id from the list. If nothing fits, return an empty agent_id.

Reply with JSON only, no prose:
{"agent_id": "<id>", "confidence": 0.0, "reason": "<12 words"}"""


def _agent_brief(agent: dict) -> dict:
    """The smallest honest description of an agent for one routing decision."""
    categories = list(agent.get("categories") or [])[:MAX_LIST]
    merchants = list(agent.get("vendors") or [])[:MAX_LIST]
    return {
        "id": agent.get("id"),
        "name": agent.get("name") or "",
        "tag": agent.get("tag") or "",
        "categories": categories,
        "merchants": merchants,
        "monthly_cap_cents": agent.get("monthly_cap_cents"),
    }


def _reason(value) -> str:
    text = str(value or "").strip().replace("\n", " ")
    return text[:MAX_REASON]


def _confidence(value) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def choose_agent(
    agents: list[dict],
    text: str,
    *,
    llm=None,
) -> dict:
    """Ask the model for one agent id. Never invents an agent; never raises.

    Returns ``{"agent_id", "reason", "confidence", "source"}`` where source is
    ``model``, ``single``, ``none`` or ``invalid``. Callers decide the fallback.
    """
    if not agents:
        return {"agent_id": None, "reason": "this workspace has no agents",
                "confidence": 0.0, "source": "none"}
    if len(agents) == 1:
        only = agents[0]
        return {
            "agent_id": only.get("id"),
            "reason": "the only agent in the workspace",
            "confidence": 1.0,
            "source": "single",
            "agent_name": only.get("name"),
        }

    payload = {
        "request": str(text or "").strip()[:MAX_TEXT],
        "agents": [_agent_brief(agent) for agent in agents],
    }
    try:
        raw = (llm or legacy_engine.deepseek)(SYSTEM, json.dumps(payload, ensure_ascii=False))
        if isinstance(raw, str):
            raw = json.loads(raw)
            if isinstance(raw, str):
                raw = json.loads(raw)
        if not isinstance(raw, dict):
            raise ValueError("routing answer is not an object")
        parsed = raw
    except Exception:  # noqa: BLE001 - a model failure must not break the request
        return {
            "agent_id": None,
            "reason": "the model did not answer clearly",
            "confidence": 0.0,
            "source": "invalid",
        }

    wanted = str(parsed.get("agent_id") or "").strip()
    match = next((agent for agent in agents if agent.get("id") == wanted), None)
    if match is None:
        return {
            "agent_id": None,
            "reason": "the model named an agent outside the workspace",
            "confidence": 0.0,
            "source": "invalid",
        }
    return {
        "agent_id": match["id"],
        "agent_name": match.get("name"),
        "reason": _reason(parsed.get("reason")) or "the model matched the scope",
        "confidence": _confidence(parsed.get("confidence")),
        "source": "model",
    }


def route_request(
    store,
    workspace: dict,
    text: str,
    *,
    llm=None,
) -> tuple[dict | None, dict]:
    """Resolve the agent for a request that did not name one.

    Returns ``(agent, decision)``. ``decision`` carries ``source`` and the
    ``reason`` the record's trail will show. The fallback is the workspace
    default agent — a channel is never consulted here.
    """
    agents = store.list_agents(workspace["id"])
    # A paused agent is not a candidate: it cannot spend. When every agent is
    # paused the fallback still names one, and the intake refuses it in words.
    active = [agent for agent in agents if (agent.get("status") or "active") == "active"]
    candidates = active or agents
    decision = choose_agent(candidates, text, llm=llm)
    chosen = next(
        (agent for agent in candidates if agent.get("id") == decision.get("agent_id")),
        None,
    )
    if chosen is not None:
        decision["agent_name"] = chosen.get("name")
        return chosen, decision

    default_id = workspace.get("default_agent_id")
    fallback = next(
        (agent for agent in candidates if agent.get("id") == default_id),
        candidates[0] if candidates else None,
    )
    if fallback is None:
        return None, decision
    decision = {
        **decision,
        "agent_id": fallback.get("id"),
        "agent_name": fallback.get("name"),
        "source": "default",
    }
    return fallback, decision


def trail_note(decision: dict) -> dict:
    """The event a record keeps for its trail, in the product's voice."""
    reason = decision.get("reason") or "no reason given"
    if decision.get("source") == "model":
        confidence = decision.get("confidence") or 0.0
        detail = f"the model read the request · {reason} · {round(confidence * 100)}%"
    elif decision.get("source") == "single":
        detail = "the only agent in this workspace"
    elif decision.get("source") == "default":
        detail = f"fallback to the workspace default · {reason}"
    else:
        detail = f"fallback to the workspace default · {reason}"
    return {
        "kind": "route",
        "label": f"Routed to {decision.get('agent_name') or 'an agent'}",
        "detail": detail,
    }
