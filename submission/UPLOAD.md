# Devpost upload sheet

Live demo: <https://veto-bracken-field.onrender.com> · Source: <https://github.com/runake25/veto>

Twelve files. Each one is a designed slide — the screenshot with its caption baked in, because
Devpost's gallery has no caption field, so the story has to live on the image. All PNG, exactly
3:2 (3000×2000), 90–483 KB, well under the 5 MB cap. Upload in this order.

## Thumbnail (Project Overview → Thumbnail image)

`media/00-thumbnail-cover.png` — the brand cover: **"Consent, not checkout."**

## Project media (Image Gallery) — 11 slides

The eleven slides read as one request: the question → how a cart is built → the decision →
the money → the refusal → the ledger → the agents → the jobs → the audit → the rails → the phone.

| # | File | Eyebrow | Caption |
|---|---|---|---|
| 1 | [media/01-overview.png](media/01-overview.png) | 01 · THE CONSOLE | **What needs a signature, and what is already spent.** One cart waiting, the month's held and settled money, and the agents that can spend. |
| 2 | [media/02-pipeline.png](media/02-pipeline.png) | 02 · THE PIPELINE | **A sentence becomes a judged cart.** DeepSeek reads what to buy, Channel3 prices real offers, and code decides. |
| 3 | [media/03-record-awaiting.png](media/03-record-awaiting.png) | 03 · THE DECISION | **Over the line, the cart stops.** Every rule stores its observed value and limit; the consent binds the approval to this exact cart. |
| 4 | [media/04-record-paypal.png](media/04-record-paypal.png) | 04 · THE MONEY | **The signature is what moves money.** PayPal order, authorization, capture — the consent nonce is the idempotency key. |
| 5 | [media/05-record-blocked.png](media/05-record-blocked.png) | 05 · THE REFUSAL | **A refusal is a receipt too.** The rule and its numbers, no consent issued, and zero PayPal objects. |
| 6 | [media/06-inbox.png](media/06-inbox.png) | 06 · THE LEDGER | **Every request, every state.** Needs you, settled, refused, withdrawn — one board for every door the request came through. |
| 7 | [media/07-agents.png](media/07-agents.png) | 07 · THE AGENTS | **Authority belongs to an agent.** Each agent owns a budget slice and a rule set — not a prompt. |
| 8 | [media/08-agent-jobs.png](media/08-agent-jobs.png) | 08 · THE JOBS | **Agents keep working when nobody is watching.** A cadence, a monthly envelope, and a pause that is a money rule. |
| 9 | [media/09-analytics.png](media/09-analytics.png) | 09 · THE AUDIT | **Captured is the only spend counted.** Held, stopped and refused money sits beside it — by agent, merchant, category and rule. |
| 10 | [media/10-integrations.png](media/10-integrations.png) | 10 · THE RAILS | **The rail out, and every door in.** One PayPal sandbox wallet, agent API keys, six signed Slack commands. |
| 11 | [media/11-mobile.png](media/11-mobile.png) | 11 · THE PHONE | **The same board, one column.** At 390px rows fold, the sidebar becomes a nav, and nothing scrolls sideways. |

If the form has no separate thumbnail field, upload `00` first and then the eleven above. If it
has per-image caption fields, use the bold sentence + the sentence after it.

## Not for upload

- [storyboard.png](storyboard.png) — a one-glance preview of the whole set.
- This file.
- The raw (uncaptioned) screenshots are still in git: `git show 726bef1:submission/media/05-inbox.png`.
