import { useEffect, useState } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { Link } from 'react-router-dom'
import { api, capMoney, money, type Agent, type OverviewData } from '../api'
import { InlineError } from '../components/Bits'
import { FreshnessButton, useFreshness } from '../components/freshness'
import { motionSet } from '../components/motion'
import { requestLabel, when } from '../components/request'

function Stat({
  label,
  value,
  note,
  tone,
}: {
  label: string
  value: string
  note: string
  tone?: 'pos' | 'wait'
}) {
  return (
    <div className="stat">
      <p className="stat-label">{label}</p>
      <p className={`stat-value${tone ? ` is-${tone}` : ''}`}>{value}</p>
      <p className="stat-note">{note}</p>
    </div>
  )
}

function capsLine(agent: Agent): string {
  const parts: string[] = []
  if (agent.per_order_cap_cents !== null)
    parts.push(`${capMoney(agent.per_order_cap_cents)} / order`)
  if (agent.per_unit_cap_cents !== null)
    parts.push(`${capMoney(agent.per_unit_cap_cents)} / item`)
  if (agent.approval_threshold_cents !== null)
    parts.push(`signature above ${capMoney(agent.approval_threshold_cents)}`)
  return parts.join(' · ')
}

/**
 * Overview: what needs a human, and where the money is. No charts, no invented
 * history — only facts the engine can stand behind, all read from the store.
 */
