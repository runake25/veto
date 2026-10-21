# Veto

A consent layer for agent spending. An AI agent can propose a purchase; money only
moves after a charter's rules pass and a named human consents.

Two things live here:

| | What | Where |
|---|---|---|
| **Runner** | the working pipeline: message → items → cart → rules → consent → PayPal | `server.py` |
| **Console** | the operator UI, a single offline file, demo data | `index.html` |

---

## Run it

```bash
./start.sh          # tunnel + runner, one command
python3 server.py   # runner only, local
```

```
console   http://127.0.0.1:8787/
harness   http://127.0.0.1:8787/demo
wallet    http://127.0.0.1:8787/connect
paypal    sandbox   llm  deepseek   catalog channel3
```

Python 3.10+, no dependencies, nothing to install. Keys are read from `.env`.

`start.sh` brings up a Cloudflare quick tunnel first (so Slack can reach the machine),
writes the new host into `.env`, and then starts the runner. Quick-tunnel hosts change on
every start; the script says so and tells you to re-run `slack/link.py` when they do.

**The harness (`/demo`) is the demo.** Type a message or hit a preset, and the
whole pipeline runs for real: DeepSeek extracts the items, the catalogue prices
them, the charter rules decide, and Approve moves money.

### The model: agents hold the rules

An **agent** is the unit of authority. It owns a slice of one charter, the rules it is
allowed to apply, and the tags its requests carry. A `/veto` in its channel is the only
way a request exists; the console is where a human signs for one.

- **Rules are per agent.** Seven exist (monthly cap, per order, per unit, approval
  threshold, vendor allowlist, category allowlist, catalogue match). An agent holds some
  of them; switching one on or off changes what the agent can find and what it must ask
  about, from the next slash command on.
- **The search is the rule set.** `Find a product` searches the catalogue *as* a chosen
  agent. An item that breaks one of that agent's rules is marked and explained, never
  hidden, and the count at the top-tight says how many items fit right now.
- **Tags go to analytics.** Every request carries the tag its agent wrote, so committed
  and refused money can be grouped by tag and by agent.

### The console: six views, all reachable

| Route | Screen |
|---|---|
| `/` | the ledger: what needs a decision, how much of the month is spoken for, every request |
| `#/agents` | the five agents: scope, charter, rules held, tags, requests, money |
| `#/agents/desk` | one agent: its seven rules as switches, its tags, its requests, and what the rule set lets it find |
| `#/catalogue` | the catalogue searched as a chosen agent, every item marked fits / needs consent / refused |
| `#/analytics` | committed money by tag, by agent, the decision mix, and which rule stopped what |
| `#/requests` | every request with filters that recompute the counts in scope |
| `#/charters` | the four budget lines, their caps, their approvers |
| `#/approvals` | the ledger filtered to what is waiting on you |
| `#req-1042` | one request: the cart, the agent that raised it, the rule that fired, the consent seal, the rail from slash command to capture |
| `#palette` or `⌘K` | the command palette; typing filters, arrows move, Enter runs, Escape closes |

### Rebuild the console

```bash
python3 ui/build.py     # ui/src/{index.html,app.css,app.js} -> index.html, one offline file
node ui/shoot.mjs       # drive a real Chrome, run 25 interaction checks, write shots/*.png
node ui/shoot.mjs --check   # checks only, no screenshots
```

`index.html` is one file with the fonts, styles, script and data inlined: it opens
from the filesystem with no server and no network.

### The catalogue is Channel3

The agent does not invent products. It sends the extracted item text to Channel3 and
prices the cart from what comes back: a real product, a real merchant domain and an
affiliate buy link, via `veto/channel3.py` (stdlib only, key in `CHANNEL3_API_KEY`).

The rule set is applied **as search constraints**, not just as a later check:

| Rule the agent holds | How it reaches the search |
|---|---|
| vendor allowlist | `filters.website_ids` — only approved merchants are searched |
| per-order / monthly cap | `filters.price.max_price` — the hard ceiling bounds what can come back |
| per-unit cap, approval threshold | stay decision rules: they fire on the cart and send it to a human |
| category allowlist | the charter's categories filter what maps into the cart |

Because the agent only holds *some* of the seven rules, two agents on the same charter
treat the same cart differently: Desk kit holds no per-unit cap, so an $89.99 keyboard
is only over its $50 threshold; Pantry runner does hold it, so a $75 pack is flagged per
unit as well. That is the per-agent rule model doing real work rather than being drawn.

If no approved merchant carries the item, or nothing maps to an allowed category, the
request is **blocked with that reason** instead of quietly proposing something outside
policy. Meals stay on the local list: food is a service, not a catalogue product.

### PayPal: two modes, one line apart

