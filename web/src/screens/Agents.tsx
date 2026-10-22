import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion, useReducedMotion } from 'motion/react'
import { api, money, type Agent } from '../api'
import { motionSet } from '../components/motion'

function capsSummary(agent: Agent): string {
  const parts: string[] = []
  if (agent.per_order_cap_cents !== null) parts.push(`${money(agent.per_order_cap_cents)} / order`)
  if (agent.per_unit_cap_cents !== null) parts.push(`${money(agent.per_unit_cap_cents)} / line`)
  if (agent.approval_threshold_cents !== null)
    parts.push(`ask above ${money(agent.approval_threshold_cents)}`)
  return parts.join(' · ') || 'no caps set'
}

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
          <p>The groups that can spend, and the rules each one carries.</p>
        </div>
        <Link className="btn btn-primary" to="/agents/new">
          New agent
        </Link>
      </motion.header>

      <div className="cols cols-wide-left">
        <motion.section className="card" variants={m.riseIn}>
          <header className="card-head">
            <h2>All agents</h2>
            <span className="card-note mono">{agents ? `${agents.length} total` : 'loading'}</span>
          </header>

          {error ? <p className="inline-error">{error}</p> : null}

          {!agents && !error ? (
            <div className="rows">
              {[0, 1, 2].map((i) => (
                <div className="row row-skel" key={i}>
                  <span className="skel" style={{ width: '30%', height: 12 }} />
                  <span className="skel" style={{ width: '56%', height: 10 }} />
                </div>
              ))}
            </div>
          ) : null}

          {agents ? (
            <ul className="rows rows-agent">
              {agents.map((agent) => (
                <li key={agent.id}>
                  <Link className="agent-link" to={`/agents/${agent.id}`}>
                    <div className="agent-link-top">
                      <b>{agent.name}</b>
                      <span className="chip chip-quiet">{agent.tag}</span>
                      {agent.channel ? (
                        <span className="mono agent-channel">{agent.channel}</span>
                      ) : null}
                      <span className="agent-arrow" aria-hidden="true">
                        →
                      </span>
                    </div>
                    <p className="agent-caps mono">{capsSummary(agent)}</p>
                    <p className="agent-scan">
                      {agent.vendors.length} approved{' '}
                      {agent.vendors.length === 1 ? 'merchant' : 'merchants'}
                      {' · '}
                      {agent.categories.join(', ') || 'no categories'}
                      {agent.overrides.length
                        ? ` · ${agent.overrides.length} category exception${agent.overrides.length > 1 ? 's' : ''}`
                        : ''}
                    </p>
                  </Link>
                </li>
              ))}
            </ul>
          ) : null}
        </motion.section>

        <motion.section className="card card-soft" variants={m.riseIn}>
          <header className="card-head">
            <h2>How an agent works</h2>
          </header>
          <ul className="checklist quiet">
            <li>It searches only among its approved merchants, under its own caps.</li>
            <li>Every cart is judged before money moves; a dry run shows the verdict without ordering.</li>
            <li>Inside the rules it pays silently; outside them a human signs.</li>
            <li>Its tag is what rolls the spend up in analytics.</li>
          </ul>
          <Link className="text-link mono" to="/agents/new">
            create an agent →
          </Link>
        </motion.section>
      </div>
    </motion.div>
  )
}
