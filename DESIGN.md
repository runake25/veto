# Veto: design system

The console is an **app screen**: someone works in it, fast, a few times a day. It follows
app-screen rules, not landing-page rules — no hero, no icon cards, no gradients, no colour
in the chrome. One left edge and one right edge on every block.

## The direction: framed sheet, ledger tone

A cool graphite desk holds a dark navy rail and a 48px top bar; the work sits on a white
sheet inset 8px from the window's right and bottom edges, one 1px edge, 12px corners, no
shadow. Depth is a single tone step.

Why: Veto's artefact is a signed page, not a dashboard. The frame is the desk, the sheet is
the record, and the consent is the stamp on it. The dark rail makes the chrome read as an
instrument the user operates; the cool neutral + navy palette says institutional finance,
not a friendly spending tracker. The whole frame fits in one 100%-zoom screenshot, which a
flat white dashboard does not.

**The face.** IBM Plex Sans 400/500/600; IBM Plex Mono 400 for every identifier, hash, id
and timestamp — it is how the product refers to a request, a cart hash or a PayPal object.
Money is tabular everywhere.

**Signature elements.** The **status mark** (a 6px dot and one word — green captured, amber
held, red refused, grey withdrawn) and the **verdict band** on a record (a 3px state rail,
`POLICY · outcome · status` in mono, one sentence of conclusion, the rule reasons with their
observed/limit figures, and only the actions that state allows).

## Tokens

```
ground #e9ebee   surface #ffffff   wash #f7f8fa   raised #eef0f3   active #e2e6ea
border #d8dde2   rule #e7eaed      control #b6bec6   sheet-edge #ccd3da
text #0e141b     muted #4e5761     faint #68717c
accent #1b3f63   accent-soft #e2e9f1   accent-ink #12304d
positive #0f6b43 (money captured)  negative #a01f24 (refused)  hold #8a5a09 (money held)
rail #0f1822  rail-ink #a9b4c0  rail-active #1d2c3b  rail-line #233140  rail-mark #8fb4d6
radius: card 6, control 4, sheet 12   sheet inset 8   sidebar 228   topbar 48
gap 20-24   card padding 15-16   row 48
```

Colour budget: under 3%. Navy carries links, focus and the one chart series; navy is never a
surface. Green means money moved, red means money was refused, amber means money is held
for a signature — those three never appear as decoration. The only filled primary button is
near-black. No gradients.

## Type

Scale: 30 record total · 27 record title · 24 page title (600) and plate lead (500) · 19
card/plate title (600) and secondary figure (500) · 18 plate readout · 16 card title (600) ·
15.5 verdict headline (500) · 13.5 table figure · 13 body (400, 1.5) · 12.5 table title ·
11.5 labels and meta (500) · 10.5 machine keys · 10 tracked capitals. The only tracked
capitals are machine labels (table headers, endpoint labels, the `POLICY · …` kicker and the
agent plate readout keys). Sentence case everywhere else. Figures are always larger than
their labels; money is right-aligned and tabular; a card title is always heavier than the
body under it.

## Screens (six, one store)

- **Overview** — KPI row (needs you, committed-unsigned, settled this month, agents) + the
  pending requests as links + the agent roster. No charts and no aggregates: those have a
  owner (Analytics).
- **Inbox** — the only request list. Status tabs and agent filter live in the URL; the six
  columns are request, agent, source, amount, status, created. Pending first, then newest.
  An amount is `—` when nothing was priced (a blocked request may have no cart at all).
- **Record** — one request as a document: identity + amount + status, the verdict band with
  its reasons and actions, the cart, the PayPal objects, the consent (hash, window, use) and
  the append-only trail. Approve-and-capture and Veto only while awaiting; Void only while
  pending; an expired consent disables approve and says why instead of failing silently.
- **Agents** — one plate per instance, not a shared table: serial + state, name + tag,
  monthly lead at 24 over three 18px caps on one baseline, scope footer. A charter that
  cannot buy says `can't buy` in the state slot.
