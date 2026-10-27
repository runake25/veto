import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import {
  api,
  ApiError,
  capMoney,
  money,
  type Agent,
  type AgentDetailData,
  type AgentInput,
  type Me,
  type Reason,
  type RequestDetail,
  type RequestSummary,
  type Routine,
  type RoutineRun,
  type ScopedSearch,
  type PricedLine,
  type SimulateResult,
  type SimulateStreamFrame,
} from '../api'
import { AgentForm } from '../components/AgentForm'
import { AgentAvatar } from '../components/AgentAvatar'
import { InlineError, Track } from '../components/Bits'
import { FreshnessButton, useFreshness } from '../components/freshness'
import { PipelineLive, type Live, type LiveItem } from '../components/live'
import { StatusMark, requestLabel, when } from '../components/request'
import { PressButton, Spinner } from '../components/PressButton'
import { motionSet } from '../components/motion'
import { State, agentState } from '../components/state'
import { StandingJobs } from '../components/StandingJob'

/* What each rule is called when a person reads it, not when the engine does. */
const RULE_WORD: Record<string, string> = {
  catalogue: 'catalogue',
  category_allowlist: 'category',
  vendor_allowlist: 'merchant',
  monthly_cap: 'monthly cap',
  per_order_cap: 'per-order cap',
  per_unit_cap: 'per-item cap',
  approval_threshold: 'signature',
}

const OVERRIDE_WORD: Record<string, string> = {
  per_unit_cap: 'per item',
  per_order_cap: 'per order',
  approval_threshold: 'to sign',
}

function defaultCart(agent: Agent): string {
  if (agent.categories.includes('equipment')) return '2 anti-fatigue standing mats for the studio'
  if (agent.categories.includes('pantry')) return '2 packs of toilet paper'
  if (agent.categories.includes('meals')) return 'lunch for eight, client visit'
  if (agent.categories.includes('stationery')) return 'a box of envelopes and 2 packs of pens'
  return 'a standing mat for the desk'
}

function Money({ cents, fallback }: { cents: number | null; fallback: string }) {
  return cents === null ? <span className="spec-void">{fallback}</span> : <>{capMoney(cents)}</>
}

/**
 * The whole charter on one band: the monthly budget is the lead figure, the three
 * caps sit beside it, and the scope (categories, merchants, exceptions) reads as
 * plain sentences underneath. This is the policy; the read-only rule table it
 * replaces only repeated these numbers in snake_case.
 */
function Guardrails({ agent }: { agent: Agent }) {
  const on = new Set(agent.rules)
  const monthly = on.has('monthly_cap') ? agent.monthly_cap_cents : null
  const perOrder = on.has('per_order_cap') ? agent.per_order_cap_cents : null
  const perItem = on.has('per_unit_cap') ? agent.per_unit_cap_cents : null
  const signature = on.has('approval_threshold') ? agent.approval_threshold_cents : null
  const categories = on.has('category_allowlist') ? agent.categories : null
  const merchants = on.has('vendor_allowlist') ? agent.vendors : null
  const exceptions = agent.overrides.filter((override) => override.value_cents !== null)

  return (
    <section className="card guardrails">
      <header className="card-head">
        <h2>Charter</h2>
        <span className="card-note">what it may buy</span>
      </header>

      <div className="spec-band">
        <div className="spec-lead">
          <p className="spec-label">Monthly budget</p>
          <p className="spec-number">
            <Money cents={monthly} fallback="No cap" />
          </p>
        </div>
        <div className="spec-cell">
          <p className="spec-label">Per order</p>
          <p className="spec-figure">
            <Money cents={perOrder} fallback="No cap" />
          </p>
        </div>
        <div className="spec-cell">
          <p className="spec-label">Per item</p>
          <p className="spec-figure">
            <Money cents={perItem} fallback="No cap" />
          </p>
        </div>
        <div className="spec-cell">
          <p className="spec-label">Signature above</p>
          <p className="spec-figure">
            <Money cents={signature} fallback="Pays alone" />
          </p>
        </div>
      </div>

      {monthly !== null ? (
        <div className="band-track">
          <Track
            spentCents={agent.spent_month_cents ?? 0}
            committedCents={agent.committed_month_cents ?? 0}
            capCents={monthly}
          />
        </div>
      ) : null}

      <dl className="scope-list">
        <div className="scope">
          <dt>Can buy</dt>
          <dd>
            {categories === null ? (
              <span className="spec-void">Any category</span>
            ) : categories.length ? (
              <span className="scope-chips">
                {categories.map((category) => (
                  <span className="chip chip-quiet" key={category}>
                    {category}
                  </span>
                ))}
              </span>
            ) : (
              <span className="spec-void">Nothing yet</span>
            )}
          </dd>
        </div>
        <div className="scope">
          <dt>Merchants</dt>
          <dd>
            {merchants === null ? (
              <span className="spec-void">Any merchant</span>
            ) : merchants.length ? (
              <span className="scope-mono mono">{merchants.join(' · ')}</span>
            ) : (
              <span className="spec-void">None approved yet</span>
            )}
          </dd>
        </div>
        {exceptions.length ? (
          <div className="scope">
            <dt>Exception</dt>
            <dd>
              {exceptions.map((override) => (
                <span className="scope-exception" key={`${override.category}-${override.kind}`}>
                  <span className="chip chip-quiet">{override.category}</span>{' '}
                  {money(override.value_cents as number)}{' '}
                  {OVERRIDE_WORD[override.kind] ?? 'override'}
                </span>
              ))}
            </dd>
          </div>
        ) : null}
      </dl>
    </section>
  )
}

