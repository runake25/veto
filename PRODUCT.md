# Veto: product truth

Veto is a **consent layer for agent spending**. An AI agent can propose a purchase; money only
moves once that agent's charter passes it and, when the charter says so, a named human signs
for it. The signed consent is the product's object; PayPal is the rail.

## Who it is for

An ops or finance approver at a 20–200 person company that already lets agents buy things (a
Slack bot that orders supplies, a procurement agent, a research agent with a card). They are
not a developer and not a treasurer. They open Veto a few times a day, from a laptop or a
phone, usually because something needs them.

## The one job on the overview

*What needs my decision before money moves, and how much of this month is already spoken for?*
Every figure on the first screen answers that, and the most frequent action — signing or
refusing one request — is one click from it. The Inbox is the whole history and the Record is
where a decision is made with its reasons in view.

## The unit of authority: the agent

An **agent** is a named scope with its own charter. It owns a slice of the workspace budget,
the subset of rules it is allowed to apply, the merchants and categories it may use, and the
channel it answers on. Nothing is bought outside an agent, and an agent can only do what its
rule set allows.

- **Three doors in, one record out.** A request can arrive from Slack (`/veto …`), from an
  agent API key, or from the console's dry run. All three produce the same stored object.
- **The search is the rule set.** When an agent searches the catalogue, an item that breaks
  one of its rules is marked with the rule and the number, not hidden. That is the difference
  between "AI shopping" and an agent that can only buy inside policy.

## The objects

- **Agent**: name, tag, channel, scope, the charter slice it draws on, the rules it holds.
- **Charter**: monthly budget, per-order cap, per-item cap, approval threshold, approved
  merchants, allowed categories, category exceptions, one approver per workspace.
- **Request**: one agent proposal. It holds cart lines with quantities, unit prices and a
  vendor, is judged against the agent's charter, and ends `blocked`, `awaiting`, `captured`,
  `vetoed` or `voided`.
- **Decision**: the rules that fired, each with the observed value and the limit.
- **Consent**: a single-use, ten-minute authorisation bound to a hash of the exact cart. It is
  the trust artefact and the reason a request can be stopped without a human watching the
  payment rails. Its nonce is the PayPal idempotency key.
- **Payment**: the PayPal objects in order — order, authorization, capture — stored on the
  record. A capture is the only moment money moves.
- **Trail**: every step appended to the request, including who signed or refused it.

## What the product refuses to claim

- It is not a better checkout: PayPal checkout already asks a human. Veto exists for the case
  where no human is at the payment step, so consent has to be a separate, server-side object.
- A block is not "zero orders". It is **no capture without signed consent**, and every block
  leaves a receipt with its reasons.
- Category rules are the weakest signal. Merchant, unit, cap and threshold rules do the real
  work.
- The demo history is genuine: requests are written by the real pipeline and money moves in
  the PayPal sandbox. `seed.py` creates agents and rules, never fake requests.

## Constraints

- One workspace per runner; one approver (and admins) per workspace.
- PayPal Orders `intent: AUTHORIZE`, then authorize, then capture. Sandbox only in this build.
- Nothing on screen is a placeholder: every number reads the store, or it does not ship.
- The console is an app screen at 1440×900 first, usable at 390px; the sidebar becomes a top
  nav, rows fold, and nothing scrolls sideways.

## Non-goals for this build

No refunds beyond voiding an uncaptured request. No multi-currency, no accounting export, no
Slack app distribution/OAuth, no team management, no hosted multi-tenant deploy. Slack is one
integration, not the product.
