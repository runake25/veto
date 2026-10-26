# Veto

**Veto is a money-control layer for AI agents.** An agent asks to buy something in plain
words; Veto applies that agent's charter, asks a human only when the cart crosses a cap or a
threshold, and moves money through PayPal only against a signed, single-use consent. Every
step is a stored record — cart, reasons, consent, PayPal ids, trail.

Slack is one door, not the product. The product is the record: **Overview → Inbox → Record →
Agents → Analytics → Integrations**, plus Settings for the seat, all reading one SQLite
store. Built sandbox-only for the PayPal AI Hackathon (October 2026).

## Run it

Needs Python 3.12+ (the backend is stdlib only) and Node 22+ to build the web app.

```bash
cp .env.example .env            # PayPal sandbox, DeepSeek, Channel3, Slack (all optional)
cd web && npm install && npm run build && cd ..
python3 server.py               # http://127.0.0.1:8787/
```

`./start.sh` additionally opens a public quick tunnel and writes its URL into `.env` (Slack
callbacks need a public URL). The host changes on every start; paste the two URLs in
*Integrations* back into the Slack app after a restart.

**Sign in:** the auth screen's *Open the demo workspace* button signs in as **Caroline
Hayes**, the approver seeded in *Bracken & Field* — `caroline@brackenfield.demo` /
`veto-demo-2026`. The admin account is `ops@brackenfield.demo` / `veto-demo-2026`.

## The loop

`POST /api/v1/requests` is the one entry point. It writes the request, runs the pipeline
(`extract → source → choose → price → judge`) and then follows the decision:

```
auto    → consent → PayPal order → authorization → capture      status: captured
review  → consent (10 min, single use) → awaiting
             ├─ approve → capture                               status: captured
             ├─ veto                                            status: vetoed
             └─ void                                            status: voided
block   → (no consent, no PayPal object)                        status: blocked
```

- **Multi-item carts are resolved in parallel**: extract once, then every item is
  searched and chosen concurrently (`VETO_PARALLEL_ITEMS`, default 4), with the cart
  assembled in the order the request named the items.
- **The dry run is streamed**: the agent page reads `POST /api/v1/simulate/stream`, so
  each stage and each resolved item appears as it lands instead of behind a spinner.
- **A lapsed consent is still waiting on a person**: a request whose 10-minute consent
  expired stays in **Needs you**, marked `Consent expired`; the record locks approve and
  offers **Send it again** with a fresh window.
- **The Inbox finds things**: a real table with sortable column headers, search over title,
  text, agent, requester and merchant (URL-addressable), a day range in the viewer's own
  calendar, a filtered total, and a footer that adds the queue up.
- **The board is live**: open tabs hold one `GET /api/v1/events` stream; a write — a Slack
  approval, a routine run, a web action — publishes a change frame, so the Inbox and the
  record update without anyone pressing refresh. A 30-second poll and a refetch on tab focus
  are the fallbacks for a sleeping instance or a proxy that buffers the stream.
- **Two kinds of delete, one rule**: a standing job can be deleted with its run history (the
  requests it made stay in the record); an agent that has ever made a request cannot be
  deleted at all — the money trail has to stay readable, so Veto refuses and points at
  pause. Every delete is a two-step confirmation that names the consequence.
- **An agent has a brake**: pause and resume from its page; a paused agent refuses new requests.
- **A check before a bigger buy**: `/veto-check` shows the shortlist the agent's search
  actually returned inside its rules; choosing one pins that exact offer and re-enters the
  pipeline at PRICE, so the model never stands in for the person's choice.
- **Agents are standing jobs**: an agent can own one routine — a standing instruction, a
  cadence, a monthly envelope and a dedupe window. The runner's scheduler claims each due
  slot once, runs it through the same pipeline, records the run, and escalates a cart that
  crosses the envelope instead of capturing it. The agent page shows the job in full: next
  run, the month's envelope track, the recorded runs, and a **Run now** that streams the
  real pipeline. A pause on either the agent or the job stops spending at the money door.
- **Routing** is semantic: a request that does not name an agent is read by the model and
  matched to the agent whose categories and approved merchants fit it. The channel is only
  provenance; the routing decision is the first step of the record's trail.
- **Consent** is one sha256 of the cart, single use, ten minutes, and its nonce is the
  PayPal idempotency key — a retried approval replays the order instead of charging twice.
