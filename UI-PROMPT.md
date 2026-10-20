# Veto — UI build brief (converted prompt)

> Converted from the API-billing console brief. Same deliverable contract (English UI, 1440×900,
> three states, offline `index.html`, three DPR-2 screenshots). Different product, and a specific
> art direction so the result isn't a template.

---

## Role

You are building the operator console for **Veto**, a spend-authority layer that sits between an AI
agent and PayPal. An agent proposes company purchases; Veto checks them against a **charter** (a
scoped spending agent with caps, an allowed-vendor list and an approver); money only moves after a
named human consents. Every decision — allowed or refused — leaves a receipt.

Build an interactive desktop admin console for the people who approve and audit that spend. English
interface. Desktop viewport **1440×900**. Deliver a single **offline-openable `index.html`**: no CDN,
no external fonts, no network at runtime, all CSS/JS/data inline.

---

## The art direction (do not substitute your own)

**Framed sheet.** The chrome is one tinted desk holding the sidebar and the 48px top bar with no rule
between them; the work sits on a white page inset 8px from the window's right and bottom edges, one 1px
edge, 16px corners, no shadow. Depth is a single tone step. Veto's artefact is a signed page, not a
dashboard: the frame is the desk, the sheet is the ledger, the consent seal is the stamp on it.

**Type — two faces, each with one job.**
- UI/body: one grotesk with engineering character (**IBM Plex Sans**, OFL, embedded as base64
  `woff2` so the file stays offline; fall back to `ui-sans-serif`). Not Inter, not Geist, not the
  system default.
- Identifiers only: a monospace (**IBM Plex Mono**) for request IDs, order and capture IDs, consent
  hashes, keyboard hints. Never for prose.
- Money uses the grotesk with `font-variant-numeric: tabular-nums`, right-aligned, 2 decimals,
  thousands separators, `$` with no space.
- Scale: 12 / 14 / 16 / 24, plus one lead figure at 30. Page title 24px at 600. Body 14px, line-height 20px.

**Colour tokens** (light, the only theme):
```
--ground #f3f2f0    /* the desk: a low-chroma stone, not a cream */
--wash #fafafb      /* the sheet's interior */
--surface #ffffff   --raised #f2f1ef   --active #e8e6e3
--border #e4e2df    --rule #edebe8     --control #cfcdc9   --sheet-edge #dfddd8
--text #17191b      --muted #5d6165    --faint #6a6e72
--primary #17191b   /* the one filled button, near-black, white label */
--accent #1b6c80    /* the one hue: data and the seal, never the chrome */
--accent-soft #d9e9ed  --accent-ink #104453
--negative #c4242a  --negative-soft #fbeae9  --negative-ink #7d1618  --positive #1e7b4f
--gap 24px  --card-pad 16-20px  --pad 20px  --row-height 48px  --control-height 30px
--radius-card 8px  --radius-control 6px  --radius-sheet 16px  --sheet-inset 8px
```
**Colour budget: under 3% of pixels.** The hue goes on the main data element (the charter tracks), the
approval pill and the seal, and nowhere in the chrome. Red only on a refused or stopped thing — one red
mark per object, never a red stripe down a list.

**Layout.** 228px labelled sidebar on the desk tint, one hairline. Sheet content 1162px wide inside 20px
padding; two columns 726 + 24 + 412. One left edge from the title through every block. Panels are
`--surface` with a 1px `--border` and **no shadow**. Grouping beats boxing: a KPI row is one panel split
by 1px dividers, not four cards.

## What is on it

**Workspace:** Bracken & Field, a 62-person studio. Products are charters: *Office supplies*,
*Coffee & pantry*, *Client lunches*, *Courier & post*. People: Priya Raman (Ops, approver),
Tom Iverson (Finance), Dan Okafor and Mira Sandoval (requesters, from Slack).

**The objects:** a *Request* (`REQ-1042`) holds items, quantities, unit prices and a vendor, is judged
against one charter, and ends in one of three outcomes — **auto-approved** (within policy),
**awaiting approval** (over the threshold, needs a human), or **vetoed/blocked** (over a hard cap,
vendor not allowed, or consent expired). A **Decision** carries the rule that fired. A **Consent** is
a signed, single-use, expiring authorisation bound to a hash of the exact cart. A **LedgerEntry**
records each PayPal event.

Sample data must reconcile — every total is the sum of its rows, every delta names its baseline, and
a figure that appears twice is one variable. Use real catalogue items and real money:

| Item | Qty | Unit | Vendor | Line |
| --- | --- | --- | --- | --- |
| Vertuo Intenso capsules, 30 ct | 2 | $18.00 | Kettle Supply | $36.00 |
| Bamboo toilet paper, 24 rolls | 1 | $14.50 | Kettle Supply | $14.50 |
| **Cart total** | | | | **$50.50** |

Office supplies charter: $800.00 monthly cap, $300.00 per order, **$15.00 per unit**, approver Priya.
The capsules break the per-unit cap by $3.00 — that is the finding the screen exists to show.

---

## The three states

### State 1 — `shots/desktop.png` · the ledger
The daily work area. **The screen's one question:** *what needs my decision before money moves, and
how much of this month's allowance is already spoken for?*

- **Title:** `Ledger` (h1, 24/600). Second line, 14px grey, carries the finding: *"Two of three open
  requests break the per-unit cap, all from the same vendor."*