| `.env` | What the runner does | Needs a login? |
|---|---|---|
| `PAYPAL_LIVE=0` | built-in simulator: order → authorise → capture, same shapes, fake ids | no |
| `PAYPAL_LIVE=1` | the real PayPal **sandbox**: real ids, real approval | one sandbox login |

The code path is identical; only the base URL changes. The runner ships on `0` so
everything works out of the box. Flip it to `1` after the step below and captures become
real, with no other change.

**The sandbox is a separate fake world — your normal PayPal login does not work there.**
To get a sandbox login:

1. Open **developer.paypal.com/dashboard** → **Testing Tools** → **Sandbox Accounts**.
2. If no *Personal* (buyer) account is listed, create one — Type **Personal**, any country.
3. Click **Log in** on that account. It opens `sandbox.paypal.com` already signed in.
4. In that **same browser window**, open **http://127.0.0.1:8787/connect** and press
   *Connect PayPal*, then Approve.
5. Set `PAYPAL_LIVE=1` in `.env` and restart the runner.

Nothing else changes: same consent, same rules, same rail — the capture just moves from
the simulator to PayPal.

**A vaulted wallet changes the shape of the calls.** Once the payer approves once, the
payment is merchant-initiated (`stored_credential`, `usage: SUBSEQUENT`), and PayPal fills
the order while creating it: the order comes back `COMPLETED` with its authorization
already attached. Calling `/authorize` on that order answers `422
ORDER_ALREADY_AUTHORIZED`. So `_pay` reads what PayPal built and only falls back to an
explicit authorize-then-capture when the order arrives empty. Verified both ways against
the sandbox: auto-approval captured `7EK989323J293960J`, a human-approved cart captured
`54A73878WD450251A`.

### Connecting the wallet (one click, one login)

```bash
python3 server.py
open http://127.0.0.1:8787/connect
```

One button. PayPal asks you to log in as the **sandbox buyer** and approve; the page
notices by itself and turns all three steps green. Until this is done, approved carts
stop at "capture waits for the wallet" instead of moving money.

The approval survives things that used to break it: the setup token is written to
`.state/vault.json`, so reloading the page, clicking *Connect* twice, or restarting the
runner picks the same pending approval back up instead of minting a new link and
orphaning the one open in your browser. While the payer has not pressed Approve yet the
API answers `425` ("waiting"), not `500`.

### Putting `/veto` in a real Slack channel (one link, two secrets)

The endpoints already speak Slack — slash command, block-kit reply, interactivity
buttons, `ssl_check`, HMAC-SHA256 signature checks. What is missing is the app
registration. Point a tunnel at the runner, then let the script fill in the manifest:

```bash
cloudflared tunnel --url http://127.0.0.1:8787     # prints https://…trycloudflare.com
echo 'VETO_PUBLIC_URL=https://…trycloudflare.com' >> .env
python3 slack/link.py                              # prints a one-click install link
```

1. Open the printed link, pick your workspace, press **Create**.
2. In the new app: **Install to Workspace**.
3. Copy two values into `.env` and restart the runner:

```
SLACK_SIGNING_SECRET=…   # Basic Information → App Credentials
SLACK_BOT_TOKEN=xoxb-…   # OAuth & Permissions
```

Then `/veto 2 packs of bamboo toilet paper` in any channel. The reply names the agent,
its tag, the rules that fired and the consent hash, and carries Approve / Veto buttons.

Two things that matter in a real workspace:

- **Slack gives a slash command three seconds.** Pricing a cart takes one to four —
  DeepSeek, then the live catalogue. So a real Slack call is acknowledged in
  milliseconds (*"Checking the charter…"*) and the decision arrives through
  `response_url` from a worker thread. `curl` gets the answer inline as before.
- **Approving needs a connected wallet.** With no vault, the ephemeral reply carries a
  *Connect PayPal* button that opens `/connect` on the tunnel instead of failing.

`python3 slack/verify.py` checks the whole binding in one command: secrets present, bot token
accepted by `auth.test` (and which workspace it is), the public URL reachable, `ssl_check`, and a
signed `/veto` call. `GET /api/health` reports both bindings at a glance:

```json
{"paypal":"sandbox","vault":false,"vault_pending":true,
 "slack":{"signing_secret":false,"bot_token":false,"app_ready":false}}
```

### The slash command

`POST /api/slack/veto` is the only door a request can come through. It reads the same
form Slack posts for a slash command and answers in Slack's block-kit shape, so the URL
works as a Slack app endpoint and from curl:

```bash
curl -s -X POST localhost:8787/api/slack/veto \
  -d 'command=/veto&text=2 boxes of vertuo capsules and 1 pack of bamboo toilet paper from kettle supply&user_name=dan&channel_name=office-ops'
```

The reply names the agent that took it, the tag it will carry into analytics, the rules
that fired, the consent hash and two buttons. It is `in_channel`, so the decision is on
the record where the request started.

