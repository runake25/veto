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

**The face.** Three voices, one system. **IBM Plex Serif 400/500/600** is the document
voice — page titles, the agent name on a plate, a record title, a Settings section title;
it never sets a control, a row, a figure or a button. **IBM Plex Sans 400/500/600** is the
interface — rows, controls, labels, body, and money (always tabular). **IBM Plex Mono 400**
is the machine voice — every identifier, hash, serial, channel, id and timestamp, because
that is how the product refers to a request, a cart hash or a PayPal object. The utilities
`.display` (serif 600) and `.display-500` exist for titles only.

**Signature elements.**

- **The status mark** (`components/state.tsx`) — a 6px dot and one word, four tones: green
  captured / active, amber held, red refused / can't buy, grey withdrawn / paused. One
  component renders request status, agent state, job state, run outcome, integration state
  and payment state, so a colour cannot mean two things.
- **The verdict band** on a record — a 3px state rail, `POLICY · outcome · status` in
  mono, one sentence of conclusion, the rule reasons with their observed/limit figures, and
  only the actions the state allows.
- **The instance rail** — under the Agents tab, a collapsible list of the running agents:
  a 24px agent mark, the name, its state ring, and the count in the disclosure head. It
  opens by itself inside `/agents*`, stays collapsed elsewhere, and remembers what a person
  chose. It navigates to instances; it is not a second roster.
- **The agent mark** (`components/AgentAvatar.tsx`) — a running thing needs an identity,
  not a photograph: a dark navy tile, a two-letter mono monogram, a 4×4 dot grid hashed
  from the serial, and a 7px four-tone state ring. 24px in the rail, 44px on a plate, 56px
  on the instance page.

## Tokens

```
ground #e9ebee   surface #ffffff   wash #f7f8fa   raised #eef0f3   active #e2e6ea
border #d8dde2   rule #e7eaed      control #b6bec6   sheet-edge #ccd3da
text #0e141b     muted #4e5761     faint #68717c
accent #1b3f63   accent-soft #e2e9f1   accent-ink #12304d
positive #0f6b43 (money captured)  negative #a01f24 (refused)  hold #8a5a09 (money held)
rail #0f1822  rail-ink #b3bdca  rail-active #1d2c3b  rail-line #233140  rail-mark #8fb4d6
radius: card 6, control 4, sheet 12   sheet inset 8   sidebar 228   topbar 48
gap 20-24   card padding 15-16   row 48
```

Colour budget: under 3%. Navy carries links, focus and the one chart series; navy is never a
surface. Green means money moved, red means money was refused, amber means money is held
for a signature — those three never appear as decoration. The only filled primary button is
near-black. No gradients.

## Type