function ReasonRow({ reason }: { reason: Reason }) {
  return (
    <li>
      <span className={`rule-flag ${reason.severity === 'block' ? 'is-neg' : 'is-wait'}`}>
        {RULE_WORD[reason.rule] ?? reason.rule}
      </span>
      <span className="reason-text">{reason.message}</span>
      {reason.unblock ? <span className="reason-unblock">{reason.unblock}</span> : null}
    </li>
  )
}

/** The dry run's verdict in the shared state language, before the details. */
function dryOutcome(outcome: SimulateResult['decision']['outcome']): {
  label: string
  tone: 'pos' | 'wait' | 'neg'
} {
  if (outcome === 'auto') return { label: 'Would pay automatically', tone: 'pos' }
  if (outcome === 'review') return { label: 'Would wait for a signature', tone: 'wait' }
  return { label: 'Refused', tone: 'neg' }
}

const FIT_LABELS = {
  category: 'category blocked',
  per_unit: 'over per-item cap',
  per_order: 'over per-order cap',
} as const

function FitFlags({ item }: { item: ScopedSearch['results'][number] }) {
  const flags: { key: keyof typeof FIT_LABELS; soft: boolean }[] = []
  if (!item.fits.category) flags.push({ key: 'category', soft: false })
  if (!item.fits.per_order) flags.push({ key: 'per_order', soft: false })
  if (!item.fits.per_unit) flags.push({ key: 'per_unit', soft: true })
  if (!flags.length) return null
  return (
    <span className="fit-flags">
      {flags.map((flag) => (
        <span className={flag.soft ? 'fit-soon' : 'fit-bad'} key={flag.key}>
          {FIT_LABELS[flag.key]}
        </span>
      ))}
    </span>
  )
}

/** A search result's own verdict — not an approval, so it does not borrow the
    consent language ("fits", not "auto-approved"). Only the category and
    per-order caps are hard blocks; a per-item cap or the signature threshold
    still lets a human say yes. */
function FitPill({ item }: { item: ScopedSearch['results'][number] }) {
  if (!item.fits.category || !item.fits.per_order) {
    return <State tone="neg">Blocked</State>
  }
  if (!item.fits.per_unit || !item.fits.threshold) {
    return <State tone="wait">Needs signature</State>
  }
  return <State tone="pos">Fits</State>
}

type AddOutcome =
  | { state: 'done'; request: RequestDetail }
  | { state: 'error'; message: string }

/** The record an offer actually produced — never a promise, always the engine's answer. */
function OutcomeBand({ outcome }: { outcome: AddOutcome }) {
  return (
    <span
      className={`result-outcome${outcome.state === 'error' ? ' is-error' : ''}`}
      role={outcome.state === 'error' ? 'alert' : 'status'}
    >
      {outcome.state === 'done' ? (
        <>
          <span className="mono result-outcome-id">{requestLabel(outcome.request.seq)}</span>
          <StatusMark status={outcome.request.status} />
          <span className="result-outcome-amount">{money(outcome.request.total_cents)}</span>
          <Link className="text-link" to={`/inbox/${outcome.request.id}`}>
            Open record
          </Link>
        </>
      ) : (
        <span className="inline-error">{outcome.message}</span>
      )}
    </span>
  )
}