### Prove it from the terminal

```bash
curl -s -X POST localhost:8787/api/decide -H 'Content-Type: application/json' \
  -d '{"id":"REQ-1046","action":"approve"}'
```

That second call returns `status: captured` with an order id, an authorization id
and a capture id — the three PayPal objects, in order.

---

## What is real, and what is not

Honest inventory. Do not claim more than this in the pitch.

| Piece | Today | To make it real |
|---|---|---|
| Conversation → items | **Live DeepSeek** (`deepseek-flash`) | already |
| Pricing | **Live Channel3** — real products, real merchants, real buy links; local 8-line list only as fallback | already |
| Charter rules | Real, deterministic, 7 rules | already |
| Consent | Real sha256 cart hash, single-use, 10-min expiry | already |
| PayPal | **Live sandbox, end to end** — vaulted wallet, real order → authorization → capture, real ids. Both paths run: rules-pass captures with no human, a Slack approval captures after the click | already |
| Slack | **Live in a real workspace** — `/veto` slash command, block-kit reply, Approve/Veto buttons with HMAC-verified interactivity, `ssl_check`. Bot `veto` installed in `vetollc.slack.com`; answer acknowledged in milliseconds and delivered through `response_url` | already |
| Console | Static October dataset so the screenshots are reproducible | feed `ui/src/app.js` its `DATA` from `/api/state` |

The mock reproduces PayPal's real shape — status lifecycle, 4xx envelopes, and
duplicate-`PayPal-Request-Id` replay — but **a passing mock is not evidence that
the sandbox works.** Only the live gate proves that.

---

## Going live on PayPal sandbox

1. **Get a working key pair.** In the developer dashboard → Apps & Credentials →
   Sandbox → your app, copy the *Client ID* **and** the *Secret* (Secret is a
   different 80-char string, revealed with *Show*). The client ID and secret are
   never the same value. Enable **Accept payments** and **Vault** under App
   Feature Options.
2. **Prove the capability before building on it:**
   ```bash
   PAYPAL_CLIENT_ID=... PAYPAL_CLIENT_SECRET=... python3 spike/gate.py --live
   ```
   You want `PATH A — VIABLE`. If it prints an approval URL, open it as the
   sandbox **personal** buyer, approve, then run the same command again.
3. **Run the app live:**
   ```bash
   PAYPAL_LIVE=1 python3 server.py
   curl -X POST localhost:8787/api/connect
   ```
   `connect` returns `428` with an `approve_url` the first time. Approve it as the
   buyer, then:
   ```bash
   curl -X POST localhost:8787/api/connect/finish
   ```
   The vault id is saved to `.state/vault.json` and reused. From then on the agent
   spends with no browser in the loop — that is the whole point.

---

## The rule engine

Seven rules, evaluated in `Engine._evaluate`. Deterministic, no model involved.

| Rule | Fails when | Outcome |
|---|---|---|
| `catalogue` | no product matches an item | block |
| `vendor_allowlist` | a line's vendor is not approved | block |
| `category_allowlist` | a line's category is not allowed | block |
| `per_order_cap` | cart total over the per-order cap | block |
| `monthly_cap` | would push the month past its cap | block |
| `per_unit_cap` | any line's unit price over the cap | **review** |
| `approval_threshold` | cart total over the ask-a-human line | **review** |

`block` never reaches PayPal. `review` waits for a human. `auto` pays immediately.

---

## Files

```
server.py            HTTP: console, harness, pipeline API
veto/engine.py       extract -> search -> price -> rules -> consent -> PayPal
veto/channel3.py     Channel3 search: the catalogue the agent is allowed to find
veto/prompts.py      the single LLM prompt
.env.example         the keys the runner reads (real values live in .env, gitignored)
demo/demo.html       the harness: a /veto in, one decision out, on the live engine
index.html           the console, offline, demo data
ui/src/              console source: data.js (dataset, agents, catalogue), app.css, app.js
ui/shoot.mjs         drives Chrome: interaction checks + the three screenshots
spike/               PayPal capability gate: client, mock, notes, live gate
slack/link.py        one-command Slack app install link (fills in the tunnel URL)
slack/manifest.yml   the same app manifest, for pasting by hand
demo/connect.html    the one-page wallet connect flow
proto/extract_items.py  prompt-level tests for the extraction step
shots/               console screenshots, 1440x900 @2x
```

---

## Known limits

- No persistence: requests live in memory, restart clears them.
- No Slack app yet: `/veto` is a text box in the harness. The console renders the same
  command, agent and tag model the engine produces.
- The console renders a fixed October dataset so its screenshots are reproducible;
  the harness (`/demo`) is the same design running on the live engine.
- PayPal is the mock client until `.env` holds a working sandbox secret.
- `price` uses whole cents, but PayPal amounts are formatted from them per request.
