# Veto PayPal capability gate

**Question this spike answers:** can an AI agent spend company money with a
vaulted PayPal instrument and **nobody at a checkout**?

Three paths are tested, in priority order:

| Path | What it means | Acceptable for agent spend? |
|---|---|---|
| **A** | Vault a PayPal wallet once → create an `AUTHORIZE` order with `payment_source.paypal.vault_id` → authorize → capture | **Yes, if it passes live** |
| **B** | Vault a card → create an `AUTHORIZE` order with `payment_source.card.vault_id` → authorize → capture | **Yes, fallback** |
| **C** | Payer-approval redirect | **Only to establish a vault. Never for the agent's spend.** |

Everything here is standard-library Python 3. No packages, no build step, no
server to deploy.

> **Status of this workspace:** `--mock` passes. `--live` has **not** been run
> against PayPal in this environment because no credentials exist here. Do not
> treat the mock result as proof that the sandbox merchant has Vault or card
> capability enabled. Only the live run can establish that.

---

## 1. Run the mock first (about 10 seconds)

From the repository root:

```bash
python3 spike/gate.py --mock
```

Expected: exit code `0`, a `PASS / FAIL MATRIX` with every row `PASS`, and a
verdict block ending in:

```
PATH A — VIABLE IN MOCK
PATH B — VIABLE IN MOCK
PATH C — SETUP ONLY
NEXT ACTION (one): ... run `python3 spike/gate.py --live`
```

No credentials are read and no request leaves the machine. The gate starts
`mock_paypal` on an ephemeral loopback port, runs the flow, prints the matrix,
and stops the server.

Key lines from a passing mock transcript:

```
[PASS] A: Create setup token — id=... status=PAYER_ACTION_REQUIRED
[PASS] A: Payer approval — mock payer approval -> setup token APPROVED
[PASS] A: Create payment token — vault_id=... customer_id=...
[PASS] A: Create order with vault_id — order=... status=CREATED — no payer-action link
[PASS] A: Authorize order — authorization=... status=CREATED
[PASS] A: Capture authorization — capture=... status=COMPLETED amount=1.00 USD
[PASS] A: Duplicate Request-Id replay (capture) — same capture id; no second capture
[PASS] A: Void authorization — authorization=... status=VOIDED, no capture
[PASS] B: Create card payment token — vault_id=... card=****1111
[PASS] B: Capture card vault order — capture=... status=COMPLETED
[PASS] C: Agent spend without redirect — Path A spend order had no approve/payer-action link
TOTALS: PASS=39  FAIL=0  SKIP=0  PENDING=0  WARN=0
```

`mock_paypal` is **explicitly a fake**. It simulates payer approval for the
setup token, uses synthetic IDs, and marks the order status `AUTHORIZED` (a
mock-only status; real Orders v2 has no such status). It is not a sandbox
account and it cannot prove merchant eligibility.

---

## 2. Files

| File | What it is |
|---|---|
| `paypal_client.py` | Stdlib HTTP client: OAuth2 token cache, Orders v2, Payments v2, Payment Method Tokens v3, normalized errors, mandatory idempotency header on every POST |
| `mock_paypal.py` | Local fake of the same endpoints; also runnable standalone with `python3 spike/mock_paypal.py --port 18080` |
| `gate.py` | The runner: `--mock` or `--live`, PASS/FAIL matrix, verdict block |
| `README.md` | This file |
| `NOTES.md` | Gotchas, field-name caveats, honor windows, webhook plan |
| `.gitignore` | Keeps `.state/`, `__pycache__/`, `*.pyc` out of version control |

The mock-only admin endpoints are intentionally namespaced:

```
GET  /__mock/state
POST /__mock/setup-tokens/{ID}/approve
POST /__mock/reset
```

They are not part of PayPal's API and the real client never calls them.

---

## 3. Environment variables

### Required for `--live`

