import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { motion, useReducedMotion } from 'motion/react'
import { api, ApiError, type AgentInput } from '../api'
import { AgentForm } from '../components/AgentForm'
import { motionSet } from '../components/motion'

export function NewAgent() {
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const reduce = useReducedMotion()
  const m = motionSet(reduce)

  const submit = async (input: AgentInput) => {
    setBusy(true)
    setError(null)
    try {
      const agent = await api.createAgent(input)
      setSaved(true)
      window.setTimeout(() => navigate(`/agents/${agent.id}`), reduce ? 0 : 420)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the agent.')
      setBusy(false)
    }
  }

  return (
    <motion.div className="screen" variants={m.stagger} initial="hidden" animate="show">
      <motion.nav className="breadcrumb mono" variants={m.riseIn} aria-label="Breadcrumb">
        <Link to="/agents">Agents</Link> <span aria-hidden="true">/</span> <span>New</span>
      </motion.nav>

      <motion.header className="screen-head" variants={m.riseIn}>
        <div>
          <h1>New agent</h1>
          <p>Set the budget, the caps and the point where a person signs.</p>
        </div>
      </motion.header>

      <motion.section className="card" variants={m.riseIn}>
        <AgentForm
          submitLabel="Create agent"
          busy={busy}
          saved={saved}
          error={error}
          onSubmit={(input) => void submit(input)}
          onCancel={() => navigate('/agents')}
        />
      </motion.section>
    </motion.div>
  )
}
