# Veto

**Veto is a money-control layer for AI agents.** An agent asks to buy something in plain
words; Veto applies that agent's charter, asks a human only when the cart crosses a cap or a
threshold, and moves money through PayPal only against a signed, single-use consent. Every
step is a stored record — cart, reasons, consent, PayPal ids, trail.

Slack is one door, not the product. The product is the record: **Inbox → Record → Agents →
Analytics → Integrations**, all reading one SQLite store. Built sandbox-only for the PayPal
AI Hackathon (October 2026).

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

**Sign in:** the auth screen's *Open the demo workspace* button signs in as the approver
seeded in `Bracken & Field`. The admin account is `ops@brackenfield.demo` / `veto-demo-2026`.

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

## The six screens

| Screen | It owns |
|---|---|
| **Overview** (`/`) | needs-you count, committed-unsigned, settled this month, the agent roster, the oldest pending requests as links |
| **Inbox** (`/inbox`) | the only request list; status and agent filters live in the URL |
| **Record** (`/inbox/REQ-1043`) | the cart, the rule reasons, the consent hash, the PayPal ids, the trail and approve / veto / void |
| **Agents** (`/agents`) | one plate per agent; the agent page carries the real month track (spent / unsigned / left), a dry run that can be sent as a real request, and a scoped catalogue search |
| **Analytics** (`/analytics`) | captured money by day, agent, vendor, category and rule over one range, linking into the filtered Inbox |
| **Integrations** (`/integrations`) | Slack endpoints and model routing, the PayPal wallet, and agent API keys |

## Slack

`/veto <what to buy>` creates the request. The model reads the cart against every agent's
scope (categories, approved merchants, tag) and routes it to the agent whose charter should
judge it — the channel only records where it was typed. A model error or an unknown agent
falls back to the workspace default. The reply carries the decision and, when a signature is
needed, **Approve** and **Veto** buttons that write the same store. Requests are signed with `SLACK_SIGNING_SECRET`; configure the slash command and
interactivity URLs from *Integrations*.

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
                     requests.py (the board) · settlement.py (the only money mover)
web/src/             React app: screens/, components/, styles/tokens.css
app/                 the built web app (served at / and /app/)
tools/               check_m3.py (request board, ~60s) · check_m4.py (read models, ~1s)
veto/routing.py      model routing for requests that do not name an agent
veto/ratelimit.py    per-door minute limit; 429 + Retry-After
tests/               stdlib unittest suite
slack/               Slack app manifest + link.py for a one-click install link
shots/               screenshots used as delivery evidence
```

## Verify it

```bash
python3 -m unittest discover -s tests    # 206 backend tests
python3 tools/check_m3.py                # 19 checks: the request loop over HTTP (real pipeline)
python3 tools/check_m4.py                # 32 checks: read models, read-only, no money moves
cd web && npm run build && npx tsc --noEmit
```

`check_m3` runs the real pipeline and leaves genuine request history in the demo workspace —
Overview, Inbox and Analytics show real sandbox captures, not seeded fakes. `seed.py` writes
no requests.

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
distribution, multi-currency and accounting exports are future work. The free quick tunnel
changes host on restart, so the Slack URLs must be re-pasted until the hosted deploy lands.