export function Overview() {
  const [agents, setAgents] = useState<Agent[] | null>(null)
  const [overview, setOverview] = useState<OverviewData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [overviewError, setOverviewError] = useState<string | null>(null)
  const [reloadTick, setReloadTick] = useState(0)
  const { tick, markLoaded } = useFreshness()
  const reduce = useReducedMotion()
  const m = motionSet(reduce)

  useEffect(() => {
    let live = true
    setError(null)
    setOverviewError(null)
    api
      .agents()
      .then((list) => {
        if (live) setAgents(list)
      })
      .catch(() => {
        if (live) setError('The agent list did not load.')
      })
    api
      .overview()
      .then((data) => {
        if (!live) return
        setOverview(data)
        markLoaded()
      })
      .catch(() => {
        if (live) setOverviewError('The live figures did not load.')
      })
    return () => {
      live = false
    }
  }, [reloadTick, tick, markLoaded])

  const counts = overview?.counts
  const pending = overview?.pending ?? []
  const expired = overview?.expired ?? []
  const expiredCount = counts?.expired_consents ?? expired.length
  const action =
    counts && counts.needs_you > 0
      ? { to: '/inbox?status=awaiting', label: 'Review requests' }
      : expiredCount > 0
        ? { to: '/inbox?status=expired', label: 'Review expired' }
        : { to: '/inbox', label: 'Open the inbox' }

  return (
    <motion.div className="screen" variants={m.stagger} initial="hidden" animate="show">
      <motion.header className="screen-head" variants={m.riseIn}>
        <div>
          <h1>Overview</h1>
          <p>What needs you, and where the money is.</p>
        </div>
        <div className="head-tools">
          <FreshnessButton />
          <Link className="btn btn-secondary" to={action.to}>
            {action.label}
          </Link>
        </div>
      </motion.header>

      {overviewError ? (
        <InlineError onRetry={() => setReloadTick((value) => value + 1)}>
          {overviewError}
        </InlineError>
      ) : null}

      <motion.div className="stats" variants={m.riseIn}>
        <Stat
          label="Needs you"
          value={counts ? String(counts.needs_you) : '—'}
          note={
            expiredCount > 0
              ? `${expiredCount} consent${expiredCount === 1 ? '' : 's'} expired — send again`
              : 'requests waiting for a signature'
          }
          tone={counts && counts.needs_you > 0 ? 'wait' : undefined}
        />
        <Stat
          label="Held for a signature"
          value={counts ? money(counts.committed_cents) : '—'}
          note="money waiting on an approver"
          tone={counts && counts.committed_cents > 0 ? 'wait' : undefined}
        />
        <Stat
          label="Settled this month"
          value={counts ? money(counts.settled_month_cents) : '—'}
          note="captured through PayPal"
          tone={counts && counts.settled_month_cents > 0 ? 'pos' : undefined}
        />
        <Stat
          label="Left this month"
          value={overview ? money(overview.remaining_cents) : '—'}
          note={
            overview
              ? `${money(overview.counts.settled_month_cents + overview.counts.committed_cents)} of ${money(overview.budget_cents)} committed`
              : ' '
          }
          tone={
            overview && overview.remaining_cents === 0 && overview.budget_cents > 0
              ? 'wait'
              : undefined
          }
        />
      </motion.div>

      <div className="cols">
        <motion.section className="card" variants={m.riseIn}>
          <header className="card-head">
            <h2>Needs you</h2>
            <Link className="text-link mono" to="/inbox?status=awaiting">
              open inbox →
            </Link>
          </header>
          {pending.length ? (
            <ul className="rows rows-pending">
              {pending.map((request) => (
                <li key={request.id}>
                  <Link className="pending-link" to={`/inbox/${request.id}`}>
                    <span className="pending-main">
                      <b>{request.title}</b>
                      <span className="pending-agent mono">
                        {request.agent.name} · {request.line_count}{' '}
                        {request.line_count === 1 ? 'line' : 'lines'}
                      </span>
                    </span>
                    <span className="pending-side">
                      <span className="pending-money">
                        {money(request.total_cents, request.currency)}
                      </span>
                      <span className="pending-meta mono">
                        {requestLabel(request.seq)} · {when(request.created_at)}
                      </span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <div className="empty">
              <p className="empty-title">Nothing is waiting.</p>
              <p className="empty-note">
                A request lands here when a cart crosses the agent&rsquo;s approval threshold or
                its per-line cap. Inside the rules, no one is asked.
              </p>
            </div>
          )}
          {expired.length ? (
            <p className="card-footnote">
              {expired.length === 1
                ? `${expired[0]?.id ?? 'A request'} expired before it was signed — send it again from the record.`
                : `${expired.length} requests expired before they were signed — send them again.`}{' '}
              <Link className="text-link mono" to="/inbox?status=expired">
                see expired →
              </Link>
            </p>
          ) : null}
        </motion.section>

        <motion.section className="card" variants={m.riseIn}>
          <header className="card-head">
            <h2>Agents</h2>
            <Link className="text-link mono" to="/agents">
              manage →
            </Link>
          </header>

          {error ? (
            <InlineError onRetry={() => setReloadTick((value) => value + 1)}>{error}</InlineError>
          ) : null}

          {!agents && !error ? (
            <div className="rows">
              {[0, 1, 2].map((i) => (
                <div className="row row-skel" key={i}>
                  <span className="skel" style={{ width: '34%', height: 12 }} />
                  <span className="skel" style={{ width: '52%', height: 10 }} />
                </div>
              ))}
            </div>
          ) : null}

          {agents ? (
            <ul className="rows">
              {agents.map((agent) => (
                <li className="row agent-row" key={agent.id}>
                  <div className="agent-line">
                    <b>{agent.name}</b>
                    <span className="chip chip-quiet">{agent.tag}</span>
                    {agent.channel ? (
                      <span className="mono agent-channel">{agent.channel}</span>
                    ) : null}
                  </div>
                  <p className="agent-caps mono">{capsLine(agent)}</p>
                  <p className="agent-scan">
                    {agent.vendors.length} approved{' '}
                    {agent.vendors.length === 1 ? 'merchant' : 'merchants'}
                    {' · '}
                    {agent.categories.join(', ')}
                  </p>
                </li>
              ))}
            </ul>
          ) : null}
        </motion.section>
      </div>
    </motion.div>
  )
}
