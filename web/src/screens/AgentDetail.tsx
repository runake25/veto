import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import {
  api,
  ApiError,
  capMoney,
  money,
  type Agent,
  type AgentInput,
  type Reason,
  type ScopedSearch,
  type SimulateResult,
  type SimulateStreamFrame,
} from '../api'
import { AgentForm } from '../components/AgentForm'
import { OutcomePill, Track } from '../components/Bits'
import { PressButton, Spinner } from '../components/PressButton'
import { motionSet } from '../components/motion'

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

function defaultQuery(agent: Agent): string {
  if (agent.categories.includes('equipment')) return 'anti-fatigue standing mat'
  if (agent.categories.includes('pantry')) return 'toilet paper'
  if (agent.categories.includes('meals')) return 'team lunch catering'
  if (agent.categories.includes('stationery')) return 'envelopes'
  return 'standing mat'
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
        <h2>Guardrails</h2>
        <span className="card-note">what this agent may do</span>
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

function sendLabel(result: SimulateResult): string {
  if (result.decision.outcome === 'auto') return `Send · capture ${money(result.total_cents)}`
  if (result.decision.outcome === 'review') return 'Send for a signature'
  return 'Send · record as blocked'
}

function sendNote(result: SimulateResult): string {
  if (result.decision.outcome === 'auto')
    return 'Inside every rule: creating the request captures this amount through PayPal immediately.'
  if (result.decision.outcome === 'review')
    return 'A real request is recorded and waits for a signature in the Inbox.'
  return 'A real request is recorded as blocked. No consent, no PayPal object, no money.'
}

type LiveStep = { kind: string; label: string; detail?: string | null }
type LiveItem = { index: number; name: string; ok: boolean; title?: string | null }

/* What the live panel says while a stage is still in flight. */
function liveNextLabel(steps: LiveStep[]): string {
  if (!steps.length) return 'Reading the request…'
  const last = steps[steps.length - 1]?.kind
  if (last === 'extract') return 'Searching and choosing…'
  if (last === 'catalogue') return 'Pricing the cart…'
  if (last === 'price') return 'Applying the rules…'
  return 'Finishing…'
}

function DryRun({ agent }: { agent: Agent }) {
  const [text, setText] = useState(() => defaultCart(agent))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<SimulateResult | null>(null)
  const [live, setLive] = useState<{ steps: LiveStep[]; items: LiveItem[] } | null>(null)
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)
  const runAbort = useRef<AbortController | null>(null)
  const navigate = useNavigate()

  useEffect(() => () => runAbort.current?.abort(), [])

  const send = async () => {
    if (!result) return
    setSending(true)
    setSendError(null)
    try {
      const record = await api.createRequest(text, agent.id)
      navigate(`/inbox/${record.id}`)
    } catch (err) {
      setSendError(err instanceof ApiError ? err.message : 'The request was not created.')
    } finally {
      setSending(false)
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

  return (
    <section className="card">
      <header className="card-head">
        <h2>Test a request</h2>
        <span className="card-note">dry run</span>
      </header>
      <p className="card-lede">
        The agent&rsquo;s decision for this message: extract, choose a product, price it, apply
        the rules. Nothing is ordered until you send the request.
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
        <div className="dry-live" aria-live="polite">
          <div className="dry-live-head">
            <span className="live-dot" aria-hidden />
            <span>Running the pipeline</span>
            <span className="dry-live-note">read-only · nothing ordered</span>
          </div>
          {live.items.length ? (
            <ul className="live-items">
              {live.items.map((item) => (
                <li key={item.index} className={item.ok ? 'is-ok' : 'is-miss'}>
                  <span className="mono live-index">
                    {String(item.index + 1).padStart(2, '0')}
                  </span>
                  <span className="live-name">{item.ok ? item.title : item.name}</span>
                  <span className="live-state">{item.ok ? 'found' : 'no match'}</span>
                </li>
              ))}
            </ul>
          ) : null}
          <ol className="mini-rail">
            {live.steps.map((step, i) => (
              <li key={`${step.kind}-${i}`}>
                <span className="mini-step mono">{String(i + 1).padStart(2, '0')}</span>
                <span className="mini-label">{step.label}</span>
                {step.detail ? <span className="mini-detail mono">{step.detail}</span> : null}
              </li>
            ))}
            <li className="is-pending">
              <span className="mini-step mono">
                {String(live.steps.length + 1).padStart(2, '0')}
              </span>
              <span className="mini-label">{liveNextLabel(live.steps)}</span>
            </li>
          </ol>
        </div>
      ) : null}

      <AnimatePresence mode="wait">
        {result ? (
          <motion.div
            key={`${result.decision.outcome}-${result.total_cents}-${result.elapsed_ms}`}
            className="dry-result"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, transition: { duration: 0.12 } }}
            transition={{ duration: 0.24, ease: [0.16, 1, 0.3, 1] }}
          >
            <div className="dry-head">
              <OutcomePill outcome={result.decision.outcome} dry />
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

            {result.lines.length ? (
              <ul className="cart-lines">
                {result.lines.map((line) => (
                  <li key={`${line.sku ?? line.title}-${line.qty}`}>
                    <span className="cart-qty mono">{line.qty}×</span>
                    <span className="cart-title">{line.title}</span>
                    {line.category ? (
                      <span className="chip chip-quiet cart-cat">{line.category}</span>
                    ) : null}
                    <span className="cart-vendor mono">{line.vendor ?? 'unknown'}</span>
                    <span className="cart-money">{money(line.line_cents)}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="dry-miss">
                <p className="dry-empty">
                  {result.near_misses?.length
                    ? 'Nothing the catalogue returned is the item asked for, so the cart never got priced.'
                    : 'No product was found inside this agent’s scope, so the cart never got priced.'}
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
                  This catalogue carries household, office and equipment goods — it does not
                  stock groceries or food brands.
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

            <div className="dry-send">
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => void send()}
                disabled={sending}
              >
                {sending ? 'Sending…' : sendLabel(result)}
              </button>
              <p className="dry-send-note">{sendNote(result)}</p>
              {sendError ? <p className="inline-error">{sendError}</p> : null}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </section>
  )
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
    return <span className="pill is-neg">Blocked</span>
  }
  if (!item.fits.per_unit || !item.fits.threshold) {
    return <span className="pill is-wait">Needs signature</span>
  }
  return <span className="pill is-pos">Fits</span>
}

function FindProduct({ agent }: { agent: Agent }) {
  const [query, setQuery] = useState(() => defaultQuery(agent))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<ScopedSearch | null>(null)
  const reduce = useReducedMotion()

  const run = async (event: FormEvent) => {
    event.preventDefault()
    if (!query.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      setResult(await api.searchAgent(agent.id, query))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The search failed.')
    } finally {
      setBusy(false)
    }
  }

  const scope = result?.scoped_by
  return (
    <section className="card">
      <header className="card-head">
        <h2>Browse the catalogue</h2>
        <span className="card-note">scoped to the rules</span>
      </header>
      <p className="card-lede">
        Every offer these merchants return for a search, annotated with the rules it passes or
        fails. This is the list a decision is made from.
      </p>
      <form className="inline-form" onSubmit={run}>
        <input
          className="input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="what should it look for?"
          disabled={busy}
          aria-label="Product search"
        />
        <PressButton type="submit" loading={busy} variant="secondary">
          {busy ? (
            <>
              <Spinner label="searching" /> Searching…
            </>
          ) : (
            'Search'
          )}
        </PressButton>
      </form>

      {error ? <p className="inline-error">{error}</p> : null}

      <AnimatePresence mode="wait">
        {result ? (
          <motion.div
            key={`${result.query}-${result.results.length}`}
            initial={{ opacity: 0, y: reduce ? 0 : 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, transition: { duration: 0.1 } }}
            transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
          >
            <p className="scoped-line">
              Searched {scope?.vendors.length ?? 0} merchants
              {scope?.max_price_cents ? `, under ${money(scope.max_price_cents)} per order` : ''}
            </p>

            {result.results.length ? (
              <ul className="search-results">
                {result.results.map((item, i) => (
                  <motion.li
                    key={`${item.sku ?? item.title}-${i}`}
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
                        {item.category ? <span className="chip chip-quiet">{item.category}</span> : null}
                      </span>
                      <FitFlags item={item} />
                    </span>
                    <span className="result-right">
                      <span className="result-price mono">{money(item.unit_cents)}</span>
                      <FitPill item={item} />
                    </span>
                  </motion.li>
                ))}
              </ul>
            ) : (
              <p className="dry-empty">
                Nothing matched inside these rules. The agent cannot buy what its scope does not
                cover.
              </p>
            )}

            {result.filtered.irrelevant ? (
              <p className="fineprint">
                {result.filtered.irrelevant} unrelated catalogue{' '}
                {result.filtered.irrelevant === 1 ? 'offer was' : 'offers were'} left out.
              </p>
            ) : null}
          </motion.div>
        ) : null}
      </AnimatePresence>
    </section>
  )
}

export function AgentDetail() {
  const { id = '' } = useParams()
  const [agent, setAgent] = useState<Agent | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)
  const reduce = useReducedMotion()
  const m = motionSet(reduce)

  useEffect(() => {
    let live = true
    setAgent(null)
    setError(null)
    api
      .agent(id)
      .then((a) => live && setAgent(a))
      .catch(() => live && setError('That agent does not exist, or it belongs to another workspace.'))
    return () => {
      live = false
    }
  }, [id])

  const save = async (input: AgentInput) => {
    if (!agent) return
    setBusy(true)
    setEditError(null)
    try {
      const updated = await api.updateAgent(agent.id, input)
      setAgent(updated)
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
          <p className="inline-error">{error}</p>
          <Link className="text-link mono" to="/agents">
            ← back to agents
          </Link>
        </section>
      </div>
    )
  }

  if (!agent) {
    return (
      <div className="screen">
        <div className="card">
          <span className="skel" style={{ width: '28%', height: 20 }} />
          <span className="skel" style={{ width: '52%', height: 12, marginTop: 10 }} />
          <span className="skel" style={{ width: '40%', height: 12, marginTop: 8 }} />
        </div>
      </div>
    )
  }

  return (
    <motion.div className="screen" variants={m.stagger} initial="hidden" animate="show">
      <motion.nav className="breadcrumb mono" variants={m.riseIn} aria-label="Breadcrumb">
        <Link to="/agents">Agents</Link> <span aria-hidden="true">/</span> <span>{agent.name}</span>
      </motion.nav>

      <motion.header className="screen-head agent-header" variants={m.riseIn}>
        <div>
          <h1>{agent.name}</h1>
          <div className="agent-meta">
            <span className="chip chip-quiet">{agent.tag}</span>
            {agent.channel ? <span className="mono agent-channel">{agent.channel}</span> : null}
            <span className={`agent-state${agent.status === 'active' ? ' is-live' : ''}`}>
              <span className="agent-dot" aria-hidden="true" />
              {agent.status}
            </span>
          </div>
        </div>
        <PressButton variant={editing ? 'quiet' : 'secondary'} onClick={() => setEditing((v) => !v)}>
          {editing ? 'Close editor' : 'Edit rules'}
        </PressButton>
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
              <h2>Rules for {agent.name}</h2>
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

      <div className="cols cols-tools">
        <motion.div variants={m.riseIn}>
          <DryRun agent={agent} />
        </motion.div>
        <motion.div variants={m.riseIn}>
          <FindProduct agent={agent} />
        </motion.div>
      </div>
    </motion.div>
  )
}
