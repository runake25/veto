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
1. EXTRACT   text → items[{name, qty}]            (LLM; never prices, vendors, payees)
2. SOURCE    Channel3 per item, scoped to that agent's allowed vendors/categories
3. PRICE     code picks the candidate, unit × qty
4. JUDGE     deterministic rules → auto | review | block, observed vs limit
5. CONSENT   sha256(cart) · single use · 10 minutes · named approver
6. SETTLE    PayPal vault → order → authorization → capture
             (block or veto = zero PayPal objects, ever)
7. RECORD    events, agent, tag, vendor → analytics
```

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

Evaluation order: `catalogue_match` → `category_allowlist` → `vendor_allowlist` →
`monthly_cap` (workspace + agent) → `per_order_cap` (cart and category subtotal) →
`per_unit_cap` (line, category override wins) → `approval_threshold` (cart and category
subtotal). Outcome = max severity; every reason stores cents and a sentence.

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

| Method + path | Auth | Status |
|---|---|---|
| `POST /api/v1/signup` `{workspace_name,name,email,password}` | — | **M1 done** |
| `POST /api/v1/login` `{email,password}` | — | **M1 done** |
| `POST /api/v1/logout` | session | **M1 done** |
| `POST /api/v1/demo-session` | — | **M1 done** |
| `GET /api/v1/me` | session | **M1 done** |
| `GET /api/v1/agents` | session | **M1 done** |
| `POST /api/v1/agents` · `PATCH /api/v1/agents/{id}` | admin | M2 |
| `GET /api/v1/agents/{id}/search?q=` | session | M2 — scoped product search |
| `POST /api/v1/simulate` | session | M2 — dry-run, persists nothing |
| `GET /api/v1/overview` | session | M2 |
| `GET /api/v1/requests` · `GET /api/v1/requests/{id}` | session | M3 |
| `POST /api/v1/requests` | agent / session | M3 |
| `POST /api/v1/requests/{id}/decision` · `/void` | approver / admin | M3 |
| `POST /api/v1/agents/{id}/keys` · `DELETE …/keys/{kid}` | admin | M3 |
| `GET /api/v1/analytics?from&to&group=` | session | M4 |
| `GET /api/v1/integrations` | session | M5 |
| `POST /api/slack/veto` · `POST /api/slack/interact` | Slack signature | kept from v1 |
| `GET /api/health` | — | extended (db, seeded, counts) |

Legacy v1 endpoints (`/api/request`, `/api/decide`, `/api/state`, `/api/connect*`) remain
until M3 rewires the pipeline; `/` still serves the old console until M4 flips it to the app.

## 5. Screens and data ownership

Five screens, all live from the same store. Each fact has exactly one owner; other
surfaces may show a count or a link.

| Screen | Owns |
|---|---|
| **Overview** | needs-me count · committed-unsigned · settled this month · agent activity · top-3 pending as links · where the money went |
| **Inbox** | the only request list; filters live in the URL |
| **Record** | lines, reasons, consent hash, PayPal ids, events, approve/veto/void |
| **Agents** | workspace → agent budget hierarchy, rules + category overrides, vendors, categories, tags, channel, API key, **scoped search**, test-a-cart |
| **Analytics** | aggregates by agent / tag / vendor / category / rule, drill-down links into Inbox |
| **Integrations** | Slack, API keys, web, wallet |

Guided rule setup (option A): one form with template defaults, an optional category
override, and a read-only "advanced" panel showing the rule rows. Not a raw editor.

## 6. Auth and tenancy

Email + password (scrypt), opaque sessions (sha256 stored, HttpOnly cookie, SameSite=Lax,
7-day expiry, `Secure` only on https requests). Roles: admin, approver, viewer. Sign-up
creates a workspace and its first admin. The seeded demo workspace is reachable in one
click so a judge can interact without sign-up. Agent API keys are `vkey_…`, shown once,
stored hashed.

## 7. Motion and design rules

Keep the framed-sheet direction: tinted stone desk, white sheet, one accent hue, IBM Plex,
no shadows at rest. Motion budget: (1) press feedback on every control (0.97 scale, 80ms),
(2) one moving selection indicator per view, (3) a single first-enter stagger, (4) view
transitions that preserve provenance (list row → record). Loading states are real: the
pipeline reports steps as they finish. `prefers-reduced-motion` collapses everything to
the end state.

## 8. Milestones

| # | Ship | Status |
|---|---|---|
| M0 | this spec, branch `v2` | **done** |
| M1 | SQLite store, auth, seeded demo, session API, React shell + auth screen | **done (2026-10-22)** |
| M2 | policy engine, agents/rules CRUD, scoped search, dry-run, Overview live | next |
| M3 | requests/consent/payments/void/events, Slack rewired, API keys | |
| M4 | Inbox, Record, Agents, Analytics screens; delete the old console; `/` flips to the app | |
| M5 | parallel search, streamed pipeline, rate limits, hosting, UI smoke test | |
| M6 | README/SPEC/PRODUCT, deploy, video, Devpost text | |

## 9. Running it

```bash
python3 server.py            # http://127.0.0.1:8787/app/
cd web && npm install && npm run build   # rebuild app/ after frontend changes
cd web && npm run dev                    # dev server on :5173, proxies /api to :8787
python3 -m unittest discover -s tests    # backend tests
```

`PAYPAL_LIVE=0` runs the whole loop against the in-process PayPal simulator with no keys.
Hosted deploys pass the approved `PAYPAL_VAULT_ID` through the environment.

## 10. Non-goals (v1)

Multi-workspace Slack OAuth · email intake · MCP server · Postgres · SSO · multi-currency ·
refunds beyond voiding an uncaptured authorization · a framework migration of the runner.
