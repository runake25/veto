# Veto PayPal capability gate — NOTES

Everything here is a finding from this spike, a mitigation for a known PayPal
gotcha, or an item explicitly marked **verify against docs** because the exact
field/behavior could not be confirmed without running live. The mock passing
does not make a live item true.

Date of research: 2026-10-20. PayPal docs change; re-check the links at the end.

---

## 1. The gotchas that matter

| # | Gotcha | One-line mitigation |
|---|---|---|
| 1 | **Sandbox merchants are not all card-eligible.** Card vault/spend needs Advanced Credit and Debit Card Payments approval; some sandbox business accounts cannot take cards at all. | Test Path A first; treat a card capability error as a result, not a code bug. Try another sandbox business account or fall back to A. |
| 2 | **Vault is an app feature option, not just an API.** A valid OAuth token can still get `403` if `Vault` and `Accept payments` are not selected on the sandbox app. | Enable both under App Feature Options → Advanced options → Vault, then create a fresh token. |
| 3 | **Vaulting a PayPal wallet always needs one payer approval.** The setup token comes back `PAYER_ACTION_REQUIRED` with an `approve` link. That redirect is acceptable **only** to establish the vault. | Treat approval as onboarding, store the resulting `vault_id`, and never use a redirect for the agent's spend. |
| 4 | **The setup token expires.** PayPal says the default lifetime is 3 days. | Create the setup token only when the buyer is ready; resume with `PAYPAL_SETUP_TOKEN_ID` or the gate's state file. |
| 5 | **`intent: AUTHORIZE` is captured through Payments, not Orders.** `POST /v2/checkout/orders/{id}/capture` on an AUTHORIZE order returns `422 ACTION_DOES_NOT_MATCH_INTENT`; that endpoint is for `intent: CAPTURE`. | Use `POST /v2/payments/authorizations/{auth_id}/capture`; `PayPalClient.capture_authorization()` does. The mock reproduces the 422. |
| 6 | **A capture is not a void.** Once captured, the authorization is `CAPTURED`; you refund the capture, you cannot void it. | Write the void path before the capture path; use a separate authorization for the void test. |
| 7 | **Authorization timing is two different clocks.** The authorization is valid for 29 days; the high-success **honor period** is the first 3 days. After that, capture can still work but is risk/funds dependent. | Capture within 3 days; reauthorize after day 3 (new authorization id, restarts honor); never wait until day 29. |
| 8 | **`PayPal-Request-Id` is the double-charge guard.** PayPal stores it for a limited, API-specific period; the docs' refund example says up to 45 days, but do not assume that for every endpoint. | Derive it deterministically from the consent nonce, keep the mapping, and retry with the **same** header. |
| 9 | **Request-Id length/uniqueness.** PayPal's docs recommend UUID-sized values and mention a 38 single-byte character limit; it must be unique per API call type. | The client hashes the idempotency key to `veto-` + 32 hex chars = 37 chars. Never reuse a key across different call types. |
| 10 | **Not every API supports `PayPal-Request-Id`.** The token endpoint is a special case; the spike still sends a deterministic id there. | Verify support per endpoint; the client sends it on every POST and on the DELETE used by this spike. |
| 11 | **Card data changes your PCI scope.** Storing card numbers/CVV directly is PCI SAQ D; PayPal's card-vault guide requires an existing advanced card integration and SAQ D compliance. | For the spike use sandbox test cards only; for production, use hosted fields / PayPal's card components and never handle raw PAN in Veto. |
| 12 | **`payment_source.token.type` is `SETUP_TOKEN`.** It is not `PAYMENT_METHOD_TOKEN` and there is no `payment_source.paypal` on payment-token creation. | Use `{"token": {"id": setup_id, "type": "SETUP_TOKEN"}}`; schema-confirmed. |
| 13 | **The setup-token `experience_context` is required for the PayPal wallet contingency.** Missing `return_url`/`cancel_url` gets a validation error. | Always pass both, HTTPS; the gate defaults to `example.com` placeholders so it can create the token. |
| 14 | **Order status is not authorization status.** Real Orders v2 statuses are `CREATED / SAVED / APPROVED / VOIDED / COMPLETED / PAYER_ACTION_REQUIRED`; `AUTHORIZED` is not one of them. The authorization object has `CREATED / CAPTURED / VOIDED / PENDING`. | Read `purchase_units[].payments.authorizations[].status`; do not gate on a fictional order status. The mock's `AUTHORIZED` is explicitly synthetic. |
| 15 | **A `payer-action` link means the spend is not server-side.** If PayPal needs a redirect during the order, the agent cannot pay unattended. | Treat `PAYER_ACTION_REQUIRED`/`payer-action` as Path C and fail the capability gate. The mock asserts no such link. |
| 16 | **`PayPal-Auth-Assertion` is a partner header, not a direct-merchant requirement.** A direct merchant does not need to send it just because it knows its merchant id. | Leave the assertion off by default; `PAYPAL_MERCHANT_ID` is used only for the payee sanity check. |
| 17 | **`PAYPAL_BASE` is a foot-gun.** Sandbox credentials plus the production base (or vice versa) produce confusing 401s. | Default to sandbox; the gate refuses production unless `--allow-production` and an explicit confirmation env var are both present. |
| 18 | **Sandbox test cards are not real cards, but card eligibility still bites.** PayPal's public test-number list is for sandbox only; the merchant must still have card capability. | Use PayPal's official card-testing list, never a real card; if B fails, record the issue and debug id. |
| 19 | **The mock's approval endpoint does not exist at PayPal.** | Only `mock_paypal.py` calls `/__mock/...`; `paypal_client.py` never does. Real approval is a browser redirect. |
| 20 | **A timeout is `UNKNOWN`, not failure.** Retrying a capture with a new key can double-charge. | Re-query the order/authorization with the same `PayPal-Request-Id`; converge from state. Do not invent a response. |
| 21 | **Webhooks are not a substitute for querying.** Signature verification proves the sender, not that the event is current or unique. | Verify signature, dedupe on event id, then re-fetch the order/authorization before writing the ledger. |
| 22 | **Card vault fallback is not the primary contract.** The v3 schema accepts a card source directly, but PayPal's card guide uses a setup token; both may be gated differently. | The live gate tries direct card vault, then the documented setup-token route; report which one worked. |
| 23 | **A vault is tied to the app/merchant.** A token from another app looks valid but fails with not-found/not-authorized. | State the app fingerprint; clear `spike/.state/live-setup.json` when switching apps. |
| 24 | **Sandbox reviewer accounts are not production accounts.** Sandbox eligibility can differ from live. | Do not claim live eligibility from a sandbox pass; re-run the capability gate with live credentials before launch. |
| 25 | **Captured sandbox money is not refunded by this gate.** | Use `PAYPAL_TEST_AMOUNT=1.00`; delete the sandbox token or refund manually if you care about the sandbox balance. |

