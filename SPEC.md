# Veto — product spec v2

> Agents spend. Humans govern. The dashboard proves it.

Target: PayPal AI Hackathon, closes **Nov 12 2026 12:00 PT = Nov 13 04:00 HKT**.

Veto is a **spending-policy control plane** for AI agents — not a Slack app. Any agent
asks it to buy something; the cart is priced from real products, judged by deterministic
rules, signed by a human when it breaks policy, and settled from a pre-vaulted PayPal
wallet. Slack, the web app and the REST API are three doors into the same engine.

Primary persona: the **ops / finance person** who has to answer "what did our agents
spend, on what, and why did nobody stop it".

Stack: Python 3 standard library server (`server.py`, `veto/`) + **React 19 / Vite /
TypeScript** app (`web/`, built into `app/` and served by the runner). Design direction
and tokens: `DESIGN.md` (framed sheet). Motion follows the oil-ui method: press feedback,
a moving selection indicator, first-enter stagger, shared-element transitions.

---

## 1. The one loop

```
an agent wants to buy something
   │  POST /api/v1/requests   (its own API key — the agent is a fact, not a claim)
   ▼
1. EXTRACT   text → items[{name, qty, query, product_type, brand}]
             (LLM; rewrites each item into a catalogue query and a bare product noun;
             never prices, vendors, payees)
2. SOURCE    per item, resolve the structured params against the catalogue and search
             inside the agent's merchants + per-order ceiling; category/brand filters
             first, then plain query variants; local catalogue when offline
3. CHOOSE    per item, pick the one offer that IS the requested product
             (LLM pick constrained to the sourced candidates; sees the catalogue's own
             category and never sees money)
4. PRICE     unit × qty for the chosen offers; the cart total is the sum of its lines
5. JUDGE     deterministic rules → auto | review | block, observed vs limit
6. CONSENT   sha256(cart) · single use · 10 minutes · named approver
7. SETTLE    PayPal vault → order → authorization → capture
             (block or veto = zero PayPal objects, ever)
8. RECORD    events, agent, tag, vendor → analytics
```

**EXTRACT now returns search params, not just a name.** Each item carries:

| Field | Purpose | Example |
|---|---|---|
| `name` | what the thread called it | "whiteboard markers" |
| `qty` | how many | 2 |
| `query` | the catalogue phrase, brand-free, with meaningful qualifiers | "dry erase markers" |
| `product_type` | the bare noun used for the category lookup | "markers" |
| `brand` | the brand when one is named | "Logitech" |

This is the bridge from EXTRACT to SOURCE: "whiteboard markers" never matches the
catalogue lexically, but the product type "markers" resolves to Channel3's `markers`
category, and the catalogue query "dry erase markers" returns real markers.

**SOURCE builds a structured search ladder** (`_search_attempts`):

