import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion, useReducedMotion } from 'motion/react'
import { api, money, type Agent } from '../api'
import { motionSet } from '../components/motion'

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
 * "Active" is the stored state, not the whole story: a charter with no approved
 * merchant or no allowed category cannot buy anything, and that has to show in
 * the same slot, not as a footnote at the end of the scope.
 */
function instanceState(agent: Agent, rules: ReturnType<typeof charter>) {
  if (agent.status === 'paused') return { label: 'paused', tone: '' }
  const cannotBuy =
    (rules.categories !== null && rules.categories.length === 0) ||
    (rules.merchants !== null && rules.merchants.length === 0)
  return cannotBuy
    ? { label: "can't buy", tone: ' is-blocked' }
    : { label: 'active', tone: ' is-live' }
}

function Plate({ agent }: { agent: Agent }) {
  const rules = charter(agent)
  const state = instanceState(agent, rules)
  const readout = [
    { label: 'Monthly', value: figure(rules.monthly), fallback: 'No cap', lead: true },
    { label: 'Per order', value: figure(rules.perOrder), fallback: 'No cap', lead: false },
    { label: 'Per item', value: figure(rules.perItem), fallback: 'No cap', lead: false },
    { label: 'Signature above', value: figure(rules.signature), fallback: 'Pays alone', lead: false },
  ]

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
  const exceptionHint =
    'A category with its own cap, overriding the agent default for that category.'

  return (
    <Link className="agent-plate" to={`/agents/${agent.id}`}>
      <div className="agent-plate-tag">
        <span className="mono">{agent.id}</span>
        <span className={`agent-state${state.tone}`}>
          <span className="agent-dot" aria-hidden="true" />
          {state.label}
        </span>
      </div>

      <header className="agent-plate-head">
        <h2>{agent.name}</h2>
        <span className="chip chip-quiet">{agent.tag}</span>
      </header>

      <div className="agent-plate-rules">
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

      <footer className="agent-plate-foot">
        <p className="agent-plate-scope">
          <span>Buys {buys}</span>
          <span className="agent-plate-rest">
            <span
              className={
                rules.merchants !== null && rules.merchants.length === 0 ? 'is-cold' : undefined
              }
            >
              {merchants}
            </span>
            {exceptions ? (
              <span title={exceptionHint}>
                {' '}
                · {exceptions} category exception{exceptions === 1 ? '' : 's'}
              </span>
            ) : null}
          </span>
        </p>
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
      <div className="agent-plate-tag">
        <span className="skel" style={{ width: 78, height: 9 }} />
        <span className="skel" style={{ width: 44, height: 9 }} />
      </div>
      <header className="agent-plate-head">
        <span className="skel" style={{ width: '46%', height: 18 }} />
        <span className="skel" style={{ width: 56, height: 18 }} />
      </header>
      <div className="agent-plate-skel-rules">
        {[0, 1, 2, 3].map((i) => (
          <span className="skel" style={{ height: 28 }} key={i} />
        ))}
      </div>
      <footer className="agent-plate-foot">
        <span className="skel" style={{ width: '58%', height: 10 }} />
        <span className="skel" style={{ width: 10, height: 10 }} />
      </footer>
    </div>
  )
}

/**
 * Agents: each one is its own plate, not a row in a shared table. The plate carries
 * everything that defines the instance — its serial, who it is, the four numbers of
 * its charter, and the scope it buys inside. The detail screen spends them.
 */
export function Agents() {
  const [agents, setAgents] = useState<Agent[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const reduce = useReducedMotion()
  const m = motionSet(reduce)

  useEffect(() => {
    let live = true
    api
      .agents()
      .then((list) => live && setAgents(list))
      .catch(() => live && setError('The agent list did not load.'))
    return () => {
      live = false
    }
  }, [])

  return (
    <motion.div className="screen" variants={m.stagger} initial="hidden" animate="show">
      <motion.header className="screen-head" variants={m.riseIn}>
        <div>
          <h1>Agents</h1>
          <p>The agents that can spend, and the rules each one carries.</p>
        </div>
        <div className="head-actions">
          {agents ? (
            <span className="head-count">
              {agents.length} agent{agents.length === 1 ? '' : 's'}
            </span>
          ) : null}
          <Link className="btn btn-primary" to="/agents/new">
            New agent
          </Link>
        </div>
      </motion.header>

      {error ? <p className="inline-error">{error}</p> : null}

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
        <div className="agent-grid">
          <div className="agent-plate agent-plate-empty">
            <span className="empty-mark" aria-hidden="true" />
            <p className="empty-title">No agents yet.</p>
            <p className="empty-note">
              An agent is a team&rsquo;s spending charter: its budget, its merchants, its
              categories and the point where a person has to say yes.
            </p>
          </div>
        </div>
      ) : null}

      <motion.p className="agents-note" variants={m.riseIn}>
        Inside its rules an agent pays silently; outside them a person signs.
      </motion.p>
    </motion.div>
  )
}
