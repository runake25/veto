import { useEffect, useMemo, useState } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { Link } from 'react-router-dom'
import { api, capMoney, money, type Agent, type OverviewData, type RequestSummary } from '../api'
import { AgentAvatar } from '../components/AgentAvatar'
import { InlineError } from '../components/Bits'
import { FreshnessButton, useFreshness } from '../components/freshness'
import { motionSet } from '../components/motion'
import { stateGroupHref, stateGroupLabel } from '../components/state'
import { age, requestLabel, whenFull } from '../components/request'

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

function monthLabel(month: string | undefined): string | null {
  if (!month) return null
  const date = new Date(month)
  if (Number.isNaN(date.getTime())) return null
  return new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric' }).format(date)
}

/**
 * One request waiting on a person. Title and money lead; the agent that raised
 * it, its age and the way in are quiet.
 */
function NeedRow({ request, agent }: { request: RequestSummary; agent?: Agent }) {
  return (
    <li>
      <Link className="ov-need" to={`/inbox/${request.id}`} title={request.title}>
        {agent ? (
          <AgentAvatar agent={agent} size={30} />
        ) : (
          <span className="ov-need-avatar" aria-hidden="true" />
        )}
        <span className="ov-need-main">
          <b>{request.title}</b>
          <span className="ov-need-agent">
            {request.agent.name} · {requestLabel(request.seq)}
          </span>
        </span>
        <span className="ov-need-amount">{money(request.total_cents, request.currency)}</span>
        <span className="ov-need-age mono" title={whenFull(request.created_at)}>
          {age(request.created_at)}
        </span>
        <span className="ov-need-open">Open</span>
      </Link>
    </li>
  )
}

/**
 * Overview: the decision first, the money second. A person lands here to sign
 * something; the standing figures and the fleet are quiet support.
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
  const needsCount = counts?.needs_you ?? 0
  const action =
    needsCount > 0
      ? {
          to: stateGroupHref('needs'),
          label: `Review ${needsCount} request${needsCount === 1 ? '' : 's'}`,
        }
      : { to: stateGroupHref(''), label: 'Open inbox' }

  const byId = useMemo(() => {
    const map = new Map<string, Agent>()
    for (const agent of agents ?? []) map.set(agent.id, agent)
    return map
  }, [agents])

  const month = monthLabel(overview?.month)
  const settled = counts?.settled_month_cents ?? 0
  const committed = counts?.committed_cents ?? 0
  const used = settled + committed
  const budget = overview?.budget_cents ?? 0
  const usedPct = budget > 0 ? Math.min(100, Math.round((used / budget) * 100)) : 0

  const lapsedRequest = expired[0]
  const lapsedLine =
    expiredCount > 0 ? (
      expiredCount === 1 && lapsedRequest ? (
        <p className="ov-lapsed">
          Consent lapsed on {requestLabel(lapsedRequest.seq)} before it was signed —{' '}
          <Link to={`/inbox/${lapsedRequest.id}`}>send it again from the record</Link>.
        </p>
      ) : (
        <p className="ov-lapsed">
          {expiredCount} consents lapsed before signing —{' '}
          <Link to={stateGroupHref('needs')}>send them again from their records</Link>.
        </p>
      )
    ) : null

  return (
    <motion.div className="screen" variants={m.stagger} initial="hidden" animate="show">
      <motion.header className="screen-head" variants={m.riseIn}>
        <div>
          <h1>Overview</h1>
          <p>What needs your signature, and where the money stands.</p>
        </div>
        <div className="head-tools">
          <FreshnessButton />
          <Link className="btn btn-primary" to={action.to}>
            {action.label}
          </Link>
        </div>
      </motion.header>

      {overviewError ? (
        <InlineError onRetry={() => setReloadTick((value) => value + 1)}>
          {overviewError}
        </InlineError>
      ) : null}

      <motion.section className="ov-needs" variants={m.riseIn}>
        <header className="ov-panel-head">
          <h2>{stateGroupLabel('needs')}</h2>
          {overview && pending.length ? (
            <Link className="text-link" to={stateGroupHref('needs')}>
              open inbox →
            </Link>
          ) : null}
        </header>

        {!overview && !overviewError ? (
          <ul className="ov-needs-rows" aria-hidden="true">
            {[0, 1, 2].map((i) => (
              <li className="ov-need ov-need-skel" key={i}>
                <span className="ov-need-avatar skel" />
                <span className="ov-need-main">
                  <span className="skel" style={{ width: '54%', height: 12 }} />
                  <span className="skel" style={{ width: '32%', height: 9 }} />
                </span>
                <span className="skel" style={{ width: 58, height: 11 }} />
              </li>
            ))}
          </ul>
        ) : null}

        {overview && pending.length ? (
          <ul className="ov-needs-rows">
            {pending.map((request) => (
              <NeedRow key={request.id} request={request} agent={byId.get(request.agent.id)} />
            ))}
          </ul>
        ) : null}

        {overview && !pending.length && !expiredCount ? (
          <p className="empty-title ov-needs-empty">Nothing is waiting.</p>
        ) : null}

        {overview ? lapsedLine : null}
      </motion.section>

      <motion.section className="ov-month" variants={m.riseIn}>
        <div className="ov-month-head">
          <h2>This month</h2>
          {month ? <span className="ov-month-name">{month}</span> : null}
          <Link className="text-link ov-month-link" to="/analytics">
            analytics →
          </Link>
        </div>
        <dl className="ov-figures">
          <div>
            <dt>Held for signature</dt>
            <dd className="ov-figure is-hold">{overview ? money(committed) : '—'}</dd>
          </div>
          <div>
            <dt>Settled</dt>
            <dd className="ov-figure is-pos">{overview ? money(settled) : '—'}</dd>
          </div>
          <div>
            <dt>Left</dt>
            <dd className="ov-figure">{overview ? money(overview.remaining_cents) : '—'}</dd>
          </div>
        </dl>
        {overview && budget > 0 ? (
          <div className="ov-budget">
            <div
              className="ov-budget-bar"
              role="img"
              aria-label={`${usedPct}% of the ${money(budget)} monthly budget committed`}
            >
              <span style={{ width: `${usedPct}%` }} />
            </div>
            <span className="ov-budget-note mono">
              {money(used)} of {money(budget)} committed
            </span>
          </div>
        ) : null}
      </motion.section>

      <motion.section className="ov-agents" variants={m.riseIn}>
        <header className="ov-panel-head">
          <h2>Agents</h2>
          <Link className="text-link" to="/agents">
            manage →
          </Link>
        </header>

        {error ? (
          <InlineError onRetry={() => setReloadTick((value) => value + 1)}>{error}</InlineError>
        ) : null}

        {!agents && !error ? (
          <ul className="ov-agent-rows" aria-hidden="true">
            {[0, 1, 2].map((i) => (
              <li className="ov-agent" key={i}>
                <span className="ov-agent-skel skel" />
                <span className="skel" style={{ width: '24%', height: 11 }} />
                <span className="skel ov-agent-caps" style={{ width: '40%', height: 9 }} />
              </li>
            ))}
          </ul>
        ) : null}

        {agents ? (
          <ul className="ov-agent-rows">
            {agents.map((agent) => (
              <li className="ov-agent" key={agent.id}>
                <AgentAvatar agent={agent} size={28} />
                <b className="ov-agent-name">{agent.name}</b>
                <span className="ov-agent-caps mono">{capsLine(agent) || 'No caps set'}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </motion.section>
    </motion.div>
  )
}
