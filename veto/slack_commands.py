"""The Slack command surface: one URL, several commands.

`/veto` is the buying door. The rest are the manager's view of the same system:
what is waiting for a signature, what this month has cost, and which standing
jobs run next. One endpoint serves them all — Slack sends the invoked `command`
in the payload, so no extra route or Slack app is needed per command.

Everything here is a pure formatter over plain dicts, so the payloads can be
tested without Slack, a socket, or a workspace.
"""
from __future__ import annotations

from datetime import datetime, timezone

# The command set, as it appears in the Slack app settings and in /veto-help.
# One Request URL for all of them: the `command` field in the payload tells
# them apart. `usage_hint` is the text Slack shows beside the command while a
# person is typing it — keep it about the argument, not the feature.
COMMANDS = (
    {
        "command": "/veto",
        "description": "Order supplies through the team's spending rules.",
        "usage_hint": "what to buy — e.g. 2 packs of toilet paper",
        "example": "/veto 2 packs of toilet paper",
    },
    {
        "command": "/veto-check",
        "description": "Show the top five matches so you can pick one or refine.",
        "usage_hint": "what to look for — e.g. paper towels",
        "example": "/veto-check paper towels",
    },
    {
        "command": "/veto-status",
        "description": "Show what is waiting for a signature.",
        "usage_hint": "[agent] — leave empty for everything",
        "example": "/veto-status",
    },
    {
        "command": "/veto-budget",
        "description": "Show this month's money: settled, waiting, left.",
        "usage_hint": "[agent] — leave empty for the whole workspace",
        "example": "/veto-budget",
    },
    {
        "command": "/veto-routines",
        "description": "Show the agents' standing jobs and when they run next.",
        "usage_hint": "[agent] — leave empty for every routine",
        "example": "/veto-routines",
    },
    {
        "command": "/veto-help",
        "description": "Show what Veto can do from Slack.",
        "usage_hint": "",
        "example": "/veto-help",
    },
)

OUTCOME_WORD = {
    "captured": "ordered and captured",
    "awaiting": "escalated for a signature",
    "approved": "approved, capture owed",
    "blocked": "refused by the rules",
    "skipped": "skipped",
    "failed": "failed",
}


# ---------------------------------------------------------------------------
# small formatters
# ---------------------------------------------------------------------------
def money(cents: int | None) -> str:
    return "${:,.2f}".format((cents or 0) / 100)


def _month_label() -> str:
    return datetime.now(timezone.utc).strftime("%B %Y")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def ago(value: str | None) -> str:
    moment = _parse(value)
    if moment is None:
        return "just now"
    seconds = max(0, int((datetime.now(timezone.utc) - moment).total_seconds()))
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} d ago"


def when(value: str | None) -> str:
    moment = _parse(value)
    if moment is None:
        return "not scheduled"
    local = moment.astimezone()
    return f"{local.strftime('%a')} {local.day} {local.strftime('%b')} {local:%H:%M}"


def in_words(value: str | None) -> str:
    moment = _parse(value)
    if moment is None:
        return "not scheduled"
    seconds = int((moment - datetime.now(timezone.utc)).total_seconds())
    if seconds <= 0:
        return "due now"
    if seconds < 3600:
        return f"in {max(1, seconds // 60)} min"
    if seconds < 86400:
        return f"in {seconds // 3600} h"
    return f"in {seconds // 86400} d"


def match_agents(text: str, agents: list[dict]) -> set[str] | None:
    """Optional agent filter: a name or tag fragment. None means 'everything'."""
    needle = (text or "").strip().lower()
    if not needle:
        return None
    hits = set()
    for agent in agents:
        name = str(agent.get("name") or "").lower()
        tag = str(agent.get("tag") or "").lower()
        if needle in name or needle in tag or name in needle:
            hits.add(agent["id"])
    return hits


# ---------------------------------------------------------------------------
# blocks
# ---------------------------------------------------------------------------
def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def _payload(fallback: str, blocks: list[dict]) -> dict:
    return {"response_type": "ephemeral", "text": fallback, "blocks": blocks}


def _commands_url(base_url: str | None) -> str | None:
    return f"{base_url.rstrip('/')}/app" if base_url else None


