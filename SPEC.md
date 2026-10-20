# Veto — MVP Spec (Slack-first)

> An AI agent can spend company money only inside a charter, invoked from the conversation where the need appears, and unable to pay until the approval is a real, signed, single-use thing.

Target: PayPal AI Hackathon, submission closes **Nov 12 2026 12:00 PT = Nov 13 04:00 HKT**.

The rule that governs everything below: **the spine ships first.** Additions are allowed only after the spine runs end-to-end without a human touching the terminal.

---

## 1. The one loop

```
#office-ops
  someone: "we're out of coffee capsules and toilet paper"
  someone: /veto coffee capsules and toilet paper
        │
        ├─ 1. EXTRACT   LLM reads thread context + command → items[{name, qty}]   (structured output)
        ├─ 2. SOURCE    Channel3 search per item → candidates{title, price, vendor, image, buy_link}
        ├─ 3. PRICE     code picks a default candidate, computes unit price + total  (LLM never sets a price)
        ├─ 4. JUDGE     charter rules, deterministic → decision + reasons[]
        │
        ├─ AUTO    under threshold  ──────────────────────────┐
        ├─ REVIEW  over threshold   → Block Kit approval card │
        ├─ BLOCK   hard cap / vendor / category → refusal     │
        │             + "what would unblock it"               │
        │                                                     │
        ├─ APPROVER: Approve │ Veto │ reply with an amendment ─┤
        │             amendment → re-plan → re-propose        │
        └─ 5. CONSENT signed, single-use, 10-min expiry → PayPal AUTHORIZE → CAPTURE
                                                            → receipt posted in thread
                                                            → ledger entry
```

Four branches, one pipeline. Every branch is demonstrable in the 3-minute video.

---

## 2. Data model — six objects, no more

| Object | Fields |
|---|---|
| **Charter** | id, name, owner, approver_id, budget_line, currency, rules{}, paypal_connection_id, created_at |
| **Request** | id, charter_id, slack_channel, slack_thread_ts, requester_id, raw_text, context_snippet, items[], status |
| **Item** | name, qty, chosen_candidate{}, unit_price, candidates[] |
| **Decision** | request_id, outcome(auto/ review/ block), reasons[]{rule_id, observed, limit, message}, decided_at |
| **Consent** | id, request_id, cart_hash, amount, currency, approver_id, rule_snapshot, nonce, issued_at, expires_at, single_use |
| **LedgerEntry** | id, request_id, event, paypal_order_id, paypal_capture_id, amount, at |

`Consent` is the trust story. It binds a specific cart by hash. If the cart changes by one line the consent no longer verifies → no capture. Single-use, expiring, revocable from Slack.

---

## 3. Rules v1 — exactly seven

1. `monthly_cap`
2. `per_order_cap`
3. `per_unit_cap`
4. `vendor_allowlist`
5. `category_allowlist`
6. `approval_threshold` — over → human, at/below → auto
7. `expires_at`

Every additional rule costs a unit test, a UI control, an error message, and a demo path. Rules 8+ are v1.5. If a judge asks "can you add a rule?" the answer is a config edit, not a code change — that's the point of a pure decision function.

**Rule engine contract:** `decide(charter, cart, now) -> Decision`, pure, no I/O, no LLM. Fail closed on anything unparseable. This is the most-tested unit in the repo.

---

## 4. Slack surface — this is the product

Slack is where the work already happens, so it's the whole UX. The web app is admin + ledger.

- **`/veto <text>`** — primary entry point.
- **Message shortcut "Veto this thread"** — same pipeline, reads the thread, ~1 extra handler. Cheap, high value.
- **Request card** — items, unit prices, vendor, total, the rule that fired, and what the agent chose *not* to buy and why.
- **Approval card** — Approve / Veto buttons + a free-text "amend" field ("only 2 boxes", "nothing over $15").
- **Receipt card** — order id, captured amount, approver, timestamp, running budget burn. Posted as a thread reply so the decision record lives where the conversation did.
- **Home tab** — active charters, spend this month vs cap. Pure read, high Design value.

Approval must be idempotent and expire. Two admins clicking Approve must not double-charge — keyed on the consent nonce.

---

## 5. Web app — thin and read-mostly

- Charter admin: create/edit, seven rules, connect PayPal.
- Ledger: every request, decision, consent, order, capture — filterable.
- Budget burn per charter.

**Not** an order management ERP. No POs, no invoices, no tax, no vendor onboarding, no approvals matrix. The moment this becomes a CRUD treadmill, the Design score drops and the demo stops mattering.

---

## 6. PayPal — the four calls that matter

| Step | What |
|---|---|
| Vault once | `POST /v3/vault/setup-tokens` → approve once → `POST /v3/vault/payment-tokens` → store `vault_id`. The "connect company wallet" moment. |
| Create order | `intent: AUTHORIZE`, `payment_source.paypal.vault_id`, header `PayPal-Request-Id: <consent nonce>` |
| Move money | authorize → capture on approval; void on veto, on expiry, or on failed cart-hash check |
| Reconcile | webhook receiver → verify signature → match order id → ledger entry |

Verify exact field names against the Orders v2 reference on day 1 — they shift. Hard requirements either way:

- **`PayPal-Request-Id`** on every POST, derived from the consent nonce. A retried capture must not double-charge.
- **Void path written before the approve path.** A veto that leaves a dangling authorisation is the only failure a judge will actually catch.
- **`UNKNOWN` is a real state.** On timeout, don't guess. Re-query, then converge: captured → ledger; voided → release the hold.

---