---

## 2. Vault flow details (confirmed against the v3 schema/docs)

The vault API is v3. The spike uses:

```
POST /v3/vault/setup-tokens
GET  /v3/vault/setup-tokens/{id}
POST /v3/vault/payment-tokens
GET  /v3/vault/payment-tokens?customer_id={id}
GET  /v3/vault/payment-tokens/{id}
DELETE /v3/vault/payment-tokens/{id}
```

### PayPal wallet

1. Create a setup token with `payment_source.paypal` and
   `experience_context.return_url` + `cancel_url`.
2. Response: `status: PAYER_ACTION_REQUIRED`, `links[rel=approve]`, and a
   `customer.id`.
3. Payer approves in the browser. In sandbox, open the `approve` link in a
   private window signed in as the **Personal** sandbox account, review the
   consent, and approve. The redirect to `return_url` is cosmetic; the gate
   checks the setup token state, not the redirect.
4. `GET` the setup token; it should be `APPROVED`.
5. Convert it:

```json
{
  "payment_source": {
    "token": {
      "id": "4G4976650J0948357",
      "type": "SETUP_TOKEN"
    }
  }
}
```

6. Response is the payment token: `id` is the `vault_id`, plus
   `payment_source.paypal` (email, payer_id, etc.).
7. Spend from it:

```json
{
  "intent": "AUTHORIZE",
  "purchase_units": [{"amount": {"currency_code": "USD", "value": "1.00"}}],
  "payment_source": {
    "paypal": {
      "vault_id": "vwg...",
      "stored_credential": {
        "payment_initiator": "MERCHANT",
        "usage": "SUBSEQUENT",
        "usage_pattern": "UNSCHEDULED_PREPAID"
      }
    }
  }
}
```

The `stored_credential` fields are schema-confirmed for PayPal wallet:
`payment_initiator` is required, `usage` can be `FIRST/SUBSEQUENT/DERIVED`, and
`usage_pattern` accepts `UNSCHEDULED_PREPAID` among others. Whether PayPal's
risk engine wants `SUBSEQUENT` on the very first vaulted spend is a
**verify against docs/behavior** item.

### Card

The v3 payment-token request schema accepts a direct `payment_source.card`
(number, expiry, name, billing address, optional security code). PayPal's guide
prefers a card **setup token** first; with no verification the setup token can
come back `APPROVED` immediately, then the same `token -> SETUP_TOKEN` conversion
is used. The live gate tries direct first and falls back to the setup-token
route.

A card order uses `payment_source.card.vault_id` and:

```json
"stored_credential": {
  "payment_initiator": "MERCHANT",
  "payment_type": "UNSCHEDULED",
  "usage": "SUBSEQUENT"
}
```

`payment_type` is required for card stored-credential; `ONE_TIME` is only
compatible with `payment_initiator=CUSTOMER`, not MIT.

### Setup token statuses

`CREATED`, `PAYER_ACTION_REQUIRED`, `APPROVED`, `VAULTED`, `TOKENIZED`.
The mock uses `PAYER_ACTION_REQUIRED -> APPROVED -> VAULTED`.

### Payment token listing

`GET /v3/vault/payment-tokens` requires `customer_id`; it returns
`payment_tokens`, `total_items`, `total_pages`. The gate stores the `customer.id`
from the payment-token response.

---

## 3. Authorization, capture, void, and idempotency

### Honor window (PayPal docs)

- Authorization is valid for **29 days**.
- The **honor period** is the first **3 days**; captures in that window have the
  highest success probability.
- After the honor period, you can reauthorize within the 29-day window; a
  reauthorization creates a **new authorization id** and restarts the 3-day
  honor period. Capture against the new id, not the old one.
- Late reauthorization gets only the remaining days.
- Capture can be up to the authorized amount; PayPal's docs allow up to **115%
  or $75 USD more**, whichever is less, for some payment types. Do not rely on
  over-capture for Veto; authorize the exact cart.
- Partial captures are supported, but PayPal wallet partial capture must be
  enabled on the account.

### Void

- Void works only before capture. A successful void returns `204` and the
  authorization becomes `VOIDED`.
- After capture, void returns an error such as
  `AUTHORIZATION_ALREADY_CAPTURED`; refund instead.
- The gate creates a **separate** authorization for the void test. It does not
  try to void the captured authorization.

### Why `PayPal-Request-Id` is mandatory in this client

- PayPal idempotency is keyed by `PayPal-Request-Id`; without it, a retry can
  create another order, authorization, or capture.
- The header must be unique per API call type. A consent nonce is a good
  semantic source, but it can be too long/unsafe, so the client hashes it:
  `veto-<sha256(key)[:32]>` = 37 chars. Stable across retries.
- The client refuses any POST without an idempotency key before making a network
  call. The mock replays duplicate `(method, path, request-id)` responses and
  adds `Mock-Idempotent-Replay: true`.
- A live duplicate capture is checked after the real capture. If it does not
  replay the same capture id, the gate exits `1` even though money moved once:
  the retry safety is not proven.

**Verify against docs:** how long PayPal stores each idempotency key varies by
API; the 45-day figure appears in the refund example, not a universal rule.

---

