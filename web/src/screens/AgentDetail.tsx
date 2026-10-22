import { useEffect, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import {
  api,
  ApiError,
  money,
  type Agent,
  type AgentInput,
  type ScopedSearch,
  type SimulateResult,
} from '../api'
import { AgentForm } from '../components/AgentForm'
import { OutcomePill, Track } from '../components/Bits'
import { PressButton, Spinner } from '../components/PressButton'
import { motionSet } from '../components/motion'

function defaultCart(agent: Agent): string {
  if (agent.categories.includes('equipment')) return '2 anti-fatigue standing mats for the studio'
  if (agent.categories.includes('pantry')) return '2 packs of bamboo toilet paper'
  if (agent.categories.includes('meals')) return 'lunch for eight, client visit'
  if (agent.categories.includes('stationery')) return 'whiteboard markers, 4 pack'
  return 'a standing mat for the desk'
}

function defaultQuery(agent: Agent): string {
  if (agent.categories.includes('equipment')) return 'anti-fatigue standing mat'
  if (agent.categories.includes('pantry')) return 'bamboo toilet paper'
  if (agent.categories.includes('meals')) return 'team lunch catering'
  if (agent.categories.includes('stationery')) return 'whiteboard markers'
  return 'standing mat'
}

function Limit({ label, value }: { label: string; value: string }) {
  return (
    <div className="limit">
      <p className="limit-label">{label}</p>
      <p className="limit-value mono">{value}</p>
    </div>
  )
}

function RuleRows({ agent }: { agent: Agent }) {
  const rows: { rule: string; scope: string; value: string }[] = []
  if (agent.monthly_cap_cents !== null)
    rows.push({ rule: 'monthly_cap', scope: 'agent', value: money(agent.monthly_cap_cents) })
  if (agent.per_order_cap_cents !== null)
    rows.push({ rule: 'per_order_cap', scope: 'agent', value: money(agent.per_order_cap_cents) })
  if (agent.per_unit_cap_cents !== null)
    rows.push({ rule: 'per_unit_cap', scope: 'agent', value: money(agent.per_unit_cap_cents) })
  if (agent.approval_threshold_cents !== null)
    rows.push({
      rule: 'approval_threshold',
      scope: 'agent',
      value: money(agent.approval_threshold_cents),
    })
  for (const override of agent.overrides) {
    rows.push({
      rule: override.kind,
      scope: `${override.category} · exception`,
      value: money(override.value_cents),
    })
  }
  rows.push({ rule: 'vendor_allowlist', scope: 'agent', value: `${agent.vendors.length} merchants` })
  rows.push({
    rule: 'category_allowlist',
    scope: 'agent',
    value: agent.categories.join(', ') || 'none',
  })
  return (
    <div className="rule-rows">
      {rows.map((row) => (
        <div className="rule-row" key={`${row.rule}-${row.scope}`}>
          <span className="mono rule-kind">{row.rule}</span>
          <span className="rule-scope">{row.scope}</span>
          <span className="mono rule-value">{row.value}</span>
        </div>
      ))}
    </div>
  )
}

function DryRun({ agent }: { agent: Agent }) {
  const [text, setText] = useState(() => defaultCart(agent))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<SimulateResult | null>(null)

  const run = async (event: FormEvent) => {
    event.preventDefault()
    if (!text.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      setResult(await api.simulate(text, agent.id))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The dry run failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card">
      <header className="card-head">
        <h2>Try a cart</h2>
        <span className="card-note mono">dry run</span>
      </header>
      <p className="card-lede">
        The real pipeline — extraction, catalogue, rules — stops before consent. Nothing is
        ordered and no money moves.
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
              <Spinner label="pricing" /> Pricing…
            </>
          ) : (
            'Run'
          )}
        </PressButton>
      </form>

      {error ? <p className="inline-error">{error}</p> : null}

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
              <span className="dry-total mono">{money(result.total_cents)}</span>
              <span className="dry-meta mono">
                {result.source} · {Math.round(result.elapsed_ms)}ms
              </span>
            </div>

            {result.lines.length ? (
              <ul className="cart-lines">
                {result.lines.map((line) => (
                  <li key={`${line.sku ?? line.title}-${line.qty}`}>
                    <span className="cart-qty mono">{line.qty}×</span>
                    <span className="cart-title">{line.title}</span>
                    <span className="mono cart-vendor">{line.vendor ?? 'unknown'}</span>
                    <span className="mono cart-money">{money(line.line_cents)}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="fineprint">Nothing could be priced for this message.</p>
            )}

            {result.decision.reasons.length ? (
              <ul className="reason-list">
                {result.decision.reasons.map((reason, i) => (
                  <li key={`${reason.rule}-${i}`}>
                    <span className={`rule-flag ${reason.severity === 'block' ? 'is-neg' : 'is-wait'}`}>
                      {reason.rule}
                    </span>
                    <span className="reason-text">{reason.message}</span>
                    {reason.unblock ? <span className="reason-unblock">{reason.unblock}</span> : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="fineprint">Inside every rule — in a real request this would pay with no human.</p>
            )}

            <ol className="mini-rail">
              {result.events.map((event, i) => (
                <li key={`${event.kind}-${i}`}>
                  <span className="mini-dot" aria-hidden="true" />
                  <span className="mini-label">{event.label}</span>
                  {event.detail ? <span className="mini-detail mono">{event.detail}</span> : null}
                </li>
              ))}
            </ol>

            <p className="fineprint">Dry run: no order, no consent, no money moved.</p>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </section>
  )
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

  return (
    <section className="card">
      <header className="card-head">
        <h2>Find a product</h2>
        <span className="card-note mono">inside this agent&rsquo;s rules</span>
      </header>
      <p className="card-lede">
        The agent can only find what it is allowed to buy: its merchants, its categories, under
        its caps. Results outside those lines are shown as fitting or not.
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
            <p className="scoped-line mono">
              searched {result.scoped_by.vendors.length ? result.scoped_by.vendors.join(', ') : 'no merchants'}
              {result.scoped_by.max_price_cents
                ? ` · up to ${money(result.scoped_by.max_price_cents)} per order`
                : ''}
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
                      <span className="fit-flags">
                        {!item.fits.category ? <span className="fit-bad">category blocked</span> : null}
                        {!item.fits.per_unit ? <span className="fit-bad">over per-line cap</span> : null}
                        {!item.fits.per_order ? <span className="fit-bad">over per-order cap</span> : null}
                        {!item.fits.threshold ? (
                          <span className="fit-warn">would need a signature</span>
                        ) : null}
                        {item.fits.category && item.fits.per_unit && item.fits.per_order && item.fits.threshold ? (
                          <span className="fit-good">fits every rule</span>
                        ) : null}
                      </span>
                    </span>
                    <span className="result-right">
                      <span className="mono result-price">{money(item.unit_cents)}</span>
                      <OutcomePill outcome={item.verdict} />
                    </span>
                  </motion.li>
                ))}
              </ul>
            ) : (
              <p className="fineprint">
                Nothing matched inside these rules. That is the point: the agent cannot buy it.
              </p>
            )}

            {result.filtered.irrelevant || result.filtered.category ? (
              <p className="fineprint mono">
                {result.filtered.irrelevant} dropped as irrelevant · {result.filtered.category} outside
                the agent&rsquo;s categories
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

      <motion.header className="screen-head" variants={m.riseIn}>
        <div className="agent-head">
          <h1>{agent.name}</h1>
          <span className="chip chip-quiet">{agent.tag}</span>
          {agent.channel ? <span className="mono agent-channel">{agent.channel}</span> : null}
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
              <span className="card-note mono">guided</span>
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

      <div className="cols">
        <motion.section className="card" variants={m.riseIn}>
          <header className="card-head">
            <h2>Budget</h2>
            <span className="card-note mono">
              {agent.monthly_cap_cents ? `${money(agent.monthly_cap_cents)} / month` : 'no monthly cap'}
            </span>
          </header>
          <Track spentCents={0} committedCents={0} capCents={agent.monthly_cap_cents} />

          <div className="limit-grid">
            <Limit
              label="Per order"
              value={agent.per_order_cap_cents !== null ? money(agent.per_order_cap_cents) : '—'}
            />
            <Limit
              label="Per item"
              value={agent.per_unit_cap_cents !== null ? money(agent.per_unit_cap_cents) : '—'}
            />
            <Limit
              label="Ask above"
              value={
                agent.approval_threshold_cents !== null
                  ? money(agent.approval_threshold_cents)
                  : '—'
              }
            />
            <Limit label="Merchants" value={String(agent.vendors.length)} />
          </div>

          <p className="fineprint">
            Spent and unsigned figures fill from requests in the next milestone; the caps and
            allowlists on this screen are already live.
          </p>
        </motion.section>

        <motion.div variants={m.riseIn}>
          <DryRun agent={agent} />
        </motion.div>
      </div>

      <div className="cols cols-policy">
        <motion.section className="card" variants={m.riseIn}>
          <header className="card-head">
            <h2>Policy</h2>
            <span className="card-note mono">read-only · the actual rows</span>
          </header>
          <RuleRows agent={agent} />
          <div className="chips-row">
            {agent.categories.map((category) => (
              <span className="chip" key={category}>
                {category}
              </span>
            ))}
            {agent.vendors.map((vendor) => (
              <span className="chip mono" key={vendor}>
                {vendor}
              </span>
            ))}
          </div>
        </motion.section>

        <motion.div variants={m.riseIn}>
          <FindProduct agent={agent} />
        </motion.div>
      </div>
    </motion.div>
  )
}