function lineKey(line: PricedLine, index: number): string {
  return `line:${line.sku ?? line.title}-${index}`
}

/** The catalogue returns the chosen offer too; keep it in the picked block only. */
function sameOffer(a: PricedLine, b: PricedLine): boolean {
  if (a.sku && b.sku && a.sku === b.sku) return true
  return (
    a.title.trim().toLowerCase() === b.title.trim().toLowerCase() &&
    (a.vendor ?? '').trim().toLowerCase() === (b.vendor ?? '').trim().toLowerCase()
  )
}

/**
 * The one tool card on an agent. Type a cart: the dry run streams the pipeline
 * and shows the offer it would actually get, with the offer's own detail; the
 * catalogue below shows the other offers in scope. Any row can become a real
 * request — the picked line is pinned, so it is ordered exactly as shown.
 */
function TestRequest({ agent, onCreated }: { agent: Agent; onCreated: () => void }) {
  const reduce = useReducedMotion()

  // the dry run
  const [text, setText] = useState(() => defaultCart(agent))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<SimulateResult | null>(null)
  const [live, setLive] = useState<Live | null>(null)
  const runAbort = useRef<AbortController | null>(null)

  // the offers behind that pick
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [search, setSearch] = useState<ScopedSearch | null>(null)
  const [adding, setAdding] = useState<string | null>(null)
  const [outcomes, setOutcomes] = useState<Record<string, AddOutcome>>({})

  useEffect(() => () => runAbort.current?.abort(), [])

  const loadSimilar = async (lookFor: string) => {
    if (!lookFor.trim()) return
    setSearching(true)
    setSearchError(null)
    setSearch(null)
    try {
      setSearch(await api.searchAgent(agent.id, lookFor, 1))
    } catch (err) {
      setSearchError(err instanceof ApiError ? err.message : 'The catalogue did not answer.')
    } finally {
      setSearching(false)
    }
  }

  /** One real request from one offer: pinned, so the price is the shown price. */
  const request = async (offer: PricedLine, key: string) => {
    if (adding) return
    setAdding(key)
    setOutcomes((prev) => {
      const next = { ...prev }
      delete next[key]
      return next
    })
    try {
      const record = await api.createPinnedRequest(agent.id, offer)
      setOutcomes((prev) => ({ ...prev, [key]: { state: 'done', request: record } }))
      onCreated()
    } catch (err) {
      setOutcomes((prev) => ({
        ...prev,
        [key]: {
          state: 'error',
          message: err instanceof ApiError ? err.message : 'The request was not created.',
        },
      }))
    } finally {
      setAdding(null)
    }
  }

  const run = async (event: FormEvent) => {
    event.preventDefault()
    if (!text.trim() || busy) return
    runAbort.current?.abort()
    const controller = new AbortController()
    runAbort.current = controller
    setBusy(true)
    setError(null)
    setResult(null)
    setSearch(null)
    setSearchError(null)
    setOutcomes({})
    setLive({ steps: [], items: [] })
    try {
      const next = await api.simulateStream(
        text,
        agent.id,
        (frame: SimulateStreamFrame) => {
          if (frame.type === 'event') {
            setLive((prev) => (prev ? { ...prev, steps: [...prev.steps, frame.event] } : prev))
          } else if (frame.type === 'item') {
            const item: LiveItem = {
              index: frame.index,
              name: frame.name,
              ok: frame.ok,
              title: frame.title,
            }
            setLive((prev) => {
              if (!prev) return prev
              const items = [...prev.items, item].sort((a, b) => a.index - b.index)
              return { ...prev, items }
            })
          }
        },
        controller.signal,
      )
      setResult(next)
      // The catalogue behind the pick: the chosen item's title is the best query.
      void loadSimilar(next.lines[0]?.title || text)
    } catch (err) {
      if (!controller.signal.aborted) {
        setError(err instanceof ApiError ? err.message : 'The dry run failed.')
      }
    } finally {
      if (runAbort.current === controller) {
        setBusy(false)
        setLive(null)
        runAbort.current = null
      }
    }
  }

  const chosen = result?.lines ?? []
  const scope = search?.scoped_by
  const similar = (search?.results ?? []).filter(
    (item) => !chosen.some((line) => sameOffer(line, item)),
  )

  return (
    <section className="card test-request">
      <header className="card-head">
        <h2>Test a request</h2>
        <span className="card-note">dry run · nothing ordered</span>
      </header>
      <p className="card-lede">
        Type a cart. The run shows the offer this agent would actually get — its title, merchant,
        category and price — then the catalogue offers around it; any row can be ordered exactly
        as shown.
      </p>
      <form className="inline-form" onSubmit={run}>
        <input
          className="input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="2 standing mats for the studio"
          disabled={busy}
          aria-label="Cart to test"
        />
        <PressButton type="submit" loading={busy}>
          {busy ? (
            <>
              <Spinner label="working" /> Working…
            </>
          ) : (
            'Run'
          )}
        </PressButton>
      </form>

      {error ? <p className="inline-error">{error}</p> : null}

      {busy && live ? (
        <PipelineLive
          steps={live.steps}
          items={live.items}
          title="Running the pipeline"
          note="read-only · nothing ordered"
        />
      ) : null}

      <AnimatePresence mode="wait">
        {result ? (
          <motion.div
            key={`${result.decision.outcome}-${result.total_cents}-${result.elapsed_ms}`}
            className="dry-result"
            initial={reduce ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, transition: { duration: 0.12 } }}
            transition={{ duration: 0.24, ease: [0.16, 1, 0.3, 1] }}
          >
            <div className="dry-head">
              <State tone={dryOutcome(result.decision.outcome).tone} lead>
                {dryOutcome(result.decision.outcome).label}
              </State>
              <span className="dry-total">{money(result.total_cents)}</span>
              <span className="dry-meta">
                {result.lines.length
                  ? result.source === 'channel3'
                    ? 'Channel3'
                    : 'local catalogue'
                  : 'no catalogue match'}
                {result.parallel_items && result.parallel_items > 1
                  ? ` · ${result.parallel_items} items in parallel`
                  : ''}
                {' · '}
                {Math.round(result.elapsed_ms)}ms
              </span>
            </div>

            {chosen.length ? (
              <ul className="search-results picked-offers" aria-label="The offer this cart would get">
                {chosen.map((line, index) => {
                  const key = lineKey(line, index)
                  const outcome = outcomes[key]
                  return (
                    <li key={key}>
                      <span className="thumb">
                        {line.image ? <img src={line.image} alt="" loading="lazy" /> : null}
                      </span>
                      <span className="result-main">
                        <a
                          className="result-title"
                          href={line.url ?? '#'}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {line.title}
                        </a>
                        <span className="result-meta">
                          <span className="mono">{line.vendor ?? 'unknown merchant'}</span>
                          {line.category ? (
                            <span className="chip chip-quiet">{line.category}</span>
                          ) : null}
                          {line.brand ? <span className="chip chip-quiet">{line.brand}</span> : null}
                        </span>
                        <span className="picked-note">the offer this cart would get</span>
                      </span>
                      <span className="result-right">
                        <span className="result-price mono">{money(line.line_cents)}</span>
                        {line.qty > 1 ? (
                          <span className="picked-total">
                            <span className="picked-qty mono">
                              {line.qty} × {money(line.unit_cents)}
                            </span>
                          </span>
                        ) : null}
                        {outcome?.state === 'done' ? null : (
                          <button
                            type="button"
                            className="btn btn-secondary btn-small result-request"
                            onClick={() => void request(line, key)}
                            disabled={adding !== null}
                          >
                            {adding === key ? 'Requesting…' : 'Request this'}
                          </button>
                        )}
                      </span>
                      {outcome ? <OutcomeBand outcome={outcome} /> : null}
                    </li>
                  )
                })}
              </ul>
            ) : (
              <div className="dry-miss">
                <p className="dry-empty">
                  {result.near_misses?.length
                    ? 'No catalogue match for the item, so nothing was priced.'
                    : 'Nothing in scope, so nothing was priced.'}
                </p>
                {(result.near_misses ?? []).map((miss) => (
                  <div className="near" key={miss.item}>
                    <p className="near-head">Closest offers for “{miss.item}”</p>
                    <ul className="near-list">
                      {miss.offers.map((offer) => (
                        <li key={`${offer.title}-${offer.vendor}`}>
                          <span className="near-title">{offer.title}</span>
                          <span className="mono near-vendor">{offer.vendor ?? 'unknown'}</span>
                          <span className="near-price mono">{money(offer.unit_cents)}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
                <p className="fineprint">
                  The catalogue covers household, office and equipment goods; no groceries or food
                  brands.
                </p>
              </div>
            )}

            {result.decision.reasons.length ? (
              <ul className="reason-list">
                {result.decision.reasons.map((reason, i) => (
                  <ReasonRow reason={reason} key={`${reason.rule}-${i}`} />
                ))}
              </ul>
            ) : (
              <p className="dry-note">Inside every rule — this would pay with no human.</p>
            )}

            <ol className="mini-rail">
              {result.events.map((event, i) => (
                <li key={`${event.kind}-${i}`}>
                  <span className="mini-step mono">{String(i + 1).padStart(2, '0')}</span>
                  <span className="mini-label">{event.label}</span>
                  {event.detail ? <span className="mini-detail mono">{event.detail}</span> : null}
                </li>
              ))}
            </ol>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <section className="tool-sub" aria-label="Similar offers">
        <header className="tool-sub-head">
          <h3>Similar offers</h3>
          <span className="card-note">catalogue · scoped to the rules</span>
        </header>
        {!result ? (
          <p className="fineprint">
            Run a test and the catalogue offers behind its pick appear here — pick any one to
            order it exactly as shown.
          </p>
        ) : null}
        {searching ? <p className="fineprint">Reading the catalogue…</p> : null}
        {searchError ? <p className="inline-error">{searchError}</p> : null}
        {result && search ? (
          <motion.div
            key={`${search.query}-${similar.length}`}
            initial={{ opacity: 0, y: reduce ? 0 : 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
          >
            <p className="scoped-line">
              Searched {scope?.vendors.length ?? 0} merchants
              {scope?.max_price_cents ? `, under ${money(scope.max_price_cents)} per order` : ''}
            </p>
            {similar.length ? (
              <ul className="search-results">
                {similar.map((item, i) => {
                  const key = `${item.sku ?? item.title}-${i}`
                  const outcome = outcomes[key]
                  return (
                    <motion.li
                      key={key}
                      initial={{ opacity: 0, y: reduce ? 0 : 5 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: reduce ? 0 : i * 0.04, duration: 0.2 }}
                    >
                      <span className="thumb">
                        {item.image ? <img src={item.image} alt="" loading="lazy" /> : null}
                      </span>
                      <span className="result-main">
                        <a
                          className="result-title"
                          href={item.url ?? '#'}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {item.title}
                        </a>
                        <span className="result-meta">
                          <span className="mono">{item.vendor ?? 'unknown merchant'}</span>
                          {item.category ? (
                            <span className="chip chip-quiet">{item.category}</span>
                          ) : null}
                        </span>
                        <FitFlags item={item} />
                      </span>
                      <span className="result-right">
                        <span className="result-price mono">{money(item.unit_cents)}</span>
                        <FitPill item={item} />
                        {outcome?.state === 'done' ? null : (
                          <button
                            type="button"
                            className="btn btn-secondary btn-small result-request"
                            onClick={() => void request(item, key)}
                            disabled={adding !== null}
                          >
                            {adding === key ? 'Requesting…' : 'Request this'}
                          </button>
                        )}
                      </span>
                      {outcome ? <OutcomeBand outcome={outcome} /> : null}
                    </motion.li>
                  )
                })}
              </ul>
            ) : (
              <p className="dry-empty">No other offers in scope for this cart.</p>
            )}
            {search.filtered.irrelevant ? (
              <p className="fineprint">
                {search.filtered.irrelevant} unrelated catalogue{' '}
                {search.filtered.irrelevant === 1 ? 'offer was' : 'offers were'} left out.
              </p>
            ) : null}
          </motion.div>
        ) : null}
      </section>
    </section>
  )
}

/**
 * The receipts: every request this instance has raised, newest first. A bot
 * that spends is only trustworthy if its paper trail sits beside its charter —
 * who asked, who signed, what it cost, where the record is.
 */
function Orders({ agent, refreshTick }: { agent: Agent; refreshTick: number }) {
  const [rows, setRows] = useState<RequestSummary[] | null>(null)
  const [total, setTotal] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    let live = true
    setError(null)
    api
      .requests(`agent_id=${encodeURIComponent(agent.id)}&limit=15&sort=newest`)
      .then((data) => {
        if (!live) return
        setRows(data.requests)
        setTotal(data.total)
      })
      .catch(() => {
        if (live) setError('The orders did not load.')
      })
    return () => {
      live = false
    }
  }, [agent.id, refreshTick, retry])

  return (
    <section className="card orders">
      <header className="card-head">
        <h2>Orders</h2>
        <span className="card-note">
          {rows
            ? total > rows.length
              ? `Latest ${rows.length} of ${total}`
              : `${total} ${total === 1 ? 'order' : 'orders'}`
            : 'receipts'}
        </span>
      </header>

      {error ? (
        <InlineError onRetry={() => setRetry((value) => value + 1)}>{error}</InlineError>
      ) : null}

      {!rows && !error ? (
        <div className="receipt-list receipt-list-skel" aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <div className="receipt-skel" key={i}>
              <span className="skel" style={{ width: '46%', height: 11 }} />
              <span className="skel" style={{ width: 64, height: 11 }} />
            </div>
          ))}
        </div>
      ) : null}

      {rows && rows.length ? (
        <ul className="receipt-list">
          {rows.map((req) => (
            <li key={req.id}>
              <Link className="receipt" to={`/inbox/${req.id}`}>
                <span className="receipt-main">
                  <span className="receipt-title">{req.title}</span>
                  <span className="receipt-meta">
                    <span className="mono receipt-id">{requestLabel(req.seq)}</span>
                    <span className="receipt-when">{when(req.created_at)}</span>
                    {req.requester ? (
                      <span className="receipt-person">Asked by {req.requester}</span>
                    ) : null}
                    {req.approver ? (
                      <span className="receipt-person">Signed by {req.approver.name}</span>
                    ) : null}
                  </span>
                </span>
                <span className="receipt-right">
                  <span className="receipt-amount">{money(req.total_cents)}</span>
                  <StatusMark status={req.status} />
                </span>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}

      {rows && !rows.length ? (
        <div className="orders-empty">
          <p className="empty-title">No orders yet.</p>
          <p className="empty-note">Anything this bot requests shows up here as a receipt.</p>
        </div>
      ) : null}
    </section>
  )
}

export function AgentDetail({ me }: { me: Me }) {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const [agent, setAgent] = useState<Agent | null>(null)
  const [routines, setRoutines] = useState<Routine[]>([])
  const [runs, setRuns] = useState<RoutineRun[]>([])
  const [error, setError] = useState<string | null>(null)
  const [reloadTick, setReloadTick] = useState(0)
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)
  const [statusBusy, setStatusBusy] = useState(false)
  const [confirmStatus, setConfirmStatus] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [ordersTick, setOrdersTick] = useState(0)
  const { tick, markLoaded } = useFreshness()
  const reduce = useReducedMotion()
  const m = motionSet(reduce)

  const applyDetail = (detail: AgentDetailData) => {
    setAgent(detail.agent)
    setRoutines(detail.routines ?? (detail.routine ? [detail.routine] : []))
    setRuns(detail.runs)
  }

  // A different agent starts blank; a fresh read keeps what is on screen.
  useEffect(() => {
    setAgent(null)
    setError(null)
  }, [id])

  useEffect(() => {
    let live = true
    api
      .agentDetail(id)
      .then((detail) => {
        if (!live) return
        applyDetail(detail)
        markLoaded()
      })
      .catch(() => {
        if (live) {
          setError('That agent does not exist, or it belongs to another workspace.')
        }
      })
    return () => {
      live = false
    }
    // applyDetail only sets state; the fetch is the effect's only dependency
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, tick, reloadTick, markLoaded])

  const refresh = async () => {
    applyDetail(await api.agentDetail(id))
  }

  /* A catalogue request is real money traffic: re-read the agent (spent and
     committed move) and the receipts list under it. */
  const requestCreated = () => {
    setOrdersTick((value) => value + 1)
    void refresh().catch(() => {
      // the live tick and the next visit will catch up; the request exists
    })
  }

  const setStatus = async (status: 'active' | 'paused') => {
    if (!agent) return
    setStatusBusy(true)
    setEditError(null)
    try {
      // PATCH returns the rules row, not this month's money: re-read the whole
      // agent so the guardrail track and the job card keep their figures.
      await api.updateAgent(agent.id, { status })
      await refresh()
      setConfirmStatus(false)
    } catch (err) {
      setEditError(err instanceof ApiError ? err.message : 'Could not change the agent state.')
    } finally {
      setStatusBusy(false)
    }
  }

  const remove = async () => {
    if (!agent) return
    setDeleteBusy(true)
    setDeleteError(null)
    try {
      await api.deleteAgent(agent.id)
      navigate('/agents')
    } catch (err) {
      setDeleteError(
        err instanceof ApiError
          ? err.message
          : 'The agent was not deleted. Nothing changed.',
      )
      setDeleteBusy(false)
    }
  }

  const save = async (input: AgentInput) => {
    if (!agent) return
    setBusy(true)
    setEditError(null)
    try {
      await api.updateAgent(agent.id, input)
      await refresh()
      setSaved(true)
      window.setTimeout(() => {
        setSaved(false)
        setEditing(false)
        setBusy(false)
      }, reduce ? 0 : 520)
    } catch (err) {
      setEditError(err instanceof ApiError ? err.message : 'Could not save these rules.')
      setBusy(false)
    }
  }

  if (error) {
    return (
      <div className="screen">
        <nav className="breadcrumb mono">
          <Link to="/agents">Agents</Link>
        </nav>
        <section className="card">
          <InlineError onRetry={() => setReloadTick((value) => value + 1)}>{error}</InlineError>
          <Link className="text-link mono" to="/agents">
            ← back to agents
          </Link>
        </section>
      </div>
    )
  }

  if (!agent) {
    return (
      <div className="screen agent-skel" aria-hidden="true">
        <span className="skel" style={{ width: 148, height: 10 }} />
        <div className="instance-head-skel">
          <span className="skel" style={{ width: 56, height: 56, borderRadius: 13 }} />
          <div className="agent-skel-ident">
            <span className="skel" style={{ width: 220, height: 26 }} />
            <span className="skel" style={{ width: 300, height: 11 }} />
          </div>
        </div>
        <div className="card">
          <span className="skel" style={{ width: 118, height: 16 }} />
          <div className="agent-skel-band">
            {[0, 1, 2, 3].map((i) => (
              <span className="skel" style={{ height: 42 }} key={i} />
            ))}
          </div>
          <span className="skel" style={{ width: '100%', height: 10, marginTop: 14 }} />
          <span className="skel" style={{ width: '74%', height: 10, marginTop: 8 }} />
        </div>
        <div className="card">
          <span className="skel" style={{ width: 132, height: 16 }} />
          <span className="skel" style={{ width: '100%', height: 62, marginTop: 14 }} />
        </div>
        <div className="card">
          <span className="skel" style={{ width: '34%', height: 16 }} />
          <span className="skel" style={{ width: '100%', height: 38, marginTop: 14 }} />
          <span className="skel" style={{ width: '100%', height: 10, marginTop: 22 }} />
          <span className="skel" style={{ width: '72%', height: 10, marginTop: 8 }} />
          <span className="skel" style={{ width: '100%', height: 38, marginTop: 18 }} />
        </div>
        <div className="card">
          <span className="skel" style={{ width: '26%', height: 16 }} />
          {[0, 1, 2].map((i) => (
            <span className="skel" style={{ width: '100%', height: 26, marginTop: i ? 10 : 16 }} key={i} />
          ))}
        </div>
      </div>
    )
  }

  const headerState = agentState(agent)

  return (
    <motion.div className="screen" variants={m.stagger} initial="hidden" animate="show">
      <motion.div className="breadcrumb-row" variants={m.riseIn}>
        <nav className="breadcrumb mono" aria-label="Breadcrumb">
          <Link to="/agents">Agents</Link> <span aria-hidden="true">/</span>{' '}
          <span>{agent.name}</span>
        </nav>
        <FreshnessButton />
      </motion.div>

      <motion.header className="screen-head instance-head" variants={m.riseIn}>
        <div className="instance-ident">
          <AgentAvatar agent={agent} size={56} />
          <div className="instance-text">
            <div className="instance-title">
              <h1>{agent.name}</h1>
              <State tone={headerState.tone}>{headerState.label}</State>
            </div>
            <p className="instance-serial">
              <span className="mono instance-serial-id">{agent.id}</span>
              {agent.channel ? <span className="mono instance-channel">{agent.channel}</span> : null}
              <span className="instance-tag">{agent.tag}</span>
            </p>
          </div>
        </div>
        {me.user.role === 'viewer' ? (
          <span className="card-note">viewer — read only</span>
        ) : (
          <div className="agent-actions">
            <PressButton
              variant={editing ? 'quiet' : 'primary'}
              onClick={() => setEditing((v) => !v)}
            >
              {editing ? 'Close editor' : 'Edit rules'}
            </PressButton>
            {agent.status === 'active' ? (
              confirmStatus ? (
                <>
                  <PressButton
                    variant="quiet"
                    loading={statusBusy}
                    onClick={() => void setStatus('paused')}
                  >
                    Confirm pause
                  </PressButton>
                  <PressButton variant="quiet" onClick={() => setConfirmStatus(false)}>
                    Cancel
                  </PressButton>
                </>
              ) : (
                <PressButton variant="quiet" onClick={() => setConfirmStatus(true)}>
                  Pause agent
                </PressButton>
              )
            ) : (
              <PressButton
                variant="secondary"
                loading={statusBusy}
                onClick={() => void setStatus('active')}
              >
                Resume agent
              </PressButton>
            )}
          </div>
        )}
      </motion.header>

      <AnimatePresence initial={false}>
        {editing ? (
          <motion.section
            className="card"
            key="editor"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0, transition: { duration: 0.16 } }}
            transition={{ duration: 0.26, ease: [0.16, 1, 0.3, 1] }}
            style={{ overflow: 'hidden' }}
          >
            <header className="card-head">
              <h2>Rules</h2>
              <span className="card-note">guided</span>
            </header>
            <AgentForm
              initial={agent}
              submitLabel="Save rules"
              busy={busy}
              saved={saved}
              error={editError}
              onSubmit={(input) => void save(input)}
              onCancel={() => setEditing(false)}
            />
          </motion.section>
        ) : null}
      </AnimatePresence>

      <motion.div variants={m.riseIn}>
        <Guardrails agent={agent} />
      </motion.div>

      <motion.div variants={m.riseIn}>
        <StandingJobs
          agent={agent}
          routines={routines}
          runs={runs}
          role={me.user.role}
          defaultInstruction={defaultCart(agent)}
          onRoutinesChange={setRoutines}
          onRefresh={refresh}
        />
      </motion.div>

      <motion.div variants={m.riseIn}>
        <TestRequest agent={agent} onCreated={requestCreated} />
      </motion.div>

      <motion.div variants={m.riseIn}>
        <Orders agent={agent} refreshTick={tick + ordersTick} />
      </motion.div>

      {me.user.role === 'viewer' ? null : (
        <motion.section className="card danger-card" variants={m.riseIn}>
          <header className="card-head">
            <h2>Remove this agent</h2>
            <span className="card-note">irreversible</span>
          </header>
          <p className="card-lede">
            Deletes the agent, its rules and its jobs. An agent with requests on record cannot be
            deleted — pause it instead.
          </p>
          {deleteError ? (
            <p className="inline-error" role="alert">
              {deleteError}
            </p>
          ) : null}
          {confirmDelete ? (
            <div className="danger-confirm">
              <p className="danger-copy">
                Delete {agent.name}? This cannot be undone. Its jobs go too; requests it made
                stay in the record.
              </p>
              <div className="form-actions">
                <button
                  type="button"
                  className="btn btn-danger"
                  onClick={() => void remove()}
                  disabled={deleteBusy}
                >
                  {deleteBusy ? 'Deleting…' : 'Confirm delete'}
                </button>
                <button
                  type="button"
                  className="btn btn-quiet"
                  onClick={() => setConfirmDelete(false)}
                  disabled={deleteBusy}
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              className="btn btn-quiet danger-open"
              onClick={() => setConfirmDelete(true)}
            >
              Delete agent
            </button>
          )}
        </motion.section>
      )}
    </motion.div>
  )
}