## 4. Webhook signature verification (how it would be wired later)

The spike has no webhook receiver. When adding one:

1. Subscribe the HTTPS listener URL to the app's events. PayPal only delivers to
   port 443; use a tunnel for local dev.
2. Read the **raw body bytes** before JSON parsing. Do not parse and
   re-serialize for verification.
3. Read headers:
   - `PAYPAL-TRANSMISSION-ID`
   - `PAYPAL-TRANSMISSION-TIME`
   - `PAYPAL-TRANSMISSION-SIG`
   - `PAYPAL-CERT-URL`
   - `PAYPAL-AUTH-ALGO` (usually `SHA256withRSA`)
4. Compute CRC32 of the raw body; convert to **decimal**.
5. Build:
   `transmissionId|timeStamp|webhookId|crc32`
   where `webhookId` is configured when the listener is subscribed (not in the
   request).
6. Download the PEM certificate from `PAYPAL-CERT-URL`, cache it, and verify the
   base64 `PAYPAL-TRANSMISSION-SIG` over the string with SHA256withRSA.
7. Reject on failure before touching the ledger. Return 2xx only after
   accepting the event; PayPal retries non-2xx up to 25 times over 3 days.
8. Dedupe on the webhook event id (and/or a processed-transmission table).
9. Do not trust the webhook status alone: re-fetch the order/authorization via
   the API and converge. On timeout, this is the `UNKNOWN` state.
10. Alternative: post the raw event, transmission id/time, cert URL, auth algo,
    signature, and webhook id to
    `POST /v1/notifications/verify-webhook-signature`. It is simpler but adds a
    network dependency and does not work for webhook-simulator events.

Python stdlib has no RSA signature verifier. Options:
- use an optional `cryptography` dependency with a stdlib fallback that fails
  closed,
- shell out to `openssl dgst -sha256 -verify` (not ideal for a hot path),
- use only the postback endpoint until a proper verifier is added.

The client deliberately does not fake this. `mock_paypal` has no webhook
endpoint.

---

## 5. "Verify against docs" — items not proven here

These are the places where this spike made a documented choice but did not have
credentials to verify live. They are called out rather than silently guessed.

| Item | Current choice | What to verify |
|---|---|---|
| `stored_credential.usage_pattern` for a first MIT wallet spend | `UNSCHEDULED_PREPAID`, `usage=SUBSEQUENT` | Whether PayPal accepts/expects this on the first vaulted charge; try omitting it if 422 |
| Order status after authorize/capture | Gate reads authorization status, not order status | Real order status may be `COMPLETED` immediately after authorize; do not gate on `AUTHORIZED` |
| Card direct vault vs setup-token vault | Direct first, setup-token fallback | Which one your merchant's card capability permits; keep the passing route |
| Card setup token with no verification | No `verification_method` | Whether your merchant returns `APPROVED` or requires 3DS/SCA |
| Setup token `experience_context` extra fields | Only brand/locale/return/cancel | The guide's sample includes `payment_method_preference`; the vault schema does not, so it is omitted |
| Exact sandbox test card behavior | Public test numbers only | Which of PayPal's test cards are eligible for your sandbox merchant and vault flow |
| `PAYPAL_MERCHANT_ID` location | Optional env var | Dashboard wording/location varies; leave it unset if not obvious |
| `PayPal-Request-Id` storage duration | Stable hash, retry during the run | Per-endpoint duration; do not rely on 45 days universally |
| PayPal wallet eligibility | App Vault option enabled | Account-level "Save PayPal and Venmo payment methods" eligibility/approval for live |
| Production behavior | Refused by default | Do not test production with this spike; sandbox only |

---

## 6. Sandbox capability limits (the reason this gate exists)

- A public hackathon entrant reported their sandbox merchant could not take card
  payments at all. That is the failure mode this spike is designed to discover
  on day 1, not day 18.
- Card vaulting needs an approved **Advanced Credit and Debit Card Payments**
  capability and SAQ D PCI scope for raw card data. Sandbox business accounts
  can be missing it.
