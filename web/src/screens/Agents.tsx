import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion, useReducedMotion } from 'motion/react'
import { api, money, type Agent } from '../api'
import { AgentAvatar } from '../components/AgentAvatar'
import { InlineError } from '../components/Bits'
import { FreshnessButton, useFreshness } from '../components/freshness'
import { motionSet } from '../components/motion'
import { when } from '../components/request'
import { State, agentState } from '../components/state'

/** The price as short as it stays exact: $300, $1,200, $145.99. */
function figure(cents: number | null): string | null {
  if (cents === null) return null
  return money(cents).replace(/\.00$/, '')
}

/** The rules this agent actually carries. A value counts only when its rule is on. */
function charter(agent: Agent) {
  const on = new Set(agent.rules)
  return {
    monthly: on.has('monthly_cap') ? agent.monthly_cap_cents : null,
    perOrder: on.has('per_order_cap') ? agent.per_order_cap_cents : null,
    perItem: on.has('per_unit_cap') ? agent.per_unit_cap_cents : null,
    signature: on.has('approval_threshold') ? agent.approval_threshold_cents : null,
    categories: on.has('category_allowlist') ? agent.categories : null,
    merchants: on.has('vendor_allowlist') ? agent.vendors : null,
  }
}

/**
 * The standing job in one line: what it orders, how often, when it next runs.
 * When the agent owns several jobs the line leads with the count and the
 * soonest active slot; the cards themselves live on the agent page.
 * A paused job, or a job under a paused bot, never promises a next run.
 */
function JobLine({ agent }: { agent: Agent }) {
  const job = agent.routine
  const count = agent.routine_count ?? (job ? 1 : 0)
  if (!job || count === 0) return <span className="plate-muted">No standing job</span>
  const paused = job.status === 'paused' || agent.status !== 'active'
  if (count > 1) {
    return (
      <>
        <span className="plate-job-name">
          {count} standing jobs
        </span>
        {paused ? (
          <span className="plate-muted"> · paused</span>
        ) : job.next_run_at ? (
          <span className="mono plate-job-next">
            {' '}
            · next {when(job.next_run_at).split(' · ')[0]}
            <span className="plate-job-time"> · {when(job.next_run_at).split(' · ')[1]}</span>
          </span>
        ) : null}
      </>
    )
  }
  return (
    <>
      <span className="plate-job-name">{job.name}</span>
      <span className="plate-muted plate-job-cadence">· {job.cadence_word}</span>
      {paused ? (
        <span className="plate-muted"> · paused</span>
      ) : job.next_run_at ? (
        <span className="mono plate-job-next">
          {' '}
          · next {when(job.next_run_at).split(' · ')[0]}
          <span className="plate-job-time"> · {when(job.next_run_at).split(' · ')[1]}</span>
        </span>
      ) : null}
    </>
  )
}

/** The scope this instance may buy inside, as a person reads it. */
function scope(agent: Agent) {
  const rules = charter(agent)
  const buys =
    rules.categories === null
      ? 'any category'
      : rules.categories.length
        ? rules.categories.join(', ')
        : 'nothing yet'
  const merchants =
    rules.merchants === null
      ? 'any merchant'
      : rules.merchants.length
        ? `${rules.merchants.length} ${rules.merchants.length === 1 ? 'merchant' : 'merchants'}`
        : 'no merchants yet'
  const exceptions = agent.overrides.length
  return { buys, merchants, exceptions, cold: rules.merchants !== null && rules.merchants.length === 0 }
}