# ---------------------------------------------------------------------------
# /veto-help
# ---------------------------------------------------------------------------
def help_payload(base_url: str | None = None) -> dict:
    lines = []
    for command in COMMANDS:
        if command["command"] == "/veto-help":
            continue
        if command["usage_hint"]:
            lines.append(f"`{command['command']}`  {command['description']}")
        else:
            lines.append(f"`{command['command']}`  {command['description']}")
    blocks = [
        _section("*Veto from Slack*"),
        _section("\n".join(lines)),
        _context("Anything an agent proposes still has to pass its charter before money moves."),
    ]
    console = _commands_url(base_url)
    if console:
        blocks.append(_context(f"Console · {console}"))
    return _payload("Veto commands", blocks)


# ---------------------------------------------------------------------------
# /veto-check
# ---------------------------------------------------------------------------
def check_payload(
    check_id: str,
    *,
    query: str,
    agent_name: str,
    qty: int,
    candidates: list[dict],
    source: str = "Channel3",
    checked: int = 1,
    base_url: str | None = None,
) -> dict:
    """The shortlist: what the agent's search returned, and what a click would buy."""
    if not candidates:
        blocks = [
            _section(f"*No match inside {agent_name}’s rules.*"),
            _context(
                f"The catalogue returned nothing for “{query}”. Try different words, "
                "or widen the agent's scope in the console."
            ),
        ]
    else:
        blocks = [_section(f"*Top {len(candidates)} for “{query}”*")]
        note = f"{agent_name} · {source}"
        if checked > 1:
            note += f" · checked the first of {checked} items in the message"
        if qty > 1:
            note += f" · {qty} per item"
        blocks.append(_context(note))
        for candidate in candidates:
            title = candidate.get("title") or "untitled"
            url = candidate.get("url") or ""
            heading = f"*{candidate['index'] + 1} · {f'<{url}|{title}>' if url else title}*"
            amount = money(candidate.get("line_cents"))
            if candidate.get("qty", 1) > 1:
                amount += f" ({candidate['qty']} × {money(candidate.get('unit_cents'))})"
            verdict = candidate.get("verdict") or "block"
            if verdict == "auto":
                word = "fits"
            elif verdict == "review":
                word = "needs a signature"
            else:
                word = "blocked by the rules"
            meta = " · ".join([
                candidate.get("vendor") or "unknown vendor",
                candidate.get("category") or "uncategorised",
                word,
            ])
            blocks.append(_section(f"{heading} — {amount}\n{meta}"))
            if verdict != "block":
                label = "Choose" if verdict == "auto" else "Choose · needs signature"
                blocks.append({
                    "type": "actions",
                    "elements": [{
                        "type": "button",
                        "text": {"type": "plain_text", "text": label},
                        "style": "primary",
                        "action_id": "veto_pick",
                        "value": f"{check_id}:{candidate['index']}",
                    }],
                })
        blocks.append(_context(
            "Choosing prices that exact offer and runs the same rules; nothing is ordered "
            "until you choose."
        ))
    console = _commands_url(base_url)
    if console:
        blocks.append(_context(f"Console · {console}"))
    return _payload(f"Top matches for {query}", blocks)


# ---------------------------------------------------------------------------
# /veto-status
# ---------------------------------------------------------------------------
def status_payload(
    pending: list[dict],
    *,
    committed_cents: int = 0,
    capture_owed: int = 0,
    next_routine: dict | None = None,
    base_url: str | None = None,
) -> dict:
    blocks: list[dict] = []
    if pending:
        noun = "request" if len(pending) == 1 else "requests"
        verb = "waits" if len(pending) == 1 else "wait"
        blocks.append(_section(f"*{len(pending)} {noun} {verb} for a signature*"))
        bits = [f"{money(committed_cents)} committed"]
        if capture_owed:
            bits.append(f"{capture_owed} approved, capture owed")
        blocks.append(_context(" · ".join(bits)))
        for row in pending:
            title = row.get("title") or ", ".join(
                line.get("title", "") for line in row.get("lines") or []
            )
            lines = f"*{row['id']} · {row.get('agent_name') or 'agent'}* — {money(row.get('total_cents'))}"
            count = row.get("line_count") or len(row.get("lines") or [])
            detail = f"{count} item{'' if count == 1 else 's'} · asked {ago(row.get('created_at'))}"
            if title:
                detail = f"{title} · {detail}"
            blocks.append(_section(f"{lines}\n{detail}"))
            blocks.append({
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "action_id": "veto_approve",
                        "value": row["id"],
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Veto"},
                        "style": "danger",
                        "action_id": "veto_veto",
                        "value": row["id"],
                    },
                ],
            })
        blocks.append(_context("Approve captures through PayPal; Veto moves no money."))
    else:
        blocks.append(_section("*Nothing is waiting on you.*"))
        blocks.append(_context("Every request is decided."))
        if next_routine:
            blocks.append(_context(
                f"Next standing job: *{next_routine.get('name')}* "
                f"({next_routine.get('agent_name') or 'agent'}), "
                f"next {in_words(next_routine.get('next_run_at'))}."
            ))
    console = _commands_url(base_url)
    if console:
        blocks.append(_context(f"Console · {console}"))
    return _payload("Waiting on you", blocks)