- **Captured** is the only status counted as spend. Block, veto and void create zero PayPal
  objects.
- **Rules** carry the agent's monthly cap, per-order cap, per-item cap, approval threshold,
  merchant allowlist and category allowlist, plus category exceptions. Every fired rule is
  stored with its observed value and limit, so a record can explain itself.

## Hosted demo

The public demo runs on Render's free plan:

**https://veto-bracken-field.onrender.com** — open it and use the demo button (the seeded
company *Bracken & Field* signs you in as Caroline Hayes, its approver).

- **Slack:** point every command's Request URL at
  `https://veto-bracken-field.onrender.com/api/slack/veto` and Interactivity at
  `https://veto-bracken-field.onrender.com/api/slack/interact`. One URL serves all six
  commands.
- **Sleep:** a free instance spins down after 15 minutes without traffic and loses its
  ephemeral filesystem, which would reset a judging session. Three independent warmers keep
  it awake, all free: the `*/5` schedule in `.github/workflows/keepalive.yml`; a second
  schedule in the tiny public `runake25/veto-wake` repository (so one slow cron registration
  cannot matter); and `keepalive-bridge.yml`, a self-renewing job that pings every four
  minutes for ~5.5 hours and dispatches its own successor before it exits. Public-repo
  Actions minutes are unlimited, so the total bill stays nothing.
- **Data:** the instance seeds the demo workspace on boot; the history there is real sandbox
  activity created through the product (captured carts, a blocked cart, a vetoed cart, real
  routine runs). `python3 tools/demo_history.py --base <url>` recreates that history through
  the public API — every request runs the live pipeline and captures through PayPal sandbox.
- **Live board:** approvals taken from Slack land on any open console tab through the change
  feed; the Inbox, the record and the agent pages re-read themselves, and the header says
  `Live` while the stream is connected.
- **Power:** PayPal sandbox only. `PAYPAL_VAULT_ID` re-attaches the already-approved sandbox
  wallet on boot, so a hosted request captures exactly like a local one.

Deploy or redeploy with the blueprint in `render.yaml`, or create the service with the Render
CLI and inject the same env vars (`render.yaml` lists them; secrets are never in the repo).
The image is built from `Dockerfile` (runtime files only: `server.py`, `veto/`, `spike/`,
`app/`), and serves the built app on `$PORT`.

## The seven screens

| Screen | It owns |
|---|---|
| **Overview** (`/`) | one action (Review N requests / Open inbox), the Needs-you panel with amount and age, one quiet This-month strip (held / settled / left), and compact agent rows |
| **Inbox** (`/inbox`) | the only request list: five groups in importance order (Needs you · All · Settled · Refused · Withdrawn, a lapsed consent marked inside Needs you), a real table with `aria-sort` headers, search/agent/day/order in the URL, a reconciled total, and a phone list of the same rows |
| **Record** (`/inbox/REQ-1043`) | the document: identity, verdict and reasons, cart, PayPal ids (each copyable), consent hash and window, trail, payment state in words, and approve / veto / void |
| **Agents** (`/agents`) | a fleet of instance plates (agent mark, serif name, state, serial, charter figures, standing job and scope); the agent page carries the charter, the **standing job** (schedule, envelope, runs, run now, pause, edit), a dry run that can be sent as a real request, and a scoped catalogue search. The rail lists the same instances collapsibly |
| **Analytics** (`/analytics`) | one lead settled figure over held/stopped/requests, captured money by day (with a daily table), agent, vendor, category and rule over one range, linking into the filtered Inbox |
| **Integrations** (`/integrations`) | two groups: **MONEY** (PayPal wallet) and **ACCESS** (agent API keys, then Slack commands, URLs and signature state) |
| **Settings** (`/settings`) | Account (name, email, role, sign out), Workspace (admin rename, id, currency), Security (password change), read-only Environment |

## Slack

One Request URL serves several commands; Slack sends the invoked `command` in the payload,
so the app never needs a second endpoint.