- PayPal wallet vaulting needs the app's **Vault** feature and, for live, an
  eligible merchant account. The vault guide's eligibility step is under
  **Account Settings → Payment Preferences → Save PayPal and Venmo payment
  methods**; PayPal reviews the profile.
- The app's **Accept payments** option must also be on. OAuth success does not
  imply permission to vault or capture.
- Sandbox behavior is not production behavior. A sandbox pass proves the
  protocol; it does not prove live eligibility.
- The merchant country matters: PayPal's vault guide lists supported countries
  for saving PayPal and cards (for example US, Canada, UK, much of the EU, Hong
  Kong, Singapore, Japan, Australia). Check your seller account country.

---

## 7. Mock limitations

`mock_paypal.py` is a protocol fake, not a sandbox clone:

- Synthetic IDs shaped like PayPal IDs (`5O1...`, `0AW...`, `3C6...`, `4G4...`,
  `vlt...`), not real checksums.
- Order status `AUTHORIZED` is synthetic; real Orders v2 has no such status.
- Setup-token payer approval is simulated by a direct method or the
  `/__mock/setup-tokens/{id}/approve` admin endpoint. No browser, login, SCA, or
  3DS.
- No risk checks, funding sources, fees, currencies, fraud rules, or
  eligibility. Fee math is approximate.
- Idempotency is scoped by `(method, path, request-id)`; real PayPal's behavior
  and retention vary by API.
- No webhooks, certificates, or signature verification.
- No authorization expiry/reauthorization enforcement; the 29-day/3-day window
  is documented, not simulated.
- The mock serves plain HTTP on loopback; the real API is HTTPS.
- Duplicate Request-Id replay is required behavior for the test, but real PayPal
  may return `200` with the latest status rather than exactly the original
  response.

---

## 8. Small operational notes

- `spike/.state/live-setup.json` is written with mode `0600`; it contains a
  client-id fingerprint, base URL, setup token id, approve URL, vault id, and
  customer id. Never add secrets to it.
- On a different `PAYPAL_CLIENT_ID` or `PAYPAL_BASE`, the state is ignored. That
  is deliberate: a vault from app A is not usable by app B.
- `PAYPAL_VAULT_ID` is validated with a read (`GET /v3/vault/payment-tokens/{id}`)
  before spending. If that read fails, the gate does not attempt the spend.
- Path B deletes its card payment token after a successful proof. Path A leaves
  the wallet token in place because it is the useful output of the gate.
- The gate's live amount is capped at `$10.00` by code. The default is `$1.00`.
- Sandbox money is fake, but captures still appear in the sandbox business
  account. Refund or reset manually if that matters for your demo.
- If the gate is rerun after a timeout, new order keys are generated. For
  production, persist the consent/nonce and request-id mapping before the first
  call; this spike does not implement that persistence.

---

## 9. Sources (accessed 2026-10-20)

- [Payment Method Tokens v3](https://developer.paypal.com/api/payment-tokens/v3/)
- [Save PayPal with the Payment Method Tokens API](https://developer.paypal.com/api/payment-tokens/save-without-purchase/paypal)
- [Save cards with the Payment Method Tokens API](https://developer.paypal.com/api/payment-tokens/save-without-purchase/cards)
- [Orders v2](https://developer.paypal.com/api/orders/v2/)
- [Payments v2](https://developer.paypal.com/api/payments/v2/)
- [Idempotency](https://developer.paypal.com/api/rest/reference/idempotency)
- [Authorization and honor period](https://developer.paypal.com/payment-methods/auth-honor)
- [Void an authorized payment](https://developer.paypal.com/checkout/void-authorized-payment)
- [Webhooks integration guide](https://developer.paypal.com/api/rest/webhooks/rest/)
- [Sandbox accounts](https://developer.paypal.com/sandbox-testing/accounts)
- [Card testing](https://developer.paypal.com/sandbox-testing/card-testing)
- [Get started with PayPal APIs](https://developer.paypal.com/api/rest/)
- [PayPal request headers / auth assertion](https://developer.paypal.com/api/rest/requests/)