# ---------------------------------------------------------------------------
# /veto-budget
# ---------------------------------------------------------------------------
def budget_payload(
    *,
    agents: list[dict],
    budget_cents: int = 0,
    settled_cents: int = 0,
    committed_cents: int = 0,
    base_url: str | None = None,
) -> dict:
    blocks = [
        _section(f"*{_month_label()}* · {money(settled_cents)} settled"),
    ]
    left = max(0, int(budget_cents or 0) - settled_cents - committed_cents)
    fields = [
        {"type": "mrkdwn", "text": f"*Settled*\n{money(settled_cents)}"},
        {"type": "mrkdwn", "text": f"*Waiting*\n{money(committed_cents)}"},
    ]
    if budget_cents:
        fields.append({"type": "mrkdwn", "text": f"*Left*\n{money(left)} of {money(budget_cents)}"})
    blocks.append({"type": "section", "fields": fields})
    if agents:
        blocks.append({"type": "divider"})
        for agent in agents:
            cap = int(agent.get("monthly_cap_cents") or 0)
            spent = int(agent.get("spent_cents") or 0)
            waiting = int(agent.get("committed_cents") or 0)
            if cap:
                line = (f"*{agent.get('name')}* — {money(spent)} of {money(cap)} spent · "
                        f"{money(max(0, cap - spent - waiting))} left")
            else:
                line = f"*{agent.get('name')}* — {money(spent)} spent"
            blocks.append(_section(line))
    blocks.append(_context("Only captured money counts as settled; waiting is held by the rules."))
    console = _commands_url(base_url)
    if console:
        blocks.append(_context(f"Console · {console}"))
    return _payload("This month's money", blocks)


# ---------------------------------------------------------------------------
# /veto-routines
# ---------------------------------------------------------------------------
def routines_payload(routines: list[dict], *, base_url: str | None = None) -> dict:
    blocks: list[dict] = []
    if not routines:
        blocks.append(_section("*No standing jobs yet.*"))
        blocks.append(_context("A routine is an agent's recurring work — set one up on its page."))
    else:
        blocks.append(_section(f"*{len(routines)} standing job"
                               f"{'' if len(routines) == 1 else 's'}*"))
        for routine in routines:
            envelope = int(routine.get("envelope_cents") or 0)
            spent = int(routine.get("spent_cents") or 0)
            head = (f"*{routine.get('name')}* · {routine.get('agent_name') or 'agent'}\n"
                    f"_{routine.get('cadence_word') or 'on a schedule'}_ · "
                    f"next {when(routine.get('next_run_at'))} ({in_words(routine.get('next_run_at'))})")
            if envelope:
                head += f"\n{money(spent)} of {money(envelope)} this month"
            blocks.append(_section(head))
            last = routine.get("last")
            if last:
                word = OUTCOME_WORD.get(last.get("outcome"), last.get("outcome") or "ran")
                detail = f"Last run {ago(last.get('started_at'))}: {word}"
                if last.get("amount_cents"):
                    detail += f" · {money(last.get('amount_cents'))}"
                if last.get("request_id"):
                    detail += f" · {last.get('request_id')}"
                blocks.append(_context(detail))
            else:
                blocks.append(_context("No run yet."))
        blocks.append(_context("Runs use the same charter; a run past the envelope asks a human first."))
    console = _commands_url(base_url)
    if console:
        blocks.append(_context(f"Console · {console}"))
    return _payload("Standing jobs", blocks)