| Variable | Meaning |
|---|---|
| `PAYPAL_CLIENT_ID` | Sandbox REST app Client ID |
| `PAYPAL_CLIENT_SECRET` | Sandbox REST app Secret |

### Recommended

| Variable | Default | Meaning |
|---|---|---|
| `PAYPAL_MERCHANT_ID` | *(unset)* | Sandbox **business** merchant/payer id. The gate runs without it; when set, order responses are checked for a matching payee. A direct-merchant integration does **not** need a `PayPal-Auth-Assertion` header. |
| `PAYPAL_BASE` | `https://api-m.sandbox.paypal.com` | API base. Use `https://api-m.sandbox.paypal.com` for sandbox; `https://api-m.paypal.com` is refused unless `--allow-production` **and** `PAYPAL_PRODUCTION_CONFIRM=I_UNDERSTAND_REAL_MONEY` are set. Do not use production for this spike. |
| `PAYPAL_RETURN_URL` | `https://example.com/veto/paypal/return` | Return URL sent to the setup-token approval page. Must be HTTPS. It is only where the browser lands after approval; the gate does not need to serve it. |
| `PAYPAL_CANCEL_URL` | `https://example.com/veto/paypal/cancel` | Cancel URL for the approval page. Must be HTTPS. |
| `PAYPAL_VAULT_ID` | *(unset)* | Skip vault setup and spend from an already-vaulted PayPal wallet token. The gate validates it with `GET /v3/vault/payment-tokens/{id}`. |
| `PAYPAL_SETUP_TOKEN_ID` | *(unset)* | Resume a specific setup token. If it is `APPROVED`, the gate converts it to a payment token; if it is still `PAYER_ACTION_REQUIRED`, it prints the approve URL and exits `2`. |

### Optional: Path B card details

Without these, `--live` **skips** Path B and says so. It never invents a card.

| Variable | Required for B? | Meaning |
|---|---|---|
| `PAYPAL_CARD_NUMBER` | yes | Sandbox test card number only |
| `PAYPAL_CARD_EXPIRY` | yes | `YYYY-MM`, `MM/YYYY`, or `MM/YY`; the gate normalizes it |
| `PAYPAL_CARD_NAME` | no | Defaults to `Veto Sandbox Buyer` |
| `PAYPAL_CARD_CVV` | no | Omit if you do not have one; vaulting generally does not need it |
| `PAYPAL_CARD_COUNTRY` | no | Defaults to `US` |
| `PAYPAL_CARD_POSTAL_CODE` | no | Billing postal code, if your test card needs one |

