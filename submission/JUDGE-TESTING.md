# Testing Veto — judge guide

Veto is the consent layer between an AI agent and the company PayPal wallet: an agent
proposes, its charter judges, a human signs when it matters, and PayPal moves money only
against that signature. Everything below runs on **PayPal sandbox money** — no real charges.

**Live demo:** <https://veto-bracken-field.onrender.com> · **Source:** <https://github.com/runake25/veto>

## Access

| Surface | How to get in |
|---|---|
| **Console** | Open the live URL and press **Open the demo workspace** — no credentials needed. Or sign in with `caroline@brackenfield.demo` / `veto-demo-2026` (approver: signs, vetoes, voids). |
| **Admin seat** | `ops@brackenfield.demo` / `veto-demo-2026` — needed only for minting API keys, renaming the workspace or changing settings. |
| **Slack** | [Join the Veto llc workspace](https://join.slack.com/t/vetollc/shared_invite/zt-4drdephaf-2GOX~63llMwFmLJPM7XFRQ) (invitation valid ~3 weeks). The bot is `@veto`; try `/veto-help`. |
| **API** | No key is shared: sign in as admin → **Integrations → Agent API keys → New key**. The secret is shown once, and the exact `curl` command is on that card. |

Demo workspace: **Bracken & Field** — four agents, two humans, $2,850 monthly budget.
The hosted demo keeps one request waiting for a signature; its consent window is opened to
60 days so judges can always sign it (the product default is 10 minutes).

## 5-minute path (the core claim)

1. **Overview** — open the live URL. You land on one waiting request
   (**REQ-1056 · 27-inch 4K monitor · $268.51**), the month's **held / settled / left** and the
   agent fleet. Press **Review 1 request**.
2. **Sign it** — open the waiting record. Read the verdict: every rule that fired with its
   *observed vs limit* figure, the priced cart, and the consent (cart sha256 + window). There is
   **no PayPal object yet**: a request cannot spend without this signature.
   Press **Approve & capture**, confirm. The money moves in the PayPal sandbox, the record gains
   its **order → authorization → capture** ids and its trail entries, and the Overview updates
   live. (Prefer not to spend the demo's waiting cart? **Veto** it instead — nothing moves, and
   the trail records the refusal. Both are real tests.)
3. **Check the money evidence** — open a captured record, e.g.
   **[REQ-1051](https://veto-bracken-field.onrender.com/app/#/inbox/REQ-1051)** (Standing desk):
   PayPal **Order / Authorization / Capture** ids (`9WS09146XD267181K → 48793013K91633409 →
   6RY882053V175503M`), consent hash, `used_at`, and the append-only trail. Only the capture is
   counted as spend.
4. **See a refusal** — open
   **[REQ-1057](https://veto-bracken-field.onrender.com/app/#/inbox/REQ-1057)** (Coffee machine,
   Pantry runner): blocked by the category allowlist, no consent, no PayPal object, no money.
   The rules and their limits are on the record.
5. **Test an agent** — **Agents → Desk kit**
   ([deep link](https://veto-bracken-field.onrender.com/app/#/agents/agt_62716c89)): charter
   figures, two standing jobs, the dry run and the receipts. In **Test a request**, type
   `2 anti-fatigue standing mats for the studio` and press **Run**: the streamed pipeline prices a
   real Channel3 offer, shows the verdict (in our test it crossed the $50 signature line) and
   the catalogue around it. Any row can be **Request this** (a real request).
6. **Compare with Analytics** — **Analytics**: settled / held / stopped and requests reconcile
   with Overview and Inbox; **by agent**, **by category**, **Daily numbers** all read the same rows.
7. **Slack** — join the workspace, then `/veto-status` shows the waiting cart with **Approve /
   Veto buttons**; `/veto-budget` gives the same money as Analytics; `/veto 2 packs of bamboo
   toilet paper` runs the real pipeline from Slack; `/veto-check paper towels` shows the
   rule-scored catalogue; `/veto-routines` lists the standing jobs; `/veto-help` lists everything.
8. **Integrations** — PayPal wallet state/mode/vault, the agent-key mint/revoke card with the
   one-line `curl`, and the Slack endpoints with signature state.

## Full checklist — every strength, one step each

| # | Strength | Where | Do | What proves it |
|---|---|---|---|---|
| 1 | **Policy engine** — auto / review / block | Inbox + records | Compare a captured record, the waiting record (review), [REQ-1057](https://veto-bracken-field.onrender.com/app/#/inbox/REQ-1057) (block) | Each decision stores the rules that fired with observed value and limit |
| 2 | **Charter caps** | Agent page → Charter | Read monthly / per-order / per-item / signature-above, merchants, categories, exceptions | The figures that judged the requests are visible and match the records |
| 3 | **Consent is a separate object** | Any record → Consent | Read the cart hash, single-use, window, `used_at` | No consent → no order; the hash binds the signature to that exact cart |
| 4 | **PayPal Orders v2** | Captured record → PayPal | Open order → authorization → capture ids | Money moves only at capture; retries reuse the consent nonce as idempotency key |
| 5 | **Refusals leave receipts** | [REQ-1054](https://veto-bracken-field.onrender.com/app/#/inbox/REQ-1054) (vetoed), [REQ-1055](https://veto-bracken-field.onrender.com/app/#/inbox/REQ-1055) (voided), blocked rows | The trail, the reasons, no PayPal object | A block is not "zero orders"; it is no capture without signed consent |
| 6 | **Channel3 catalogue** | Agent page → Test a request → Similar offers | Run a cart; inspect offers, vendors, prices | Real offers, scoped to the agent's rules; rule-breaking offers are marked, not hidden |
| 7 | **Three doors, one record** | Inbox → Source column | Slack request, console dry-run `Request this`, API-key `curl` | All three land in the same store with the same trail (Source: Web / Routine / API key) |
| 8 | **Slack integration** | Slack workspace | `/veto`, `/veto-check`, `/veto-status`, `/veto-budget`, `/veto-routines`, `/veto-help`; use the Approve/Veto buttons | Signed commands answer; a Slack approval writes the same store the console reads |
| 9 | **Standing jobs** | Agent page → Standing jobs | Open the `⋯` menu: **Run now**, pause, edit; check the monthly envelope and recent runs | A run is a real request (Source: Routine, e.g. [REQ-1061](https://veto-bracken-field.onrender.com/app/#/inbox/REQ-1061)), subject to the same rules |
| 10 | **Receipts per agent** | Agent page → Orders | Read the last 15 requests: amount, status, who asked, who signed | Every spend is traceable to a person and a record |
| 11 | **Live board (SSE)** | Overview / Inbox | Leave it open, then act from Slack or another tab | "Updated HH:MM · Live" and the rows change without a reload |
| 12 | **Roles & settings** | Settings (admin vs approver) | Sign in as `ops@…` and as `caroline@…` | Admin-only actions (keys, workspace rename) vs the approver's read-only reasons |
| 13 | **Reliability / honesty** | `/api/health` | Open it | `ok`, `paypal: sandbox`, `vault: true`, `seeded: true`; the free instance's history is replayed through the real pipeline, never hand-written |
| 14 | **No real money** | Any screen | Everything above | Sandbox only; refunds are out of scope (void withdraws an uncaptured request) |

## Optional: API-key test (admin)

Mint a key on **Integrations**, then (the card shows the exact command):

```bash
curl -s -X POST https://veto-bracken-field.onrender.com/api/v1/requests \
  -H "Authorization: Bearer vkey_…" \
  -H "Content-Type: application/json" \
  -d '{"text":"2 packs of bamboo toilet paper"}'
```

That request runs the same extract → catalogue → price → judge → consent → PayPal path and
appears in the Inbox with Source **API key** (sandbox capture if it passes the charter).

## Known limits (deliberate)

- **Sandbox only.** No real charges; a capture cannot be reversed in this build (`Void` only
  withdraws an uncaptured request).
- **One demo workspace, one approver seat**, no multi-currency or accounting export.
- **Category rules are the weakest signal**; merchant, unit, cap and threshold rules do the real work.
- The demo's history is genuine but its **timestamps are arranged** across the month so Analytics
  has a month to draw; rules, consents and PayPal ids are real.
- Approving the seeded waiting cart is expected (it is sandbox money). If it is gone, create your
  own with `/veto …` in Slack or an agent dry run → **Request this**, then find it in the Inbox.

## Reproduce / verify (optional)

```bash
python3 -m unittest discover -s tests   # 349 tests
python3 tools/check_m4.py               # 42 read-only checks over HTTP
python3 tools/check_polish.py           # 21 read-only checks over HTTP
cd web && npm run build                 # tsc --noEmit + vite build
```

Slack wiring can be re-proved without spending: `python3 slack/verify.py` (adds `--live` only if
you want it to post a real `/veto`).