- **Analytics** — one range (this month / last month / last 90 days), four totals, settled
  per day as thin bars, then by agent, by vendor, by category and what the rules caught.
  Only captured lines count as spend; agent rows link into the filtered Inbox.
- **Integrations** — the three doors: Slack (endpoints + channel routing), the PayPal wallet
  (mode, vault, connection), and agent API keys (prefix, last use, mint once, revoke
  immediately).

## Components

- **Status mark** — dot + word, 11.5px; tones pos/wait/neg/quiet. No pills, no coloured
  backgrounds; status is a fact, not a badge.
- **Verdict band** — grid: content left, actions/outcome right. A 3px rail in the state
  colour, a tracked mono kicker, a 15.5px conclusion, rule flags with observed/limit and the
  unblock sentence, then exactly the actions the state allows.
- **Record rows** — definition rows 1px apart: 11.5px label left, 12px value right, ids and
  hashes in mono with ellipsis and the full value in `title`.
- **Trail** — numbered steps: seq in 10px mono, label + detail, time right in 10px mono.
- **Cart table** — 10px tracked header, 12.5px item titles, vendor column, right-aligned
  qty/unit/amount, total separated by a stronger rule; an empty cart says no cart was priced.
- **Filter tabs / range tabs** — a 3px-padded raised track with 27px tiles; the active tile is
  a white surface with a hairline. No animated slider.
- **Table** — 10px tracked headers on a wash band, 1px row rules, right-aligned tabular
  money, one status mark per row, whole row is the link, hover is a wash tone.
- **Stats** — one panel, four cells split by hairlines, label over value over note; semantic
  tone only when the figure is non-zero.
- **Chart** — navy bars on a 118px plot, no gridlines, three date ticks, hover darkens the
  bar; zeros are a 2px stub so the day still reads.
- **Agent plate** — machine label row, name + tag, readout on one baseline, scope footer;
  plate-wide hover tone, arrow steps right, 2×2 readout when the plate itself is narrow.
- **Buttons** — primary near-black (38px), secondary hairline, danger outline red (veto),
  quiet text, small 28px for row actions. Copy buttons are 20px mono chips.
- **Empty state** — dashed circle, one title, one sentence; never an illustration.
- **Top nav (≤900px)** — the sidebar collapses to a compact light nav in the top bar, so the
  sections stay reachable; the phone folds rows to two lines and stats to 2×2.

## States

Hover (tone step, no lift), focus-visible (2px navy, 2px offset), disabled, loading
skeletons that match the final block, empty per filter, no wallet (428 wallet_required with
its connect link), expired consent, blocked record (no payment, no consent, reasons shown),
revoked key, and the read-only role state on a record. Nothing scrolls sideways at 390px.

## Rules this build enforces

One left edge and one right edge on every block. No shadow at rest. No gradient. No icon in
a tinted box. No zebra striping. No left-aligned numbers. A number is never duplicated: the
Overview count and the Inbox total are the same query, Analytics reconciles with both, and
the record's total is the sum of its lines. Colour meaning is fixed: green = captured,
amber = held, red = refused, navy = data and links. Copy is plain and honest — no milestone
talk, no invented history, and a figure only appears when the store can stand behind it.

## Motion

Paper and ink: a switch click and a stamp landing, never float or bounce.

1. **The press answers.** Controls sink 1px and return; 140–160ms on background/colour.
2. **A screen arrives once.** Blocks stagger 60ms and rise 10px in 450ms
   `cubic-bezier(.16,1,.3,1)`; the sheet itself fades up 12px on first paint.
3. **A change stays in place.** Approving re-renders the verdict band and the payment card
   with the new state; nothing slides off the screen. The record's own objects are the
   receipt.

`prefers-reduced-motion: reduce` replaces every animation with the end state (opacity only).
All motion is transform/opacity; layout is never animated.
