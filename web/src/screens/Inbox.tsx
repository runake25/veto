import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { motion, useReducedMotion } from 'motion/react'
import { api, money, type Agent, type RequestSummary } from '../api'
import { motionSet } from '../components/motion'
import { StatusMark, originLabel, requestLabel, when } from '../components/request'

const FILTERS = [
  { value: '', label: 'All', hint: 'Every request in the record' },
  { value: 'awaiting', label: 'Needs you', hint: 'Waiting for a signature' },
  { value: 'captured', label: 'Captured', hint: 'Money moved through PayPal' },
  { value: 'blocked', label: 'Blocked', hint: 'Refused by the rules; no money moved' },
  { value: 'vetoed', label: 'Vetoed', hint: 'Refused by a person; no money moved' },
  { value: 'voided', label: 'Voided', hint: 'Withdrawn before any capture' },
]

/** Pending first, then newest — an inbox order, decided once here. */
function ordered(rows: RequestSummary[]): RequestSummary[] {
  const rank = (row: RequestSummary) =>
    row.status === 'awaiting' ? 0 : row.status === 'approved' ? 1 : 2
  return [...rows].sort(
    (a, b) =>
      rank(a) - rank(b) ||
      (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0),
  )
}

function emptyCopy(status: string): { title: string; note: string } {
  if (status === 'awaiting')
    return {
      title: 'Nothing needs you.',
      note: 'A cart lands here only when it crosses a cap or a threshold.',
    }
  if (status === 'captured')
    return {
      title: 'No captures in this view.',
      note: 'Captured money shows a PayPal order, authorization and capture id in its record.',
    }
  if (status === 'blocked')
    return {
      title: 'Nothing was blocked.',
      note: 'The rules refuse a cart when a merchant, a category or a cap says no.',
    }
  if (status === 'vetoed')
    return {
      title: 'Nothing was vetoed.',
      note: 'A veto is a person refusing a cart that the rules already sent for a signature.',
    }
  if (status === 'voided')
    return {
      title: 'Nothing was voided.',
      note: 'Voiding withdraws a pending request before any capture.',
    }
  return {
    title: 'No requests yet.',
    note: 'Ask an agent to buy something — the request appears here the moment it exists.',
  }
}

/**
 * Inbox: the only list of requests. Filters live in the URL so a view can be
 * linked, and every row opens its full record.
 */