| Command | It does | Usage hint shown in Slack |
|---|---|---|
| `/veto <what to buy>` | creates the request and model-routes it to the agent whose charter fits | `what to buy — e.g. 2 packs of toilet paper` |
| `/veto-check <what>` | the top five offers the agent's search returned, each with a **Choose** button | `what to look for — e.g. paper towels` |
| `/veto-status [agent]` | what waits for a signature, with **Approve** / **Veto** buttons | `[agent] — leave empty for everything` |
| `/veto-budget [agent]` | this month's settled, waiting and left, per agent | `[agent] — leave empty for the whole workspace` |
| `/veto-routines [agent]` | the standing jobs, their next run and last result | `[agent] — leave empty for every routine` |
| `/veto-help` | the command list | — |

`/veto` is the buying door. The model reads the cart against every agent's scope
(categories, approved merchants, tag) and routes it to the agent whose charter should judge
it — the channel only records where it was typed. A model error or an unknown agent falls
back to the workspace default. The reply carries the decision and, when a signature is
needed, **Approve** and **Veto** buttons that write the same store. Requests are signed with
`SLACK_SIGNING_SECRET`; paste the Request URL from *Integrations* into every command row.

## Agent API keys

Mint a key on *Integrations* (shown once, stored hashed), then:

```bash
curl -X POST http://127.0.0.1:8787/api/v1/requests \
  -H "Authorization: Bearer vkey_…" -H "Content-Type: application/json" \
  -d '{"text":"2 packs of bamboo toilet paper"}'
```

The key scopes the request to its own agent's charter. Revoking it is immediate.

## Repo map

```
server.py            one stdlib HTTP runner: API, Slack, the built app, /connect
veto/                store.py · auth.py · seed.py · policy.py · pipeline.py
                     requests.py (the board) · routines.py (standing jobs)
                     events.py (the in-process change feed for the live board)
                     check.py (top-five shortlists) · slack_commands.py (the command surface)
                     settlement.py (the only money mover)
web/src/             React app: screens/ (incl. Settings.tsx) · components/ (incl.
                     AgentAvatar.tsx and state.tsx) · styles/ (tokens.css plus one
                     shell, inbox, agents, integrations and record stylesheet per screen)
app/                 the built web app (served at / and /app/)
tools/               check_m3.py (request board, ~60s) · check_m4.py (read models, ~1s)
                     check_polish.py (live feed, filters, delete guard, built controls, ~2s)
                     demo_history.py (fill a demo workspace with real sandbox history)
.github/workflows/   keepalive.yml (the */5 ping) · keepalive-bridge.yml (a self-renewing
                     warm window for when a cron registration is slow)
veto/routing.py      model routing for requests that do not name an agent
veto/ratelimit.py    per-door minute limit; 429 + Retry-After
tests/               stdlib unittest suite
slack/               Slack app manifest + link.py for a one-click install link
shots/               screenshots used as delivery evidence
```

## Verify it

```bash
python3 -m unittest discover -s tests    # 343 backend tests
python3 tools/check_m3.py                # 19 checks: the request loop over HTTP (real pipeline)
python3 tools/check_m4.py                # 42 checks: read models, read-only, no money moves
python3 tools/check_polish.py            # 20 checks: live feed, filters, delete guard, built controls
cd web && npm run build && npx tsc --noEmit
```

`check_m3` runs the real pipeline and leaves genuine request history in the demo workspace —
Overview, Inbox and Analytics show real sandbox captures, not seeded fakes. `seed.py` writes
no requests. `check_polish` is read-only too: it opens the change feed, compares a filtered
Inbox against its own rows, and calls the delete guards, which refuse without writing.

## Environment

All of `.env.example` is optional; without PayPal keys the runner uses an in-process PayPal
simulator, and without Slack secrets the slash command only works through curl. With
`PAYPAL_LIVE=1` and a vaulted wallet (`.state/vault.json`, or `PAYPAL_VAULT_ID`), captures are
real sandbox captures with real ids. `VETO_PUBLIC_URL` is the public URL written into Slack
buttons and integration headers.

## Limits

Sandbox money only. Every expensive door is rate limited (defaults: 30/min per agent key,
120/min per console session, 60/min per search, 20/min per Slack user; override with
`VETO_RATE_*` in `.env`). Refunds are out of scope (`void` withdraws an uncaptured request;
captured money cannot be reversed here). One workspace per runner; Slack OAuth
distribution, multi-currency and accounting exports are future work. The hosted demo's URLs
are stable; a self-hosted runner behind a quick tunnel still gets a new host on restart.
On Render's free plan a sleeping instance loses its ephemeral history — the keep-alive
workflow exists to prevent that, and the demo workspace reseeds itself on boot.
