# Devpost upload sheet

Live demo: <https://veto-bracken-field.onrender.com> · Source: <https://github.com/runake25/veto>

Everything below is PNG, exactly 3:2 (2880×1920 or 3000×2000) and 90–390 KB — well under the
5 MB cap. Upload in this order.

## Thumbnail (Project Overview → Thumbnail image)

| File | What it is |
|---|---|
| [media/00-thumbnail-cover.png](media/00-thumbnail-cover.png) | Brand cover: veto mark + "Consent, not checkout." |

## Project media (Image Gallery) — 7 images

| # | File | What it shows |
|---|---|---|
| 1 | [media/01-overview.png](media/01-overview.png) | The decision first: one waiting cart, the month held / settled / left, the agent fleet |
| 2 | [media/02-record-awaiting.png](media/02-record-awaiting.png) | A record waiting for a signature: every rule with observed vs limit, the cart, the consent; no PayPal object yet |
| 3 | [media/03-record-paypal.png](media/03-record-paypal.png) | A captured record: PayPal order → authorization → capture ids, the consent hash and window, the append-only trail |
| 4 | [media/04-record-blocked.png](media/04-record-blocked.png) | A refusal: the rule that stopped it, "no PayPal object exists", no consent issued |
| 5 | [media/07-agent-jobs.png](media/07-agent-jobs.png) | An agent instance: charter figures and two standing jobs with monthly envelopes |
| 6 | [media/08-dry-run.png](media/08-dry-run.png) | The streamed pipeline (extract → Channel3 → price → decision) and the scored catalogue around the pick |
| 7 | [media/10-integrations.png](media/10-integrations.png) | The PayPal sandbox wallet connected, agent API keys, six Slack commands, signature verified |

If the form has no separate thumbnail field, upload `00` first and then the seven above.

## Not in this set

To keep the gallery to the eight images that carry the story, the inbox, the agents list, the
analytics screen, the mobile composite and the contact sheet were removed. They are still in git
history (commit `726bef1`) if you want one back, e.g.:

```bash
git show 726bef1:submission/media/09-analytics.png > 09-analytics.png
```

Do not upload this file.
