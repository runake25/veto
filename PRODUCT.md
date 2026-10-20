# Veto: product truth

Veto is a consent layer for agent spending. An AI agent can propose a purchase;
money only moves once the team's charter passes it and, when the charter says so,
a named human signs for it.

## Who it is for

An ops or finance approver at a 20-200 person company that already lets agents buy
things (a Slack bot that orders supplies, a procurement agent, a research agent
with a card). They are not a developer and not a treasurer. They open Veto a few
times a day, from a laptop or a phone, usually because a notification sent them.

## The one job on the ledger

*What needs my decision before money moves, and how much of this month is already
spoken for?* Everything on the first screen answers that. The most frequent action
is approving or vetoing one request, and it is one click from the first screen.

## The unit of authority: the agent

An **agent** is a named scope that one slash command opens. It owns a slice of one
charter, the subset of the seven rules it is allowed to apply, and the tags its
requests carry into analytics. Nothing is bought outside an agent, and an agent can
only do what its rule set allows: switch a rule off and it stops applying it from the
next `/veto` on.

- **Slack is the only way in.** There is no form in the console that creates a request.
  The command is `/veto …` in the agent's channel; the console exists for the human
  decision, the consent artefact, the catalogue check and the analytics.
- **The search is the rule set.** When an agent searches the catalogue, an item that
  breaks one of its rules is marked with the rule and the number, not hidden. That is
  the difference between "AI shopping" and an agent that can only buy inside policy.

## The objects

- **Agent**: name, scope, channel, owner, the charter slice it draws on, the rules it
  holds, the tags it writes.
- **Charter**: a budget line with a monthly cap, a per-order cap, a per-unit cap,
  an approval threshold, an approved-vendor list, an approver.
- **Request**: one agent proposal. It holds cart lines with quantities, unit prices
  and a vendor, is judged against one charter, and ends `auto`, `awaiting`,
  `vetoed` or `captured`.
- **Decision**: the rules that fired, with observed value and limit.
- **Consent**: a single-use, expiring authorisation bound to a hash of the exact
  cart. It is the product's trust artefact and the reason a request can be stopped
  without a human watching the payment rails.
- **Ledger entry**: each PayPal object, in order: order, authorisation, capture.
- **Tag**: one per request, written by the agent. The axis analytics groups by.

## What the product refuses to claim

- It is not a better checkout: PayPal checkout already asks a human. Veto exists
  for the case where no human is at the payment step, so consent has to be a
  separate, server-side object.
- A block is not "zero orders". It is **no capture without signed consent**, and
  every block leaves a receipt.
- Category rules are the weakest of the seven. Vendor, unit and threshold rules do
  the real work.

## Constraints

- One approver per charter; Slack is where the request starts, the web console is
  where money is signed for.
- The console must open offline as one file and screenshot at 1440x900.
- PayPal Orders `intent: AUTHORIZE`, then authorise, then capture.
- Nothing on screen is a placeholder: the numbers add up, or they do not ship.

## Non-goals for this build

No team management, no multi-currency, no accounting export beyond one CSV, no
Slack app distribution. The console renders a fixed October dataset; the harness at
`/demo` runs the same design on the live engine.