function Plate({ agent }: { agent: Agent }) {
  const rules = charter(agent)
  const state = agentState(agent)
  const readout = [
    { label: 'Monthly', value: figure(rules.monthly), fallback: 'No cap', lead: true },
    { label: 'Per order', value: figure(rules.perOrder), fallback: 'No cap', lead: false },
    { label: 'Per item', value: figure(rules.perItem), fallback: 'No cap', lead: false },
    { label: 'Signature above', value: figure(rules.signature), fallback: 'Pays alone', lead: false },
  ]
  const { buys, merchants, exceptions, cold } = scope(agent)

  return (
    <Link className="agent-plate" to={`/agents/${agent.id}`}>
      <header className="plate-ident">
        <AgentAvatar agent={agent} size={44} />
        <div className="plate-ident-text">
          <h2>{agent.name}</h2>
          <p className="plate-serial">
            <span className="mono plate-serial-id">{agent.id}</span>
            {agent.channel ? <span className="mono plate-channel">{agent.channel}</span> : null}
          </p>
        </div>
        <State tone={state.tone} className="plate-state">
          {state.label}
        </State>
      </header>

      <div className="agent-plate-rules plate-charter">
        {readout.map((cell) => (
          <div className="agent-plate-rule" key={cell.label}>
            <span className="agent-plate-label">{cell.label}</span>
            <span
              className={`agent-plate-value${cell.value ? '' : ' is-void'}${
                cell.lead ? ' is-lead' : ''
              }`}
            >
              {cell.value ?? cell.fallback}
            </span>
          </div>
        ))}
      </div>

      <footer className="plate-foot">
        <dl className="plate-facts">
          <div className="plate-fact">
            <dt>Job</dt>
            <dd>
              <JobLine agent={agent} />
            </dd>
          </div>
          <div className="plate-fact">
            <dt>Scope</dt>
            <dd>
              <span>Buys {buys}</span>
              <span> · </span>
              <span className={cold ? 'plate-muted' : undefined}>{merchants}</span>
              {exceptions ? (
                <span className="plate-muted">
                  {' '}
                  · {exceptions} category exception{exceptions === 1 ? '' : 's'}
                </span>
              ) : null}
            </dd>
          </div>
        </dl>
        <span className="agent-plate-arrow" aria-hidden="true">
          →
        </span>
      </footer>
    </Link>
  )
}

function PlateSkeleton() {
  return (
    <div className="agent-plate agent-plate-skel" aria-hidden="true">
      <header className="plate-ident">
        <span className="skel agent-avatar-skel" />
        <div className="plate-ident-text">
          <span className="skel" style={{ width: '54%', height: 17 }} />
          <span className="skel" style={{ width: '72%', height: 9, marginTop: 8 }} />
        </div>
        <span className="skel" style={{ width: 52, height: 11 }} />
      </header>
      <div className="agent-plate-rules plate-charter">
        {[0, 1, 2, 3].map((i) => (
          <span className="skel" style={{ height: 34 }} key={i} />
        ))}
      </div>
      <footer className="plate-foot">
        <span className="skel" style={{ width: '62%', height: 10 }} />
        <span className="skel" style={{ width: 10, height: 10 }} />
      </footer>
    </div>
  )
}

/**
 * Agents: the fleet of running instances. Each plate carries the identity of one
 * bot — its mark, serial, state — the four numbers of its charter, and the job it
 * runs. The detail screen spends them.
 */
export function Agents() {
  const [agents, setAgents] = useState<Agent[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reloadTick, setReloadTick] = useState(0)
  const { tick, markLoaded } = useFreshness()
  const reduce = useReducedMotion()
  const m = motionSet(reduce)

  useEffect(() => {
    let live = true
    setError(null)
    api
      .agents()
      .then((list) => {
        if (!live) return
        setAgents(list)
        markLoaded()
      })
      .catch(() => {
        if (live) setError('The agent list did not load.')
      })
    return () => {
      live = false
    }
  }, [tick, reloadTick, markLoaded])

  return (
    <motion.div className="screen" variants={m.stagger} initial="hidden" animate="show">
      <motion.header className="screen-head" variants={m.riseIn}>
        <div>
          <h1>Agents</h1>
          <p>The bots that can spend, and the charters they run inside.</p>
        </div>
        <div className="head-actions">
          {agents ? (
            <span className="head-count">
              {agents.length} instance{agents.length === 1 ? '' : 's'}
            </span>
          ) : null}
          <FreshnessButton />
          <Link className="btn btn-primary" to="/agents/new">
            New agent
          </Link>
        </div>
      </motion.header>

      {error ? (
        <InlineError onRetry={() => setReloadTick((value) => value + 1)}>{error}</InlineError>
      ) : null}

      {!agents && !error ? (
        <div className="agent-grid">
          <PlateSkeleton />
          <PlateSkeleton />
          <PlateSkeleton />
          <PlateSkeleton />
        </div>
      ) : null}

      {agents && agents.length > 0 ? (
        <motion.div className="agent-grid" variants={m.stagger}>
          {agents.map((agent) => (
            <motion.div className="agent-slot" variants={m.riseIn} key={agent.id}>
              <Plate agent={agent} />
            </motion.div>
          ))}
        </motion.div>
      ) : null}

      {agents && agents.length === 0 ? (
        <div className="fleet-empty">
          <p className="empty-title">No agents yet.</p>
          <p className="empty-note">Create one to give a bot a budget and a scope.</p>
        </div>
      ) : null}
    </motion.div>
  )
}