1. `query` + resolved `category_ids` + resolved `brand_ids` (Channel3's own filters)
2. `query` + `category_ids`
3. `query` + `brand_ids`
4. `query` alone (spelling / plural relaxed, then the thread's own words)

Category ids come from `GET /v1/categories/search` and are accepted only when the
category's own words are a subset of the product type: "toilet paper" → `toilet-paper`,
"markers" → `markers`, but "coffee" → only coffee-makers/coffee-tables, so no category
filter is used and the chooser refuses the accessory. Brand ids come from
`GET /v1/brands/search` and match the brand name exactly (Channel3 keeps several ids per
name). Every search is still scoped to the agent's merchants and per-order ceiling. A
category-filtered attempt trusts the category; an unfiltered attempt keeps only products
that share a word with the query.

**Why CHOOSE is its own step.** A lexical catalogue hit is not a product decision:
"coffee" returns coffee pots, a "milk" query returns milk bottles, "chips" returns poker
chips and bird food. The LLM picks only from the offers SOURCE already scoped, and it can
refuse every offer; it sees titles, the charter category, the catalogue's own category and
vendors but never prices, caps or payees. If it is unavailable the deterministic fallback
picks the first offer inside the agent's categories, so the loop still runs offline.

**Failure taxonomy — what a refusal can mean (all are blocks, never silent):**

| Reason | Fired when | Reported as |
|---|---|---|
| `catalogue` | no offer is the requested product (nothing sourced, or every offer refused) | "no product for: X" + the closest offers it did find |
| `category_allowlist` | the chosen product's category is outside the agent's charter | e.g. "stationery is not an approved category" |
| `vendor_allowlist` | the offer's merchant is not on the agent's list | "X is not an approved merchant" |
| `monthly_cap` / `per_order_cap` | the cart would exceed a hard cap | observed vs limit + the unblock |
| `per_unit_cap` / `approval_threshold` | over a review rule | outcome `review`, a human can sign |

**Catalogue scope is a product truth, not mock data.** The catalogue is Channel3's
general-merchandise index: household consumables, office supplies, equipment, home goods.
It does not stock groceries, so a request for Pringles, Doritos or coffee resolves to a
catalogue refusal ("closest offers: a Pringles t-shirt …") rather than buying merchandise.
The demo's happy paths are toilet paper, paper towels, dish soap, sponges, trash bags,
keyboards, monitors, printer paper, pens, envelopes, markers and mats.

**Stability harness.** `tools/check_pipeline.py` runs the demo's edge cases against a
running runner and asserts each ends in the right class (purchasable / review / refused
with a named rule). It is the regression check for extractor, search and chooser changes:

    python3 tools/check_pipeline.py --base http://127.0.0.1:8787

Intake surfaces: REST API (agent key, primary) · Slack `/veto` + buttons (one workspace in
v1, callbacks already verified) · web app (dry-run and human-originated requests, marked
as such). MCP is a stretch goal, not committed.

## 2. Hierarchy and rule precedence

```
Workspace ── monthly budget, currency, wallet
├── Users        admin · approver · viewer
└── Agents       the "groups": scope + API key
    ├── Rules           agent defaults (caps, threshold, allowlists)
    ├── Category overrides   e.g. stationery: $50/line instead of $150
    └── Requests
        ├── Lines       sku · title · qty · unit · line · vendor · category · image · url
        ├── Reasons     rule · observed · limit · message · unblock hint
        ├── Consent     cart hash · nonce · expiry · approver
        ├── Payment     vault · order · authorization · capture
        └── Events      append-only audit trail
```

Evaluation order: a catalogue refusal short-circuits pricing → `category_allowlist` →
`vendor_allowlist` → `monthly_cap` (workspace + agent) → `per_order_cap` (cart and
category subtotal) → `per_unit_cap` (line, category override wins) → `approval_threshold`
(cart and category subtotal). Outcome = max severity; every reason stores cents and a
sentence. In the dry run the result also carries `near_misses` (the offers CHOOSE
refused), so a refusal can show what the catalogue had instead.

**Precedence:** category override → agent default → workspace default.

## 3. Data (SQLite, one file)

`workspaces`, `wallets`, `users`, `sessions`, `agents`, `agent_vendors`,
`agent_categories`, `agent_rules`, `rule_overrides`, `api_keys`, `requests`,
`request_lines`, `request_reasons`, `consents`, `payments`, `events`, `meta`.

Integer cents, ISO-8601 UTC strings, `workspace_id` on every business row. `.state/veto.db`
by default (`VETO_DB` overrides). Money is formatted only at the edge.

## 4. API

Auth: `—` public · `session` cookie · `agent` bearer key · `admin` role.
New endpoints answer `{"ok":true,"data":…}` or `{"ok":false,"error":{"code","message"}}`.
The expensive doors are rate limited in-process (requests, simulate, search, Slack) and answer
`429` + `Retry-After` when a key, session or Slack user goes over its minute; limits are
`VETO_RATE_KEY_PER_MIN` / `VETO_RATE_SESSION_PER_MIN` / `VETO_RATE_SEARCH_PER_MIN` /
`VETO_RATE_SLACK_PER_MIN`.

| Method + path | Auth | Status |
|---|---|---|
| `POST /api/v1/signup` `{workspace_name,name,email,password}` | — | **M1 done** |
| `POST /api/v1/login` `{email,password}` | — | **M1 done** |
| `POST /api/v1/logout` | session | **M1 done** |
| `POST /api/v1/demo-session` | — | **M1 done** |
| `GET /api/v1/me` | session | **M1 done** |
| `GET /api/v1/agents` | session | **M1 done** |
| `POST /api/v1/agents` · `PATCH /api/v1/agents/{id}` | admin/approver | **M2 done** |
| `GET /api/v1/agents/{id}/search?q=` | session | **M2 done** — scoped product search |
| `POST /api/v1/simulate` | session | **M2 done** — dry-run, persists nothing |
| `POST /api/v1/simulate/stream` | session | **M5 done** — the same dry run as SSE: `start`, one `event` per stage, one `item` per resolved cart line, `result` |
| `GET /api/v1/overview` | session | **M3 done** — needs you (signable only), expired consents, committed, settled this month, budget/remaining, agents |
| `GET /api/v1/requests` · `GET /api/v1/requests/{id}` | session | **M3 done, polished** — summaries and the full record; list takes `state=needs|settled|refused|withdrawn` (the five Inbox groups; `all` or empty means no group; `400 invalid_state` otherwise; composes with the legacy `status`/`consent` filters and `total`/`sum_cents` respect it; the group is echoed as `state`), `status` (incl. synthetic `expired`), `consent=actionable|expired`, `q` (title, text, agent, requester **and cart-line merchant**), whitelisted `sort`, an optional `from`/`to` day window with `tz` (viewer-local calendar, east-positive minutes), and returns `total` + `sum_cents` + the echoed window |
| `GET /api/v1/events` | session | **polish** — the live board: SSE `retry`, a `ready` frame, then `{"type":"change","scope":"requests|agents|integrations"}` per write, with a `: ping` heartbeat every 20s. Bounded per-tab queues; a slow reader drops frames, never blocks a write |
| `POST /api/v1/requests` | agent key / session | **M3 done** — persist, judge, consent, capture |
| `POST /api/v1/requests/{id}/decision` · `/void` | approver / admin | **M3 done** — approve captures, veto/void move no money |
| `GET/POST /api/v1/agents/{id}/keys` · `DELETE …/keys/{kid}` | admin | **M3 done** — `vkey_…`, shown once, stored hashed |
| `GET /api/v1/analytics?from&to=` | session | **M4 done** — daily series and agent/tag/vendor/category/rule aggregates |
| `GET /api/v1/integrations` | session | **M4 done** — Slack endpoints + routing, wallet, agent keys |
| `PATCH /api/v1/me` `{name}` | session | **round 4** — display name only; email and role stay read-only; `400 invalid_name` outside 1–80 characters |
| `PATCH /api/v1/workspace` `{name}` | admin | **round 4** — rename the workspace; `403 forbidden` for an approver or viewer, `400 invalid_name` outside 2–80 characters, `409 name_taken` |
| `POST /api/v1/me/password` `{current_password,new_password}` | session | **round 4** — `401 invalid_password` when the current password is wrong, `400 invalid_password_rule` below 10 characters; existing sessions stay valid |
| `GET /api/v1/routines` | session | **M6 done** — the standing jobs with their month and next run |
| `POST /api/v1/agents/{id}/routine` · `/pause` · `/resume` | admin/approver | **M6 done** — save the standing job, stop or restart it |
| `DELETE /api/v1/agents/{id}/routine` | admin/approver | **polish** — delete the job and its run history; the requests it made stay in the record |
| `DELETE /api/v1/agents/{id}` | admin/approver | **polish** — delete an agent that never made a request (`409 agent_has_history` with the count and "pause instead" when it did) |
| `POST /api/v1/agents/{id}/routine/run` · `/run/stream` | admin/approver | **M6 done** — run once; the streamed form emits the same frames as a dry run, then the run |
| `GET /` · `GET /app/*` | — | **M4 done** — the built app; both serve the same shell |
| `POST /api/slack/veto` | Slack signature | **M6** — one Request URL for every Slack command: `/veto`, `/veto-status`, `/veto-budget`, `/veto-routines`, `/veto-help` |
| `POST /api/slack/interact` | Slack signature | **M3 done** — Approve/Veto buttons, challenge probe |
| `GET /api/health` | — | extended (db, seeded, counts) |

The request board is now the store, not the old in-memory engine:

```
POST /requests ─→ pipeline (extract → source → choose → price → judge)
                    ├─ auto   → consent → PayPal order → authorization → capture → captured
                    ├─ review → consent (10 min, single use) → awaiting ──┬─ approve → captured
                    └─ block  → blocked                                  └─ veto    → vetoed
                                                                        └─ void    → voided
```

Requests that do not name an agent (Slack `/veto`, a console session without `agent_id`) are
routed by the model: it reads the cart against each agent's categories, approved merchants
and tag, and returns one id from the workspace list; the channel is provenance only. An
invalid answer or model error falls back to the workspace default agent, and the routing
decision is the first entry in the request's trail.

Statuses: `blocked` · `awaiting` · `approved` (green-lit, capture owed) · `captured` (the
only state counted as spend) · `vetoed` · `voided`. A consent is one sha256 of the cart,
single use, ten minutes; approve after it expires answers `409 consent_expired`. Block and
veto write **zero PayPal objects**; void only applies before capture. The consent nonce is
the PayPal idempotency key, so a retried approval replays the same order instead of charging
twice. Every step is appended to `events`; `GET /requests/{id}` returns lines, reasons,
consent, payment ids, events and the agent.

Slack `/veto` and its buttons now write the same store. **Routing is semantic, not by
channel**: the model reads the request against every agent's scope (categories, approved
merchants, tag) and returns one agent id from that workspace's list; an unknown id, a model
error or no answer falls back to the workspace default agent. The channel is recorded as
provenance only. The routing decision is the first entry in the request's trail
(`Routed to …`, with the model's reason), the Slack message is built from the stored record,
and the audit event names the Slack user who pressed the button.
The old static console (`index.html`, `ui/`) is deleted: `/` and `/app/` serve the built
React app, and `/demo` no longer exists. `/connect` remains as the one-page wallet setup
utility (it drives the legacy engine's PayPal vault flow); everything the product does now
lives behind `/api/v1/*`.

### Settings and identity

Round 4 makes the seat editable and renames the demo approver.

- **Demo identity.** The seeded approver is **Caroline Hayes** `caroline@brackenfield.demo`
  (password `veto-demo-2026`); the admin stays `ops@brackenfield.demo`. On boot the runner calls
  `veto.seed.migrate_demo_identity(STORE)`, which renames a legacy seeded row idempotently by
  matching the old email or display name. User ids never change, so existing sessions and every
  request that points at the approver stay valid.
- `PATCH /api/v1/me {name}` writes the display name only (`400 invalid_name` outside 1–80
  characters); email and role are read-only.
- `PATCH /api/v1/workspace {name}` is admin-only (`403 forbidden` otherwise), uses the same
  2–80 character window (`400 invalid_name`), and answers `409 name_taken` when another
  workspace holds the name.
- `POST /api/v1/me/password {current_password,new_password}` verifies the current password
  (`401 invalid_password`) and requires at least 10 characters for the new one
  (`400 invalid_password_rule`). A password change keeps the current sessions valid in this
  build; there is no global revoke.
- **Settings** (`/settings`) is the only surface for these writes: Account, Workspace, Security
  and a read-only Environment card. Each card saves itself; there is no save-all button.

### Slack command surface

`POST /api/slack/veto` serves five commands from one Request URL; the signed payload's
`command` field selects the handler. All answers are ephemeral, and `/veto-status` carries
the same Approve/Veto buttons as a request message.

| Command | Behavior | Usage hint shown in Slack |
|---|---|---|
| `/veto <text>` | create a request through routing + pipeline | `what to buy — e.g. 2 packs of toilet paper` |
| `/veto-check <text>` | the top five offers the scoped search returned, each with Choose; a pick re-enters the pipeline at PRICE | `what to look for — e.g. paper towels` |
| `/veto-status [agent]` | up to five awaiting requests, oldest first, each with Approve/Veto | `[agent] — leave empty for everything` |
| `/veto-budget [agent]` | workspace settled/committed/left, then per-agent caps | `[agent] — leave empty for the whole workspace` |
| `/veto-routines [agent]` | cadence, envelope, next run and last outcome per routine | `[agent] — leave empty for every routine` |
| `/veto-help` (`/veto help`) | the command list | — |

The command metadata lives in `veto/slack_commands.py`; `slack/link.py`,
`slack/manifest.yml` and the Integrations screen read the same list, so the manifest and the
UI cannot drift. Every command shares `SLACK_SIGNING_SECRET` verification and the per-user
Slack rate limit.

### Hosting

The public demo is a Docker web service on Render's free plan:
`https://veto-bracken-field.onrender.com`. The image is built from the repository
`Dockerfile` and contains no secrets or state; the platform injects the env vars in
`render.yaml`, and `PAYPAL_VAULT_ID` hands the already-approved sandbox wallet in so a
hosted process captures without a browser. A fresh instance seeds the demo workspace on
boot, so the service is self-starting. A free instance sleeps after 15 idle minutes and
resets its ephemeral disk, so three independent warmers ping `/api/health`, all at $0:
`keepalive.yml` on a `*/5` schedule; a second schedule in the small public
`runake25/veto-wake` repository; and `keepalive-bridge.yml`, a self-renewing job that pings
every four minutes for ~5.5 hours and dispatches its own successor before it exits (public
repository Actions minutes are unlimited, and the job is under GitHub's six-hour limit).
The bridge makes "never sleeps" independent of how slowly GitHub registers a new cron. The
public URL is permanent, so the Slack app is pointed at it once.

### Routines

An agent owns at most one routine: a standing instruction, a cadence (`daily`, `weekly`,
`monthly`), a monthly envelope and a dedupe window. `veto/routines.py` runs a due routine
through the same `requests.create` path as any other request:

- the scheduler claims a due slot with one conditional `UPDATE`, so a restart or two ticks
  cannot order twice; a missed slot is caught up once and the cadence resumes from now;
- a run inside the dedupe window is recorded as `skipped`, not reordered;
- a cart that would cross the routine's remaining envelope is escalated (reason
  `routine_envelope`) instead of captured; an exhausted envelope skips the run;
- each run is a `routine_runs` row (`captured`, `awaiting`, `approved`, `blocked`,
  `skipped`, `failed`) linked to its request; `public_request` carries the routine name;
- pausing the agent outranks the schedule: a due run records a `skipped` row instead of
  ordering, and `requests.create` refuses a paused agent from every door (web, Slack, agent
  key, job), so the brake is a money rule, not a badge.

The agent page owns the UI: the standing job card shows next run, cadence, envelope track
(spent / unsigned / left), the recorded runs with their outcome and request link, a
**Run now** that streams the same pipeline (real request, confirm first), pause/resume and
an edit form for instruction, cadence, envelope and dedupe window. A routine's request is
labelled **Routine** in the Inbox and on its record.

Tables: `routines` (one per agent, `next_run_at`, `last_run_at`, envelope, dedupe) and
`routine_runs` (due/start/finish, outcome, request, amount, note).

## 5. Screens and data ownership

Seven screens, all live from the same store. Each fact has exactly one owner; other surfaces
may show a count or a link. `/` opens the app; a hash router owns the rest.

| Screen | Owns |
|---|---|
| **Overview** | one primary action (Review N requests / Open inbox) · the Needs-you lead panel (rows with agent mark, amount, age, Open; a quiet lapsed-consent line) · one quiet This-month strip (held for signature, settled, left, thin budget meter) · compact agent rows |
| **Inbox** | the only request list; five state groups in importance order — Needs you (awaiting + approved, including a lapsed consent marked `Consent expired`), All, Settled, Refused (blocked + vetoed), Withdrawn — with a real table, `aria-sort` headers, phone list, and search/agent/day/order filters below the tabs in the URL |
| **Record** | document order: identity, verdict band with reasons and actions, cart, PayPal objects, consent seal, trail; approve/veto/void by state, expired-consent lock and "send it again" |
| **Agents** | one instance plate per agent (44px mark, serif name, state, `agt_… · #channel · tag`, charter figures, JOB + SCOPE); the agent page carries the charter band, the standing job (schedule, envelope, runs, run now, pause, edit), the dry run and the scoped catalogue search |
| **Analytics** | one lead settled figure over a held/stopped/requests row; captured money by day (with a daily data table), by agent, by vendor, by category and by rule over one range; links into the filtered Inbox |
| **Integrations** | two groups: MONEY (one PayPal wallet card) and ACCESS (agent API keys, then Slack endpoints and signature state; the default-agent routing fact is a Slack footnote) |
| **Settings** | Account (display name, email, role, sign out), Workspace (admin rename, id, currency), Security (password change), read-only Environment (wallet/rails, agents, runner URL) |

Guided rule setup (option A): one form with template defaults and an optional category
override. The agent screen renders the charter back as a guardrail band — monthly figure
and track, per-order / per-item / signature figures, then categories, merchants and
exceptions — not as a table of rule names. Not a raw editor.

## 6. Auth and tenancy

Email + password (scrypt), opaque sessions (sha256 stored, HttpOnly cookie, SameSite=Lax,
7-day expiry, `Secure` only on https requests). Roles: admin, approver, viewer. Sign-up
creates a workspace and its first admin. The seeded demo workspace is reachable in one
click so a judge can interact without sign-up; its approver is **Caroline Hayes**
`caroline@brackenfield.demo` and the runner renames a legacy seeded row to that identity on
boot (idempotent; user ids never change, so sessions and record references keep working).
Agent API keys are `vkey_…`, shown once, stored hashed; minting and revoking are admin-only
by design, so the demo approver reads the key card but cannot write it.

## 7. Motion and design rules

The framed sheet, in a ledger tone: a cool graphite desk, one dark navy rail, the work on a
white sheet inset 8px, 1px edge, 12px corners, no shadow. Three type voices: IBM Plex Serif
for page, document, plate and Settings titles; IBM Plex Sans for the interface, rows and
money (tabular); IBM Plex Mono for identifiers, hashes and timestamps. One navy accent
carries links, focus and the one series; green is only money captured, red only a refusal,
amber only money held for a signature. Tight corners (4/6/12px), tabular figures,
identifiers in mono. Ten screens' worth of decoration would be a duplicate; every screen has
one job. Motion budget: (1) press feedback on every control, (2) one first-enter stagger of
the screen's blocks, (3) state changes render in place and keep provenance (Inbox row →
Record; approve → captured, in the same band), (4) hover is a tone step, never a lift.
Loading, empty, expired-consent and refused states are authored, not blank. Below 900px the
sidebar becomes a top nav with an account menu; below 720px rows fold to two lines and
nothing scrolls sideways. `prefers-reduced-motion` collapses everything to the end state.

## 8. Milestones

| # | Ship | Status |
|---|---|---|
| M0 | this spec, branch `v2` | **done** |
| M1 | SQLite store, auth, seeded demo, session API, React shell + auth screen | **done (2026-10-22)** |
| M2 | policy engine, guided agent rules, scoped search, dry-run | **done (2026-10-22)** |
| M3 | requests/consent/payments/void/events, Overview live, Slack rewired, API keys | **done (2026-10-23)** |
| M4 | Inbox, Record, Analytics, Integrations; Overview rewritten; palette retuned to the ledger tone; old console deleted; `/` flips to the app; model routing for Slack | **done (2026-10-24)** |
| M5 | parallel search, streamed pipeline, rate limits, hosting, UI smoke test | **done (2026-10-26)** — rate limits, real agent budget track, send-from-dry-run, parallel item search, the streamed dry run, the hosted demo and the HTTP smoke harnesses are live |
| M6 | routines (standing jobs) + Slack command surface + veto-check, hosting, UI polish | **done (2026-10-26)** — routine schema, scheduler, envelope, run history, veto-check, the six Slack commands, the routine UI and the hosted demo are live; the deferred UI-audit findings are closed in the polish round (see §9a); video and Devpost remain submission work |

## 9. Running it

```bash
python3 server.py            # http://127.0.0.1:8787/
cd web && npm install && npm run build   # rebuild app/ after frontend changes
cd web && npm run dev                    # dev server on :5173, proxies /api to :8787
python3 -m unittest discover -s tests    # backend tests (344)
python3 tools/check_m3.py                # 19 checks: the request board over HTTP (~60s)
python3 tools/check_m4.py                # 42 checks: screens' data, read-only (~1s)
python3 tools/check_polish.py            # 21 checks: live feed, filters, delete guard, built controls
```

M2 adds the parts you can click: `POST /api/v1/simulate` runs the real
extraction → catalogue → rules path and stops before consent; `GET
/api/v1/agents/{id}/search` returns products annotated with `fits` against that
agent's caps and verdicts. Channel3 is queried with the agent's merchants and a
price ceiling, so the agent can only find what it may buy.

M3 makes the requests real. `POST /api/v1/requests` (session or agent key) writes
the record, issues the consent and captures through PayPal when the rules allow;
`/decision` and `/void` move it from there. The M3 regression harness exercises
the whole board over HTTP:

    python3 tools/check_m3.py                          # against :8787
    python3 tools/check_m3.py --base http://127.0.0.1:8788

It takes ~40s because it runs the real pipeline; a run leaves genuine request
history in the demo workspace, which is what Overview and Inbox read.

M4 adds the read models the screens consume and a read-only harness:

    python3 tools/check_m4.py                          # 42 checks, no money moved

It proves Analytics reconciles with Overview (series sum = captured total = by-agent
sum = by-vendor sum), Integrations reports Slack routing/wallet/keys, a record carries
its cart, consent, PayPal capture id and ordered trail, a refused record carries no
wallet object, and `/` serves the app while the old console routes are gone.

A polish round (2026-10-26) closed the deferred findings from the two independent UI audits:

- **One status language.** `web/src/components/state.tsx` owns a single `State` component —
  a dot and a word, four tones (positive, hold, refusal, quiet) — and the vocabulary for
  agent state, job state, run outcome, payment state and payment mode. The old pill, raw
  `agent-state` and text-only run outcome are gone, so a colour cannot mean two things.
  "Can't buy" is computed by the same helper on the roster and the agent page.
- **A live board.** `GET /api/v1/events` carries change frames from `veto/events.py`;
  `FreshnessProvider` in the shell re-reads the screens on a change, on tab focus and on a
  30-second fallback poll. Slack approvals now land on an open Inbox with no manual refresh.
- **A real Inbox table.** The list is a `<table>` with sticky `aria-sort` column headers, a
  phone list of the same rows, a `when` day range (viewer-local, offset sent to the server),
  search over cart-line merchants, and a reconciliation footer.
- **Honest deletes.** A standing job deletes with its run history; an agent with ledger
  history is refused and told to pause. Both are two-step confirmations.
- **Field-level validation.** The agent form validates name, lengths, money, merchant
  domains, category slugs and the category exception at the field, mirrors the API's own
  rules, and places a server message beside the field it names.
- **No dead ends.** A failed session check opens its own retry state (with the free-instance
  wake note) instead of the sign-in screen; screen errors carry Retry; skeletons mirror the
  block geometry of the Inbox, record, agent page, analytics and integrations.
- **Ledger affordances.** PayPal order/authorization/capture ids and the cart hash copy with
  one control; `COMPLETED` reads "Completed"; mono `sandbox` tags became words; no meta text
  below 11px.
- **Run-now shows the money.** `Settlement.capture(on_step=…)` and `requests.create` stream
  record → consent → order → authorization → capture into the same live panel as the dry run.
- **Integrations layout** is Slack beside a wallet + routing column with the keys card full
  width, and its skeleton matches.

The regression harness for the round is read-only:

    python3 tools/check_polish.py                      # 21 checks, no money moved

It proves the feed opens (and needs a session), the day window only returns its own day and
reconciles its sum, a merchant is searchable through a cart line, an agent with history is
refused a delete with the pause remedy, and the built bundle carries the new controls.

M5 keeps the run honest while it works. `POST /api/v1/simulate/stream` returns the
same dry run as server-sent events: a `start` frame immediately, one `event` frame per
trail stage as it lands, one `item` frame per cart line as it resolves, then `result`.
The console shows the stages and the items filling in instead of a spinner; the run is
still read-only. A multi-item cart searches and chooses items concurrently, and the
expensive doors are rate limited.

M6 gives the agent a standing job. A routine is a standing instruction, a cadence, a
monthly envelope and a dedupe window; the runner's scheduler claims each due slot once
and runs it through the same pipeline, so a routine run is judged, consented and captured
exactly like a hand-typed request. Over-envelope runs escalate instead of capturing, and
every run is recorded on the agent. The same surface is reachable from Slack: `/veto-check`, `/veto-status`,
`/veto-budget`, `/veto-routines` and `/veto-help` share one Request URL with `/veto`, and
`/veto-status` returns the Approve/Veto buttons. `/veto-check` returns the shortlist the
scoped search actually produced; a Choose button pins that exact offer (`pinned_lines`),
skips only EXTRACT/SOURCE/CHOOSE, and re-enters at PRICE → JUDGE → CONSENT → SETTLE. The command list is data in
`veto/slack_commands.py`, consumed by the server, the manifest and the Integrations screen.

`PAYPAL_LIVE=0` runs the whole loop against the in-process PayPal simulator with no keys.
Hosted deploys pass the approved `PAYPAL_VAULT_ID` through the environment.

A round-4 pass (2026-10-26) rebuilt hierarchy and instances, and added Settings:

- **A document voice.** IBM Plex Serif 400/500/600 is bundled for page titles (h1 32/600),
  the record title, the agent-plate name and Settings section titles; the interface stays
  IBM Plex Sans and identifiers stay IBM Plex Mono. The shipped scale is page subtitle 13.5,
  card title 15, primary row 14.5, secondary 12.5–13, labels 11 tracked capitals, money
  sans tabular. `DESIGN.md` carries the full scale. The four-card Overview stat row is gone.
- **Instances, not folders.** The rail gains a collapsible **Instances** list under Agents
  (24px mark + name, count, auto-open inside `/agents*`, remembered per browser), and every
  agent is drawn by `components/AgentAvatar.tsx` — a dark mono-monogram tile with a serial-
  hashed dot grid and a four-tone state ring (24/44/56px). The Agents screen is a fleet of
  instance plates; the agent page leads with the identity header.
- **Five inbox groups, in importance order.** `state=needs|settled|refused|withdrawn` maps
  to `awaiting+approved`, `captured`, `blocked+vetoed`, `voided`; the plain `/inbox` is All.
  A lapsed consent is no longer its own tab — it stays in **Needs you**, marked
  `Consent expired`, and the record offers "send it again". This supersedes the polish
  round's separate Expired tab; the old `status=` parameter still resolves for old links.
- **Overview decision-first.** Head with one primary button and freshness → Needs-you lead
  panel (agent mark, title, amount, age, Open, one quiet lapsed line) → one quiet This-month
  strip (held / settled / left, thin meter) → compact agent rows. No four identical stat
  cards.
- **Integrations in two groups.** **MONEY** is the single PayPal wallet card; **ACCESS** is
  agent API keys then Slack; the model-routing card is deleted and its one default-agent
  fact is a Slack footnote. This supersedes the polish round's Slack-beside-wallet layout.
- **Settings.** The new `/settings` screen writes the account name, the workspace name
  (admin only) and the password through the three endpoints above, and shows the read-only
  environment beside them. Each card saves itself.
- **Identity and copy.** The demo approver is Caroline Hayes `caroline@brackenfield.demo`,
  with an idempotent boot rename for legacy rows. Page subtitles are one clause of ≤ 12
  words; card ledes were cut where they only explained the product.

## 10. Non-goals (v1)

Multi-workspace Slack OAuth · email intake · MCP server · Postgres · SSO · multi-currency ·
refunds beyond voiding an uncaptured authorization · a framework migration of the runner.
