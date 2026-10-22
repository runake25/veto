import { useEffect, useState } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { api, money, type Agent, type Me } from '../api'
import { motionSet } from '../components/motion'

interface StatProps {
  label: string
  value: string
  note: string
  tone?: 'accent'
}

function Stat({ label, value, note, tone }: StatProps) {
  return (
    <div className="stat">
      <p className="stat-label">{label}</p>
      <p className={`stat-value${tone === 'accent' ? ' is-accent' : ''}`}>{value}</p>
      <p className="stat-note">{note}</p>
    </div>
  )
}

function capsLine(agent: Agent): string {
  const parts: string[] = []
  if (agent.per_order_cap_cents !== null) parts.push(`${money(agent.per_order_cap_cents)} / order`)
  if (agent.per_unit_cap_cents !== null) parts.push(`${money(agent.per_unit_cap_cents)} / line`)
  if (agent.approval_threshold_cents !== null)
    parts.push(`ask above ${money(agent.approval_threshold_cents)}`)
  return parts.join(' · ')
}

/**
 * Overview: what needs a human, and where the money is. No charts, no invented
 * history — only facts the engine can stand behind.
 */
export function Overview({ me }: { me: Me }) {
  const [agents, setAgents] = useState<Agent[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const reduce = useReducedMotion()
  const m = motionSet(reduce)

  useEffect(() => {
    let live = true
    api
      .agents()
      .then((list) => {
        if (live) setAgents(list)
      })
      .catch(() => {
        if (live) setError('The agent list did not load.')
      })
    return () => {
      live = false
    }
  }, [])

  const agentCount = agents?.length ?? me.agent_count

  return (
    <motion.div className="screen" variants={m.stagger} initial="hidden" animate="show">
      <motion.header className="screen-head" variants={m.riseIn}>
        <div>
          <h1>Overview</h1>
          <p>What needs you, and where the money is.</p>
        </div>
      </motion.header>

      <motion.div className="stats" variants={m.riseIn}>
        <Stat label="Needs you" value="0" note="requests waiting for a signature" />
        <Stat label="Committed, unsigned" value={money(0)} note="held by the rules, not moving" />
        <Stat label="Settled this month" value={money(0)} note="captured through PayPal" />
        <Stat label="Agents" value={String(agentCount)} note="each with its own rule set" tone="accent" />
      </motion.div>

      <div className="cols">
        <motion.section className="card" variants={m.riseIn}>
          <header className="card-head">
            <h2>Needs you</h2>
            <span className="card-note mono">oldest first</span>
          </header>
          <div className="empty">
            <span className="empty-mark" aria-hidden="true" />
            <p className="empty-title">Nothing is waiting.</p>
            <p className="empty-note">
              A request lands here when a cart crosses the agent&rsquo;s approval threshold or
              its per-line cap. Inside the rules, no one is asked.
            </p>
          </div>
        </motion.section>

        <motion.section className="card" variants={m.riseIn}>
          <header className="card-head">
            <h2>Agents</h2>
            <span className="card-note mono">{agentCount} active</span>
          </header>

          {error ? <p className="inline-error">{error}</p> : null}

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
                    {agent.channel ? <span className="mono agent-channel">{agent.channel}</span> : null}
                  </div>
                  <p className="agent-caps mono">{capsLine(agent)}</p>
                  <p className="agent-scan">
                    {agent.vendors.length} approved {agent.vendors.length === 1 ? 'merchant' : 'merchants'}
                    {' · '}
                    {agent.categories.join(', ')}
                  </p>
                </li>
              ))}
            </ul>
          ) : null}
        </motion.section>
      </div>

      <motion.section className="card" variants={m.riseIn}>
        <header className="card-head">
          <h2>Foundation</h2>
          <span className="card-note mono">this build</span>
        </header>
        <ul className="checklist">
          <li>
            <Tick /> Database — workspace, users, agents, rules in SQLite; survives a restart
          </li>
          <li>
            <Tick /> Sessions — scrypt passwords, HttpOnly cookie
          </li>
          <li>
            <Tick /> Seeded demo workspace with four agents and their rule sets
          </li>
          <li className={walletTone(me)}>
            <Tick pending /> Wallet — {me.wallet.status === 'connected' ? 'connected' : 'not connected yet'}
          </li>
        </ul>
        <p className="fineprint">
          Inbox, Agents, Analytics and Integrations arrive with the next milestones. This screen
          is already reading the live engine, not a mock.
        </p>
      </motion.section>
    </motion.div>
  )
}

function Tick({ pending = false }: { pending?: boolean }) {
  if (pending) return <span className="tick is-pending" aria-hidden="true" />
  return (
    <span className="tick is-done" aria-hidden="true">
      <svg viewBox="0 0 12 12" width="10" height="10">
        <path
          d="M2.4 6.3l2.3 2.3L9.6 3.8"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  )
}

function walletTone(me: Me): string {
  return me.wallet.status === 'connected' ? '' : 'is-pending'
}
