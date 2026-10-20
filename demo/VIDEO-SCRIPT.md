# Demo video: words to say over `demo/veto-demo.mp4`

33 seconds, silent, 1440×900. Record a voice-over over it in QuickTime, or read this
straight to camera while it plays. Timings are the captions in the file.

**0:00 – 0:03 · the ledger**
> This is Veto. An agent wants to buy things for the team, and every purchase it proposes
> lands here. The charter decides first; a human decides when the charter says so.

**0:03 – 0:07 · approve**
> Approving isn't a click — it's a signature. The row leaves, a single-use consent lands,
> and the money moves from "in flight" to "spent".

**0:07 – 0:12 · the record**
> Open one and it argues its own case: the cart, the agent that raised it, the exact rule
> that stopped it — eighteen dollars a unit against a fifteen dollar cap — and the chain
> from the Slack message to the PayPal capture.

**0:12 – 0:16 · signing**
> Sign it and the consent is a hash of this exact cart, single use, expiring in ten
> minutes. Order, authorise, capture. Nothing moved before the signature.

**0:16 – 0:21 · agents hold the rules**
> Here's what makes it a product rather than a policy file: agents hold the rules. Seven
> exist; each agent only applies the ones it was given. Switch one on and the consequence
> is immediate.

**0:21 – 0:25 · the search is the rule set**
> So the catalogue search *is* the rule set. Thirteen of fourteen items pass this agent.
> Turn the unit cap on and it drops to four. Nothing is hidden — the rule is named.

**0:25 – 0:29 · analytics**
> Every request carries its tag into analytics, so money that moved and money that was
> refused can be read apart, by tag and by agent.

**0:29 – 0:33 · closing**
> Slack is the only way in. The rules are the agent's. PayPal only pays after a human
> signs. That's Veto.

## What to say about what is real

- Extraction: **live DeepSeek**.
- Catalogue and buy links: **live Channel3** — real products, real merchants.
- Rules: **deterministic, seven of them, per agent**.
- Consent: **real sha256 of the cart**, single use, 10-minute expiry.
- PayPal: **sandbox** — order creation and authorisation verified; capture happens once the
  company wallet is connected (`/connect`).
- Slack: endpoint, signatures and the Approve/Veto callback are built; the app registration
  is the remaining setup step.
