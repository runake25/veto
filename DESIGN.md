# Veto: design system

The console is an **app screen**: someone works in it, fast, a few times a day. It
follows the app-screen rules, not landing-page rules: no hero, no icon cards, no
gradients, no colour in the chrome.

## The direction: framed sheet

One tinted desk holds the sidebar and the 48px top bar with no rule between them;
the work sits on a white page inset 8px from the window's right and bottom edges,
one 1px edge, 16px corners, no shadow. Depth is a single tone step.

Why: Veto's artefact is a signed page, not a dashboard. The frame is the desk, the
sheet is the ledger, and the consent seal is the stamp on it. It also gives the
screen an identity that survives a 100%-zoom screenshot, which a flat white
dashboard does not.

**The face.** The frame is tinted from the product's world (a low-chroma stone,
`#f3f2f0`), not from a category colour; the sheet is a very slightly cool white
(`#fafafb`); and every identifier is set in IBM Plex Mono, which is how the product
itself refers to a request, a cart hash or a PayPal object. IBM Plex Sans carries
everything else.

**Signature element.** The charter track: money already spent, the paler segment of
money an agent has committed and nobody has signed for, and an overrun in the
refusal colour past the end of the track. It is the whole product in one mark, and
it appears on both the ledger and the request.

## Tokens

```
ground #f3f2f0   sheet #fafafb   surface #ffffff   raised #f2f1ef   active #e8e6e3
border #e4e2df   rule #edebe8    control #cfcdc9   sheet-edge #dfddd8
text #17191b     muted #5d6165   faint #6a6e72
accent #1b6c80   accent-soft #d9e9ed   accent-ink #104453
negative #c4242a negative-soft #fbeae9 negative-ink #7d1618   positive #1e7b4f
radius: card 8, control 6, sheet 16   gap 24   card padding 16-20   row 48
```

Colour budget: under 3%. The accent carries data and the seal, never the chrome.
The only filled primary is near-black. Red is a refusal signal, one mark per object.

## Type

IBM Plex Sans 400/500/600, IBM Plex Mono 400 for identifiers and keys only.
The scale: 24 title (600) · 26 lead figure (500) · 19 secondary figure (500) ·
16 card title (600) · 13 body (400, 1.5) · 11.5 labels and meta (500). Sentence
case throughout, no tracked capitals, tabular figures on every number. A card
title is always heavier than the body text under it, and a figure is always
larger than its label.

## Screens

Six, all reachable, none decorative: the ledger, the agent list, one agent (its
guardrails as one band, then a dry run and a product search scoped by those
guardrails), the catalogue searched as an agent, analytics by tag and agent, the
charters.

## Components

- **KPI row**: one panel, four columns split by hairlines, label over value. The
  lead cell holds the money awaiting consent at 30px; the rest sit at 24px.
- **Consent queue**: two-line rows, id in mono, the rule that fired in words, amount
  right-aligned, `Approve` and `Veto` on the row. Rows are divided by a 1px rule and
  take a raised fill on hover.
- **Charter track**: label, in-flight figure, spent-of-cap, then a 7px track. A
  legend of the marks themselves sits in the card foot.
- **Table**: 12px sentence-case grey headers, no band, 48px rows, right-aligned
  tabular money, one status mark per row (a pale pill only where a human is needed
  or money was refused).
- **Authority rail**: the record's spine. Every step from the Slack message to the
  capture, each with its object id; a veto stops the line with a square red end-cap
  and the steps after it stay hollow, marked "no capture".
- **Consent well**: tinted, with the cart hash in mono, or one sentence when nothing
  is signed yet.
- **Palette**: 640px, 8px radius, real shadow (it floats), grouped results, `↵` on
  every row, a footer naming the keys.
- **Guardrail band**: the agent screen's charter in one card. A lead monthly figure at
  26px with the charter track under it, three secondary figures (per order, per item,
  signature above) at 19px across a hairline row, then the scope as definition rows
  (categories, merchants, exceptions) in 11.5px labels and 12.5px values. It replaces
  the read-only rule table: every rule appears exactly once, in words, at the size its
  importance deserves.
- **Rule switch**: 34x20, the on state in the one hue, the rule's limit written under
  it in 12px grey. Switching it changes the catalogue count on the same screen, so the
  control has a visible consequence.
- **Tag chip**: 19px, hairline border, neutral fill. Tags are identity, not status, so
  they carry no hue.
- **Bar list**: a 6px accent bar on a raised track, the value right-aligned, the count
  and any refused money in 11px grey under it. The accent is the accent because this is
  the screen's main series.

## States

Hover, focus-visible (2px accent, 2px offset), selected (palette row on `active`),
disabled, empty (dashed frame with one sentence and one way out), and the record's
stopped rail. The phone drops the sidebar to one row of icons, the table to
two-line rows, and the KPI row to two columns; nothing scrolls sideways.

## Rules this build enforces

One left edge and one right edge on every block. Cards in a row end on the same
line. No shadow at rest. No gradient. No icon in a tinted box. No uppercase tracked
headers. No zebra. No left-aligned numbers. Numbers reconcile: the three KPI
figures sum to the table total, and the cart total is the sum of its lines.

## Motion

The world is paper and ink, so the handfeel is 咔嗒 (a plastic switch) and 砸落 (a stamp
landing) — not float, not bounce. Motion serves three jobs, in this order:

1. **The press answers.** Every control sinks 1px and returns (90ms), buttons lift 1px on
   hover, switches squeeze under the thumb. Nothing is inert.
2. **A change shows where it came from.** A view change lays the new page down (6px rise
   + fade, 180ms). An approved row leaves toward the button that removed it (150ms) and
   the rows that stay are FLIPped to their new positions (240ms). Figures are tweened
   from their last rendered value (320ms, ease-out) instead of swapping, and charter
   tracks grow to their new fill.
3. **The page arrives once.** The sheet fades up 4px on first load only (200ms). No
   re-render replays it.

**The authored moment.** When a human approves, the consent lands as a stamp: the consent
well scales down from 1.035 to rest in 280ms, the rail fills in behind it, the money moves
from in flight to spent, and the receipt strip keeps the decision in place. That is the
product's own object appearing, not decoration.

Decided requests stay in the card as a receipt strip, so the queue card keeps its height
as it empties instead of opening a hole.

All of it is transform/opacity or transform/width only, everything is interruptible (CSS
transitions restart from the current value), and `prefers-reduced-motion: reduce` sets every
duration to 0.01ms and drops the translates, with the content unchanged.