- **KPI strip:** one panel, four columns split by hairlines, label above value, no icons, no
  sparklines, no coloured tiles. `Spent this month $412.60` (suffix `/ $800.00` in smaller grey) ·
  `Awaiting you 3` · `Vetoed this month 7` (suffix `$186.40` grey) · `Auto-approved 41`.
- **Largest block:** the three awaiting requests, each a 56px row: request name, charter, the rule
  that fired in 12px grey, amount right-aligned, and an inline `Approve` / `Veto` pair on the row
  that needs it (a visible ghost button, never muted grey text). This is the most frequent job and it
  is one click from the first screen.
- **Beside it:** a spend-versus-cap plan track per charter — a track as long as the cap, the spent
  part filled in `--accent`, what is already committed as a paler blue segment, overrun past the end
  in red. Both ends labelled.
- **Below:** the full request table. Filters in one row above it: status, charter, vendor, and an
  amount threshold ("≥ $50"). Cap at 6 rows with `View all` and a real total (`1,284 requests`).
  Columns: Request · Charter · Vendor · Rule · Amount · Status. Right-aligned tabular money, 12px
  grey sentence-case headers, no zebra, no header band, no checkbox column.
- One filled primary button on the screen, near-black.

### State 2 — `shots/state-1.png` · the record
One request opened. **The screen's one question:** *why was this stopped, and what happens if I
approve it?*

- **Header:** `REQ-1042 · Coffee capsules and toilet paper`, a 14px grey subline naming charter,
  requester and Slack channel (`#office-ops`), and the outcome as one status pill.
- **Left, the cart:** a table of line items with qty, unit, vendor, line total. The offending line
  carries **the screen's one red mark** and its reason set in words: *"$18.00 per unit against a
  $15.00 cap."*
- **Under it, the decision:** the rule id, observed vs limit, and the amendment history
  (`Priya, 14:34 · "only 2 boxes" → re-priced $50.50 → $44.80`).
- **The consent block:** a tinted well — hash (`sha256:7f3ac1d…` in mono, copy button), approver,
  issued at, expires in, `single-use`. This is the product's trust artefact; give it weight.
- **Right, the authority rail — the signature element, only this product has it.** A vertical
  sequence: Slack message 14:29 → 2 items extracted → 14 Channel3 candidates → priced $50.50 →
  decision 14:32 → consent 14:34 → order created → authorised → captured, each with a timestamp and
  its PayPal id in mono. **When a request was vetoed the rail physically stops**: the line ends in a
  short red end-cap, and the steps after it are drawn as hollow, unfilled marks with the words
  *"no capture"*. The rail is the product's argument in one object.
- **Actions:** `Approve` (filled), `Veto`, `Amend cap` (both outlined), and a `Retry capture` text
  action only on a failed-capture record.

### State 3 — `shots/state-2.png` · command palette
`⌘K` over the ledger, dimmed behind it. One 640px panel, 8px radius, a real shadow (it floats), a
36px search field with a caret, grouped results with 12px grey group labels: **Actions** (Approve
request, Veto request, Amend charter cap, Export ledger CSV) · **Requests** · **Charters** ·
**Vendors**. Each row: label, a 12px grey qualifier, and its keyboard hint right-aligned in mono.
First row selected with `--active`. Footer line: `↑↓ to move · ↵ to run · esc to close`. It must be
genuinely operable: typing filters, arrows move, Enter runs, Escape closes.

---

## Behaviour

Clicking an awaiting row opens State 2 with that request. `Approve` moves the request to *approved*
and appends the capture event to the rail. `Veto` stops the rail and writes the reason. The status,
charter and amount filters actually filter the table and recompute the counts in scope. `⌘K` and
`Ctrl+K` open the palette from anywhere; `Esc` closes it and returns focus to what had it.
Keyboard focus is always visible. Empty, zero, loading and error states exist for the table and the
rail — a real zero prints as `$0.00`, a missing value is a grey dash, and an error lives inside the
block that failed, never as a full-screen banner.

The URL hash must open a state directly (`#req-1042`, `#palette`) so the three screenshots can be
taken without clicking.

---

## Don't

No gradient anywhere. No glow, glass or backdrop blur. No icon in a tinted circle or square. No
sparkline. No emoji. No "Welcome back". No violet or indigo. No dark or saturated sidebar. No
uppercase tracked table headers, no zebra rows. No left-aligned numbers, no proportional digits in
columns. No four identical KPI cards. No purple-blue "AI" gradient. No lorem ipsum, no "Acme", no
"John Doe", no invented customer logos or testimonials, no vanity metrics. No placeholder that says
"illustrative" or "demo data" on screen — the data simply is what it is.

---

## Deliverables

1. `index.html` — one file, offline, all assets inline (embedded `woff2` fonts, inline SVG charts,
   inline data).
2. `shots/desktop.png`, `shots/state-1.png`, `shots/state-2.png` — each exactly **1440×900 at
   DPR 2** (2880×1800), captured from the live page:
   ```bash
   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
     --headless=new --disable-gpu --hide-scrollbars \
     --window-size=1440,900 --force-device-scale-factor=2 \
     --screenshot=shots/desktop.png "file://$PWD/index.html"
   ```
3. Open every screenshot and look at it before finishing. Fix what is visibly wrong — a number that
   doesn't line up, a card stopping short of the right edge, a row that ends ragged, dead space inside
   a panel, a label colliding with a line.

**Acceptance:** one left edge and one right edge; no panel with a 40px blank band; every figure the
brief names is on the first screen; the amounts reconcile; the app names itself and its scope; and a
stranger can say what it is and what to do within five seconds of looking at `desktop.png`.