Shipped scale: 32 page title (serif 600, 1.12–1.15, tracking -0.015em) · 32 record title
(serif) · 21 agent plate name (serif) · 19 Settings section title (serif) · 15 card title
(sans 600) · 14.5 primary row content (sans 600) · 13.5 page subtitle (sans 400, muted,
max 58ch) · 12.5–13 secondary, meta and table figures (sans 400) · 11 labels and meta
(sans 500) · 10–11 machine keys. Money is sans 600 and tabular, never serif; identifiers
are always mono. Only machine labels take tracked capitals (table headers, endpoint
labels, the plate's charter keys, the `POLICY · …` kicker); sentence case everywhere else.
Figures are always larger than their labels; money is right-aligned; a card title is always
heavier than the body under it. There is exactly one `h1` per page, and it is the serif
voice.

## Screens (seven, one store)

- **Overview** — the decision first, the money second. Head: one short line, freshness and
  exactly one primary button (`Review N requests`, or `Open inbox` when nothing waits).
  Then the **Needs you** lead panel: one row per request (agent mark, title, agent and
  request id, amount, age, Open), the lapsed-consent line quiet at the bottom, a one-line
  empty state. Then **This month** as one quiet strip — held for signature · settled · left,
  with a thin budget meter and the Analytics link. Then compact agent rows (mark, name,
  caps in one line, manage →). The old four-card stat row is gone.
- **Inbox** — the only request list. Five state groups in importance order: **Needs you ·
  All · Settled · Refused · Withdrawn**; a lapsed consent is a marked subset of Needs you
  (`Consent expired`), never its own tab. Search, agent, day range and order sit below the
  tabs. Columns: Request · Amount · Agent · Source · Status · Created; the headers sort and
  `aria-sort` sits only on the active column; a phone gets the same rows as a list. The
  footer reconciles (`1–14 of 14` plus the group's summed money).
- **Record** — one request as a document, in this order: identity (mono kicker, serif
  title, agent, amount, status) → the verdict band with its reasons and actions → cart →
  PayPal objects → the consent seal (hash, window, use) → the append-only trail.
  Approve-and-capture and Veto only while awaiting; Void only while pending; a lapsed
  consent locks approve, says why, and offers "send it again".
- **Agents** — one instance plate per agent, not a shared table: a 44px mark with its state
  ring, a serif name, `agt_… · #channel · tag`, and the state word; the four charter
  figures under a wash band with the monthly figure in the lead; then **JOB** (name ·
  cadence · next run) and **SCOPE** (categories · merchants · exceptions) under a rule. A
  charter that cannot buy says `Can't buy` in the state slot. The plate is the link; the
  phone folds to one column.
- **Analytics** — one range (this month / last month / last 90 days), one lead figure
  (Settled) over a quiet row of held, stopped and requests; settled per day as navy bars
  with a `Daily numbers` table; then by agent, what the rules caught, by vendor and by
  category. Only captured lines count as spend; agent rows link into the filtered Inbox.
- **Integrations** — two labelled groups. **MONEY** is the one card that moves money: the
  PayPal wallet's state, mode, vault, payer, connected fact, and the connect CTA when it is
  disconnected. **ACCESS** is agent API keys first (mint, revoke, one-line curl) and Slack
  second (commands in two columns, Request URL, Interactivity URL, signature state); the one
  routing fact is a footnote, not a card.
- **Settings** — the seat, in four cards that each save themselves: **Account** (display
  name writable; email and role read-only; sign out), **Workspace** (name editable for an
  admin, read-only with the reason for anyone else; workspace id and currency), **Security**
  (current / new / confirm password, inline validation, success flash) and **Environment**
  (read-only: PayPal state, sandbox rails, agent count, runner URL). No save-all button.

## Components

- **Status mark (`.state`)** — dot + word, 11.5px (12px in tables, 13px as `.is-lead`);
  tones pos/wait/neg/quiet. No pills, no coloured backgrounds; status is a fact, not a
  badge.
- **Verdict band** — grid: content left, actions/outcome right. A 3px rail in the state
  colour, a tracked mono kicker, a 15.5px conclusion, rule flags with observed/limit and the
  unblock sentence, then exactly the actions the state allows.
- **Record rows** — definition rows 1px apart: 11.5px label left, 12px value right, ids and
  hashes in mono with ellipsis and the full value in `title`; a copy control where the value
  is meant to leave the product.
- **Trail** — numbered steps: seq in 10px mono, label + detail, time right in 10px mono.
- **Cart table** — 10px tracked header, 12.5px item titles, vendor column, right-aligned
  qty/unit/amount, total separated by a stronger rule; an empty cart says no cart was priced.
- **Filter tabs / range tabs** — a 3px-padded raised track with 27px tiles; the active tile
  is a white surface with a hairline. No animated slider.
- **Table** — a real `<table>`: sticky 10px tracked headers on a wash band, sort buttons in
  the header cells, `aria-sort` only on the active column, 1px row rules, right-aligned
  tabular money, one status mark per row, the whole row is the target, hover is a wash
  tone. The phone gets a list of the same rows.
- **Needs-you panel** — the one tinted surface on Overview: panel title + inbox link,
  decision rows (mark, title, agent and request id, amount, age, Open), one quiet
  lapsed-consent line at the bottom, a one-line empty state.
- **Money lead / month strip** — Analytics leads with one 42px settled figure over a
  three-fact hairline row; Overview's month strip is the quiet version of the same facts
  (held / settled / left) with a thin budget meter.
- **Chart** — navy bars on a 150px plot, no gridlines, three date ticks, hover darkens the
  bar; a value label only where the bar has room; a `Daily numbers` disclosure carries every
  day for keyboard and screen-reader readers.
- **Guardrail track** — on the agent page: the month's captured money fills the track, the
  unsigned money sits beside it, and the legend names spent / unsigned / left against the
  monthly budget. It reads the same rows every other screen reads.
- **Dry run** — the agent page tests a cart through the real pipeline (multi-item carts
  resolve concurrently; the meta line says "N items in parallel" and the total time); the
  result ends at **Send** (a real request, logged in the Inbox), with the consequence stated
  for its state: capture, signature, or blocked.
- **Standing job** — on the agent page: the instruction, cadence, next run, the month's
  envelope track, the reorder window, the recorded runs with outcome/amount/record link, a
  streamed **Run now** behind a confirm, pause/resume, and an inline editor. A run is a real
  request and says so.
- **Agent plate** — 44px mark, serif name, machine serial line, state, charter band, JOB +
  SCOPE footer; plate-wide hover tone, arrow steps right, 2×2 readout when the plate itself
  is narrow.
- **Buttons** — primary near-black (38px), secondary hairline, danger outline red, quiet
  text, small 28px for row actions. Every `.btn` has hover, focus-visible, disabled and
  press feedback (0.5px sink). Copy controls are mono chips on a 24px target, 19px inside
  the record kicker.
- **Empty state** — one title, one line; never an illustration.
- **Settings card** — a serif 19px section title with one quiet note, a two-column field
  grid (`.setting-field`, `.is-wide` spans both), 11px tracked labels, a hint or a field
  error under the control, and one action row per card with a `Saved.` flash. Each card
  writes its own endpoint.
- **Top chrome (≤900px)** — the sidebar collapses to a compact light nav in the top bar,
  and an account menu (identity, workspace, role, wallet state, settings, sign out) replaces
  the hidden rail footer; below 560px the nav takes its own row under the brand and account
  button. The phone folds rows to two lines.

**Copy rule.** A page subtitle is ≤ 12 words, one clause; it says what the page is for, not
what the product is. No product philosophy in the UI. Card ledes stay only when they add a
fact. Empty states are one title and one line.

## States

Hover (tone step, no lift), focus-visible (2px navy on the sheet; 2px rail-mark on the dark
rail, 8.2:1), pressed (every `.btn` sinks 0.5px), disabled, loading skeletons that match the
final block, empty per filter, no wallet (428 wallet_required with its connect link), expired
consent, blocked record (no payment, no consent, reasons shown), revoked key, the read-only
role state on a record and on the agent page, and a boot failure with Retry. Browser
surfaces are themed: selection is navy on white, the caret is accent, scrollbars are thin
and tokenised. Nothing scrolls sideways at 390px.

## Rules this build enforces

One left edge and one right edge on every block. No shadow at rest. No gradient. No icon in
a tinted box. No zebra striping. No left-aligned numbers. A number is never duplicated: the
Overview count and the Inbox total are the same query, Analytics reconciles with both, and
the record's total is the sum of its lines. Colour meaning is fixed: green = captured,
amber = held, red = refused, navy = data and links. Copy is plain and honest — no milestone
talk, no invented history, and a figure only appears when the store can stand behind it.

## Motion

Paper and ink: a switch click and a stamp landing, never float or bounce.

1. **The press answers.** Controls sink 0.5–1px and return; 140–160ms on background/colour.
2. **A screen arrives once.** Blocks stagger 60ms and rise 10px in 450ms
   `cubic-bezier(.16,1,.3,1)`; the sheet itself fades up 12px on first paint.
3. **A change stays in place.** Approving re-renders the verdict band and the payment card
   with the new state; nothing slides off the screen. The record's own objects are the
   receipt.

`prefers-reduced-motion: reduce` replaces every animation with the end state (opacity only).
All motion is transform/opacity; layout is never animated.