export function Inbox() {
  const [params, setParams] = useSearchParams()
  const status = params.get('status') ?? ''
  const agentId = params.get('agent') ?? ''

  const [rows, setRows] = useState<RequestSummary[] | null>(null)
  const [total, setTotal] = useState(0)
  const [agents, setAgents] = useState<Agent[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [moreBusy, setMoreBusy] = useState(false)
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
        // the agent filter is a convenience, not the page
      })
    return () => {
      live = false
    }
  }, [])

  useEffect(() => {
    let live = true
    setRows(null)
    setError(null)
    const query = new URLSearchParams({ limit: '50' })
    if (status) query.set('status', status)
    if (agentId) query.set('agent_id', agentId)
    api
      .requests(query.toString())
      .then((data) => {
        if (!live) return
        setRows(ordered(data.requests))
        setTotal(data.total)
      })
      .catch(() => {
        if (live) setError('The request list did not load.')
      })
    return () => {
      live = false
    }
  }, [status, agentId])

  const choose = useCallback(
    (next: { status?: string; agent?: string }) => {
      const params = new URLSearchParams()
      const nextStatus = next.status ?? status
      const nextAgent = next.agent ?? agentId
      if (nextStatus) params.set('status', nextStatus)
      if (nextAgent) params.set('agent', nextAgent)
      setParams(params, { replace: true })
    },
    [status, agentId, setParams],
  )

  const loadMore = async () => {
    if (!rows) return
    setMoreBusy(true)
    try {
      const query = new URLSearchParams({ limit: '50', offset: String(rows.length) })
      if (status) query.set('status', status)
      if (agentId) query.set('agent_id', agentId)
      const data = await api.requests(query.toString())
      setRows(ordered([...rows, ...data.requests]))
    } catch {
      setError('The next page did not load.')
    } finally {
      setMoreBusy(false)
    }
  }

  const empty = emptyCopy(status)
  const count = rows ? (agentId ? `${rows.length} shown` : `${total} in the record`) : ''

  return (
    <motion.div className="screen" variants={m.stagger} initial="hidden" animate="show">
      <motion.header className="screen-head" variants={m.riseIn}>
        <div>
          <h1>Inbox</h1>
          <p>Every request an agent made, and what happened to the money.</p>
        </div>
        {count ? <span className="head-count mono">{count}</span> : null}
      </motion.header>

      <motion.div className="filter-bar" variants={m.riseIn}>
        <div className="filter-tabs" role="group" aria-label="Filter by status">
          {FILTERS.map((filter) => (
            <button
              key={filter.value || 'all'}
              type="button"
              className={`filter-tab${status === filter.value ? ' is-on' : ''}`}
              aria-pressed={status === filter.value}
              onClick={() => choose({ status: filter.value })}
              title={filter.hint}
            >
              {filter.label}
            </button>
          ))}
        </div>
        <label className="filter-agent">
          <span className="filter-agent-label">Agent</span>
          <select
            className="select"
            value={agentId}
            onChange={(event) => choose({ agent: event.target.value })}
          >
            <option value="">All agents</option>
            {(agents ?? []).map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.name}
              </option>
            ))}
          </select>
        </label>
      </motion.div>

      {error ? (
        <p className="inline-error" role="alert">
          {error}
        </p>
      ) : null}

      <motion.section className="card card-flush" variants={m.riseIn}>
        <div className="inbox-head" aria-hidden="true">
          <span>Request</span>
          <span>Agent</span>
          <span>Source</span>
          <span className="num">Amount</span>
          <span>Status</span>
          <span className="num">Created</span>
        </div>

        {!rows && !error ? (
          <div className="inbox-rows">
            {[0, 1, 2, 3, 4].map((i) => (
              <div className="inbox-row inbox-row-skel" key={i}>
                <span className="skel" style={{ width: '46%', height: 12 }} />
                <span className="skel" style={{ width: '72%', height: 10 }} />
              </div>
            ))}
          </div>
        ) : null}

        {rows && rows.length === 0 ? (
          <div className="empty inbox-empty">
            <p className="empty-title">{empty.title}</p>
            <p className="empty-note">{empty.note}</p>
          </div>
        ) : null}

        {rows && rows.length > 0 ? (
          <ul className="inbox-rows">
            {rows.map((row) => (
              <li key={row.id}>
                <Link className="inbox-row" to={`/inbox/${row.id}`}>
                  <span className="inbox-request">
                    <b>{row.title}</b>
                    <span className="inbox-meta mono">
                      {requestLabel(row.seq)} · {row.line_count}{' '}
                      {row.line_count === 1 ? 'line' : 'lines'}
                    </span>
                  </span>
                  <span className="inbox-agent">
                    {row.agent.name}
                    <span className="inbox-agent-tag mono">{row.agent.tag}</span>
                  </span>
                  <span className="inbox-source">{originLabel(row.origin)}</span>
                  <span className="inbox-amount">
                    {row.line_count ? money(row.total_cents, row.currency) : '—'}
                  </span>
                  <span className="inbox-status">
                    <StatusMark status={row.status} />
                  </span>
                  <span className="inbox-when mono">{when(row.created_at)}</span>
                </Link>
              </li>
            ))}
          </ul>
        ) : null}

        {rows && total > rows.length ? (
          <div className="inbox-more">
            <button
              type="button"
              className="btn btn-secondary btn-small"
              onClick={() => void loadMore()}
              disabled={moreBusy}
            >
              {moreBusy ? 'Loading…' : `Load ${Math.min(50, total - rows.length)} more`}
            </button>
          </div>
        ) : null}
      </motion.section>
    </motion.div>
  )
}