Use a number from PayPal's official sandbox card-testing list, for example
`4005519200000004` or `4012888888881881`, with a future expiry. **Never use a
real card.** See [Card testing](https://developer.paypal.com/sandbox-testing/card-testing).

### Optional: spend size and output

| Variable | Default | Meaning |
|---|---|---|
| `PAYPAL_TEST_AMOUNT` | `1.00` | Sandbox amount used for authorize/capture. Must be `> 0` and `<= 10.00`; the ceiling is deliberate. |
| `PAYPAL_TEST_CURRENCY` | `USD` | Three-letter currency. The sandbox seller must accept it. |

The gate never prints the client secret, access token, full card number, or CVV.
The state file contains only IDs and an approval URL.

---

## 4. Sandbox setup in under 10 minutes

You need a PayPal developer account. This is sandbox-only; no real money moves.

### Step 1 — Create the sandbox app and enable Vault

1. Go to <https://developer.paypal.com/dashboard/> and log in.
2. Open **Apps & Credentials** and make sure you are on the **Sandbox** tab.
3. Use the **Default Application** or select **Create App**. A name such as
   `Veto Capability Gate` is fine.
4. Open the app. Under **Sandbox App Settings → App Feature Options**:
   - check **Accept payments**;
   - expand **Advanced options** and confirm **Vault** is selected;
   - save.
5. Copy the **Client ID** and **Secret** for this sandbox app. These are
   `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`.

The Vault feature option is the one most often missed. Without it the vault
calls fail with a permission error even though OAuth succeeds. PayPal's own
vault guide says: *Sandbox App Settings → App Feature Options → Accept payments
→ Advanced options → confirm Vault is selected.*

### Step 2 — Get the sandbox buyer and seller accounts

1. In the dashboard, open **Testing Tools → Sandbox Accounts**.
2. Keep one **Personal** account (the buyer who approves the vault) and one
   **Business** account (the seller/merchant whose app you are using). Create
   them if they are not already there.
3. Use **View/Edit Account** to see the generated email and to set a password
   you know.
4. Open <https://www.sandbox.paypal.com/signin> in a private window and sign in
   with the **personal** sandbox account. This is the account that will approve
   the setup-token URL.
5. Optional: copy the business account's merchant/payer ID into
   `PAYPAL_MERCHANT_ID` if the dashboard exposes it. If you cannot find it,
   leave it unset; the gate does not require it. Do not guess or invent it.

### Step 3 — Enable Path B only if you need the card fallback

Path B needs PayPal's **Advanced Credit and Debit Card Payments** capability, in
addition to the Vault app option. PayPal must approve that capability for the
business account. Sandbox accounts are sometimes not card-eligible; if Path B
returns a capability error, that is a finding, not a bug in the gate.

Path A (PayPal wallet) is the priority. Try it first. If it passes, the gate
does not test Path B, to avoid extra sandbox captures.

### Step 4 — Export credentials and run

```bash
cd /path/to/veto

export PAYPAL_CLIENT_ID='<sandbox client id>'
export PAYPAL_CLIENT_SECRET='<sandbox client secret>'
export PAYPAL_MERCHANT_ID='<sandbox business merchant id>'  # optional
export PAYPAL_BASE='https://api-m.sandbox.paypal.com'

python3 spike/gate.py --live
```

The first live run will usually stop at **pending payer approval** and print a
URL like:

```
https://sandbox.paypal.com/agreements/approve?approval_session_id=...
```

1. Open that URL in the private window signed in as the **personal** sandbox
   buyer.
2. Approve the wallet connection.
3. Rerun the exact same command:

```bash
python3 spike/gate.py --live
```

The gate saves the setup token in `spike/.state/live-setup.json` (mode `0600`,
IDs only, no secrets) and resumes automatically. If you switch apps or use a
different `PAYPAL_BASE`, the state is ignored to avoid mixing vaults between
apps.

To add the card fallback, export the card variables before the run:

```bash
export PAYPAL_CARD_NUMBER='4005519200000004'
export PAYPAL_CARD_EXPIRY='2030-12'
export PAYPAL_CARD_NAME='Veto Sandbox Buyer'
export PAYPAL_CARD_CVV='123'
export PAYPAL_CARD_COUNTRY='US'
export PAYPAL_CARD_POSTAL_CODE='95131'
```

---

## 5. What the live run does

`--live` is conservative by design:

1. Fetches and caches an OAuth2 token.
2. Reuses `PAYPAL_VAULT_ID`, resumes an approved setup token, or creates a new
   setup token. If approval is still needed, it prints the URL and **stops**
   with exit `2` rather than pretending the spend worked.
3. With a vault, creates a real `AUTHORIZE` order (`1.00` by default) with
   `payment_source.paypal.vault_id` plus merchant-initiated
   `stored_credential`, authorizes it, captures the authorization, and verifies
   `COMPLETED`.
4. Repeats the capture with the same `PayPal-Request-Id` and checks for the
   same capture id (no double charge).
5. Creates a separate authorization and **voids** it, then verifies `VOIDED`.
6. If Path A has not fully passed, tries Path B using the card env vars.
7. Prints the matrix and a verdict. Errors include HTTP status, PayPal `name`,
   `details[].issue`, and `debug_id`.

The live gate uses **`POST /v2/payments/authorizations/{id}/capture`** after an
`AUTHORIZE` order. Do not use `POST /v2/checkout/orders/{id}/capture` for this;
PayPal returns `422 ACTION_DOES_NOT_MATCH_INTENT` because that endpoint is for
`intent: CAPTURE`. The mock reproduces that error deliberately.

---

## 6. How to read the verdict

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Mock passed, or at least one of A/B was proven on the real sandbox (see caveats in the verdict) |
| `1` | The gate ran and no path was proven, or a safety path (void / idempotent replay) failed |
| `2` | Blocked, missing configuration, or waiting for human approval |

### Matrix outcomes

| Outcome | Meaning |
|---|---|
| `PASS` | That check really succeeded |
| `FAIL` | PayPal rejected it or the response was wrong; details are in the row |
| `SKIP` | Not applicable or not configured (for example, A passed so B was not tested; no card env vars) |
| `PENDING` | A human must approve the setup token before anything else can happen |
| `WARN` | Evidence is incomplete or a cleanup step failed; inspect it |

### Verdict meanings

| Verdict | What to do |
|---|---|
| **PATH A — VIABLE** | The core premise works: a vaulted PayPal wallet can be charged server-side with no redirect. Record the vault id, order id, and capture id. |
| **PATH A — PENDING HUMAN APPROVAL** | The vault exists but the buyer has not approved it. Open the printed URL and rerun. |
| **PATH B — VIABLE** | Path A failed, but a vaulted card completed the server-side flow. A is still the priority path; decide whether card-only is shippable or fix A. |
| **PATH A — SPEND VIABLE, VOID FAILED** | Money moved, but the veto path is unsafe. Fix the void before building UI. |
| **PATH A — SPEND VIABLE, IDEMPOTENCY NOT PROVEN** | A retried capture did not replay. This is a double-charge risk; fix it before building UI. |
| **PATH C ONLY** | PayPal required a payer redirect for the spend. The concept is a wrapper around checkout; the pitch must change. |
| **NO PATH PROVEN** | Quote the first failure's `debug_id` and `issue` values when checking app capabilities with PayPal support. Do not build UI. |

`--mock` **always** says "VIABLE IN MOCK". That is not a sandbox result. The
live verdict is the only one that can establish merchant capability.

---

## 7. Sandbox vs live/production

| | Sandbox | Live/production |
|---|---|---|
| API base | `https://api-m.sandbox.paypal.com` | `https://api-m.paypal.com` |
| Credentials | Sandbox app Client ID/Secret | Live app credentials |
| Money | Fake | Real |
| This spike | Allowed and expected | **Refused by default** |
| Payer approval | Sandbox personal account | Real customer, real consent |

The gate refuses the production base unless you pass `--allow-production` and
also set `PAYPAL_PRODUCTION_CONFIRM=I_UNDERSTAND_REAL_MONEY`. The hackathon
submission is sandbox-only; do not use that escape hatch.

---

## 8. Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| `401 AUTHENTICATION_FAILURE` on token fetch | Sandbox Client ID/Secret mixed with live, rotated secret, or typo | Recopy the sandbox app credentials; do not reuse live credentials |
| `403` / `PERMISSION_DENIED` / `NOT_AUTHORIZED` | App feature options missing `Vault` or `Accept payments`, or the merchant is not eligible | Enable both on the app; if wallet vaulting is not approved for the account, that is a capability result |
| `422 VAULT_ID_NOT_FOUND` / `PAYMENT_SOURCE_CANNOT_BE_USED` | Vault belongs to another app/merchant, was deleted, or env points at the wrong app | Clear `spike/.state/live-setup.json`, use the right credentials, re-vault |
| Order status `PAYER_ACTION_REQUIRED` or a `payer-action` link | The payment source is not usable for merchant-initiated spend | This is Path C. Do not call it agent spend |
| `422 ACTION_DOES_NOT_MATCH_INTENT` | You called orders capture on an `AUTHORIZE` order | Use `POST /v2/payments/authorizations/{id}/capture`; the client's `capture_authorization()` does this |
| Card failure (`INSTRUMENT_DECLINED`, `CARD_EXPIRED`, capability error) | Card not eligible in sandbox or test data wrong | Use a current test card from PayPal's card-testing page; if it still fails, B is not available on this merchant |
| `ORDER_ALREADY_CAPTURED` | A retry used a different `PayPal-Request-Id` | Reuse the original idempotency key; in Veto, derive it from the consent nonce |
| `AUTHORIZATION_VOIDED` after a capture attempt | The authorization was already voided | Expected on the void-path check; do not capture after a veto |
| State file from another app is ignored | `client_id`/`PAYPAL_BASE` changed | Delete `spike/.state/live-setup.json` or set `PAYPAL_VAULT_ID` explicitly |
| `PAYPAL_MERCHANT_ID` payee mismatch | Merchant ID does not belong to the app's seller | Correct it or unset it; it is only a check |

Every live error row gives you the `debug_id`. PayPal support and the dashboard
logs are much faster when you quote that id, the API path, and the `issue`.

---

## 9. Security and cleanup

- Credentials are read from the environment only. Nothing is written to disk
  except `spike/.state/live-setup.json`, which contains a setup token id, an
  approval URL, a vault id, a customer id, and a client-id fingerprint. It does
  **not** contain a client secret or access token.
- The gate masks card numbers in output and never prints the full PAN or CVV.
- Use sandbox test cards only. Never put a real card number in an environment
  variable for this spike.
- A live run captures real sandbox money (default `$1.00`). It is fake money,
  but it will appear in the sandbox business account.
- To reset, delete `spike/.state/live-setup.json`; optionally delete the payment
  token in the sandbox account.
- The mock is loopback-only and unauthenticated by design. Do not expose the
  mock port to a network.

---

## 10. What the mock does and does not prove

**Proves (offline):**

- The request shapes and status transitions for A and B
- That a vaulted source can create an `AUTHORIZE` order with no payer-action link
- That authorization capture works through the correct payments endpoint
- That a separate authorization can be voided and cannot be captured afterward
- That `PayPal-Request-Id` is sent on every POST and that duplicate order/capture
  requests replay instead of creating a second resource
- That PayPal-shaped errors are normalized to status + name + issue + debug id

**Does not prove:**

- That your sandbox merchant has Vault or card capability enabled
- That PayPal risk/eligibility approves your merchant
- That the sandbox buyer approval flow works with your account
- That live fees, currencies, 3DS, or webhooks behave as expected

Only `python3 spike/gate.py --live` can answer those.

---

## 11. Official references

- [Payment Method Tokens v3](https://developer.paypal.com/api/payment-tokens/v3/)
- [Save a PayPal Wallet](https://developer.paypal.com/api/payment-tokens/save-without-purchase/paypal)
- [Save a card](https://developer.paypal.com/api/payment-tokens/save-without-purchase/cards)
- [Orders v2](https://developer.paypal.com/api/orders/v2/)
- [Payments v2](https://developer.paypal.com/api/payments/v2/)
- [Idempotency](https://developer.paypal.com/api/rest/reference/idempotency)
- [Authorization and honor period](https://developer.paypal.com/payment-methods/auth-honor)
- [Void an authorized payment](https://developer.paypal.com/checkout/void-authorized-payment)
- [Webhook verification](https://developer.paypal.com/api/rest/webhooks/rest/)
- [Sandbox accounts](https://developer.paypal.com/sandbox-testing/accounts)
- [Card testing](https://developer.paypal.com/sandbox-testing/card-testing)
- [Get started with PayPal APIs](https://developer.paypal.com/api/rest/)