## 7. Day-1 capability gate (before any UI)

The entire premise is *the agent pays while nobody is at a checkout.* Prove it in one day, in this order, and record which one works:

- **A** — vaulted PayPal wallet → create order with `vault_id` → authorize → capture.
- **B** — vaulted card in the sandbox → same orders flow.
- **C** — payer-approval redirect. **Only acceptable to establish the vault, never for the agent's spend.** If this is the only path that works, the concept is a wrapper around checkout and the pitch must change.

This gate matters because a public entrant in this hackathon documented that their sandbox merchant couldn't take card payments at all and they had to fall back to Payouts. Discovering that on day 18 is fatal. Test it on day 1 with a 40-line script and a hardcoded sandbox buyer.

---

## 8. Cut list

| Cut | Why | Do instead |
|---|---|---|
| Payslip / HRIS import | Different product, different buyer (HR), days of parsing work | A 3-row CSV of budget lines. Same demo value, 30 minutes. |
| Traffic / food subsidies, reimbursements | Needs Payouts — a second rail, a second risk, and it's *post*-purchase. Off-thesis. | One line in "what's next". |
| "Categorise hard-to-categorise expenses" | That's expense management, post-purchase, and full of funded competitors | Drop entirely. Your thesis is *before* the spend. |
| Jira integration | Jira isn't chat — `/veto` has no home there. Adds OAuth + ticket schema + webhook for zero demo seconds | Optional: on capture, file one Jira issue. ~2h, after v1. |
| Confluence | A wiki. No transaction surface. | Nothing. |
| Desktop app | Packaging cost, no demo value, no judge cares | Web app. |
| LLM browsing real stores | Browser agents break on camera; Channel3 already returns valid buy links | Channel3 API or MCP. |
| Multi-currency, tax, invoices, PO matching | ERP scope | Drop. |
| RBAC / org roles | Single approver from config is sufficient | Config value. |
| Agent-to-agent negotiation, marketplace | Great v3, brutal v1 | Roadmap. |

**Rename the product internally.** "Order management tool" is the phrase that dragged the ERP scope in. It's a **charter + ledger** app. That single rename will keep you honest for three weeks.

---

## 9. "Can I add more ideas?" — yes, under one rule

Capacity is not your constraint. **Demo reliability is.** Every surface you add multiplies the ways a live demo dies.

> A new idea is allowed only if it is a new **entry point** into the same `Request → Decision → Consent → Order` pipeline — never a new pipeline.

| Cheap (do after the spine runs) | Costly (roadmap slide) |
|---|---|
| "Veto this thread" message shortcut | Payouts / reimbursements |
| Weekly restock cron → same pipeline | Jira + Confluence |
| Slack Home tab dashboard | Browser automation |
| 2-of-3 approval for large orders | Multi-currency, tax |
| "Why did you veto that?" explainer | Agent marketplace |
| Conditional veto ("only 2 boxes") | Real vendor onboarding |

Anything in the right column is a *different product*. The left column is the same product, more of it.

---

## 10. Build order

| Days | Work |
|---|---|
| 1 | **Capability gate** (§7). Sandbox merchant + vault + authorize + capture, script only. Record which path works. |
| 2–3 | Rule engine + tests. Pure `decide()`, fail-closed, seven rules. |
| 4–5 | Channel3 client + cart builder + price normalisation. Handle "no candidate found" gracefully. |
| 6–7 | PayPal client: create/authorize/capture/void, Request-Id idempotency, webhook receiver, `UNKNOWN` reconciliation. |
| 8–11 | Slack app: OAuth, `/veto`, Block Kit cards, interactions endpoint, threaded receipts. |
| 12–13 | Amend → re-plan loop. The differentiator. Protect this time. |
| 14–15 | Web app: charter admin + ledger + burn. Read-only where possible. |
| 16–17 | Hardening: retries, timeouts, void-on-veto, double-click, expired consent, empty search. |
| 18 | Hosted demo + README + one-command run + seed data. **Feature freeze.** |
| 19–21 | Video script → record → cut to <3:00. |
| 22 | Submission form: tools list, license in About, video link. |
| 23 | Buffer. Submit early — never on deadline day. |

---

## 11. Demo script (3:00)

| Time | Beat |
|---|---|
| 0:00–0:15 | Cold open: an agent tries to spend $412 on coffee. Card: **"Vetoed. $0 moved."** |
| 0:15–0:40 | The problem: agents can pay now; companies can't let them. One sentence on who this is for. |
| 0:40–1:05 | Create the charter in 20s: caps, allowed vendors, approver. |
| 1:05–1:35 | Slack: `/veto` → real products appear → priced cart → **auto-approved within policy**. |
| 1:35–2:05 | The veto: over unit cap → card says which rule and what would unblock → approver replies *"only 2 boxes"* → re-shop → re-propose. |
| 2:05–2:30 | Approve → money actually moves → receipt in thread with order id → ledger updates. |
| 2:30–2:50 | The failure branch: try a non-allowlisted vendor → blocked, zero calls to PayPal. |
| 2:50–3:00 | Close: every dollar has a named human and a receipt. If you use Channel3, say so here. |

---

## 12. Submission non-negotiables

- Public repo, **license visible in the About section** (detectable, not just a LICENSE file).
- Hosted demo URL *or* complete run instructions + seed data. A mockup fails the requirement outright.
- YouTube link, public, under 3:00, showing it running.
- "Tools used" section naming the model and every service.
- English throughout. Sandbox only. No third-party logos or music you don't have rights to.
- If you use Channel3, actually call it — projects that only reference it don't qualify.
