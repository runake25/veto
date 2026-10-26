import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { motion, useReducedMotion } from 'motion/react'
import { api, money, type Agent, type RequestSummary } from '../api'
import { motionSet } from '../components/motion'
import { StatusMark, originLabel, requestLabel, when } from '../components/request'

const FILTERS = [
  { value: '', label: 'All', hint: 'Every request in the record' },
  { value: 'awaiting', label: 'Needs you', hint: 'Waiting for a signature that is still valid' },
  { value: 'expired', label: 'Expired', hint: 'The consent lapsed before anyone signed' },
  { value: 'captured', label: 'Captured', hint: 'Money moved through PayPal' },
  { value: 'blocked', label: 'Blocked', hint: 'Refused by the rules; no money moved' },
  { value: 'vetoed', label: 'Vetoed', hint: 'Refused by a person; no money moved' },
  { value: 'voided', label: 'Voided', hint: 'Withdrawn before any capture' },
]

/** The orders the record offers. The server owns the SQL; the UI names it. */
const SORT_OPTIONS = [
  { value: '', label: 'Needs you first' },
  { value: 'newest', label: 'Newest first' },
  { value: 'oldest', label: 'Oldest first' },
  { value: 'amount_desc', label: 'Amount: high to low' },
  { value: 'amount_asc', label: 'Amount: low to high' },
  { value: 'agent', label: 'Agent: A to Z' },
  { value: 'agent_desc', label: 'Agent: Z to A' },
]

function nextSort(current: string, column: 'agent' | 'amount' | 'created'): string {
  if (column === 'agent') return current === 'agent' ? 'agent_desc' : 'agent'
  if (column === 'amount') return current === 'amount_desc' ? 'amount_asc' : 'amount_desc'
  return current === 'newest' ? 'oldest' : 'newest'
}

function sortLabel(current: string, column: 'agent' | 'amount' | 'created'): string {
  if (column === 'agent') {
    return current === 'agent_desc' ? 'Sort agent, Z to A' : 'Sort agent, A to Z'
  }
  if (column === 'amount') {
    return current === 'amount_asc' ? 'Sort amount, low to high' : 'Sort amount, high to low'
  }
  return current === 'oldest' ? 'Sort oldest first' : 'Sort newest first'
}

function sortMark(current: string, column: 'agent' | 'amount' | 'created'): string {
  if (column === 'agent' && current === 'agent') return '↑'
  if (column === 'agent' && current === 'agent_desc') return '↓'
  if (column === 'amount' && current === 'amount_desc') return '↓'
  if (column === 'amount' && current === 'amount_asc') return '↑'
  if (column === 'created' && current === 'newest') return '↓'
  if (column === 'created' && current === 'oldest') return '↑'
  return '↕'
}

function isSorted(current: string, column: 'agent' | 'amount' | 'created'): boolean {
  if (column === 'agent') return current === 'agent' || current === 'agent_desc'
  if (column === 'amount') return current === 'amount_desc' || current === 'amount_asc'
  return current === 'newest' || current === 'oldest'
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
  const sort = params.get('sort') ?? ''
  const q = params.get('q') ?? ''

  const [rows, setRows] = useState<RequestSummary[] | null>(null)
  const [total, setTotal] = useState(0)
  const [sumCents, setSumCents] = useState(0)
  const [reloadTick, setReloadTick] = useState(0)
  const [agents, setAgents] = useState<Agent[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [moreBusy, setMoreBusy] = useState(false)
  const [search, setSearch] = useState(q)
  const [refreshedAt, setRefreshedAt] = useState<Date | null>(null)
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
    if (sort) query.set('sort', sort)
    if (q) query.set('q', q)
    api
      .requests(query.toString())
      .then((data) => {
        if (!live) return
        setRows(data.requests)
        setTotal(data.total)
        setSumCents(data.sum_cents ?? 0)
        setRefreshedAt(new Date())
      })
      .catch(() => {
        if (live) setError('The request list did not load.')
      })
    return () => {
      live = false
    }
  }, [status, agentId, sort, q, reloadTick])

  // an approver leaves the tab open; the queue should not silently go stale
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') setReloadTick((value) => value + 1)
    }, 30_000)
    return () => window.clearInterval(timer)
  }, [])

  const choose = useCallback(
    (next: { status?: string; agent?: string; sort?: string; q?: string }) => {
      const params = new URLSearchParams()
      const nextStatus = next.status ?? status
      const nextAgent = next.agent ?? agentId
      const nextSort = next.sort ?? sort
      const nextQuery = next.q ?? q
      if (nextStatus) params.set('status', nextStatus)
      if (nextAgent) params.set('agent', nextAgent)
      if (nextSort) params.set('sort', nextSort)
      if (nextQuery) params.set('q', nextQuery)
      setParams(params, { replace: true })
    },
    [status, agentId, sort, q, setParams],
  )

  // a search box writes the URL after a short pause, so the view stays linkable
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const next = search.trim()
      if (next !== q) choose({ q: next })
    }, 250)
    return () => window.clearTimeout(timer)
  }, [search, q, choose])

  const loadMore = async () => {
    if (!rows) return
    setMoreBusy(true)
    try {
      const query = new URLSearchParams({ limit: '50', offset: String(rows.length) })
      if (status) query.set('status', status)
      if (agentId) query.set('agent_id', agentId)
      if (sort) query.set('sort', sort)
      if (q) query.set('q', q)
      const data = await api.requests(query.toString())
      setRows([...rows, ...data.requests])
    } catch {
      setError('The next page did not load.')
    } finally {
      setMoreBusy(false)
    }
  }

  const empty = q
    ? {
        title: `No request matches “${q}”.`,
        note: 'Try a shorter fragment, or clear the search to see the whole record.',
      }
    : emptyCopy(status)
  const filtered = Boolean(status || agentId || q)
  const count = rows ? (filtered ? `${total} matching` : `${total} in the record`) : ''

  return (
    <motion.div className="screen" variants={m.stagger} initial="hidden" animate="show">
      <motion.header className="screen-head" variants={m.riseIn}>
        <div>
          <h1>Inbox</h1>
          <p>Every request an agent made, and what happened to the money.</p>
        </div>
        <div className="head-tools">
          {count ? <span className="head-count mono">{count}</span> : null}
          <button
            type="button"
            className="refresh-link mono"
            onClick={() => setReloadTick((value) => value + 1)}
          >
            {refreshedAt
              ? `Updated ${refreshedAt.toLocaleTimeString('en-US', {
                  hour: '2-digit',
                  minute: '2-digit',
                })} · Refresh`
              : 'Refresh'}
          </button>
        </div>
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
        <label className="filter-sort">
          <span className="filter-agent-label">Order</span>
          <select
            className="select"
            value={sort}
            aria-label="Order requests"
            onChange={(event) => choose({ sort: event.target.value })}
          >
            {SORT_OPTIONS.map((option) => (
              <option key={option.value || 'priority'} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label className="filter-search">
          <span className="filter-agent-label">Search</span>
          <input
            className="input"
            type="search"
            value={search}
            aria-label="Search requests"
            placeholder="Title, agent or requester"
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
      </motion.div>

      {error ? (
        <p className="inline-error inbox-error" role="alert">
          {error}
          <button
            type="button"
            className="btn btn-secondary btn-small"
            onClick={() => setReloadTick((value) => value + 1)}
          >
            Retry
          </button>
        </p>
      ) : null}

      <motion.section className="card card-flush" variants={m.riseIn}>
        <div className="inbox-head">
          <span>Request</span>
          <span>
            <button
              type="button"
              className={`inbox-sort${isSorted(sort, 'agent') ? ' is-on' : ''}`}
              aria-label={sortLabel(sort, 'agent')}
              title={sortLabel(sort, 'agent')}
              onClick={() => choose({ sort: nextSort(sort, 'agent') })}
            >
              Agent
              <span className="sort-mark" aria-hidden="true">
                {sortMark(sort, 'agent')}
              </span>
            </button>
          </span>
          <span>Source</span>
          <span className="num">
            <button
              type="button"
              className={`inbox-sort${isSorted(sort, 'amount') ? ' is-on' : ''}`}
              aria-label={sortLabel(sort, 'amount')}
              title={sortLabel(sort, 'amount')}
              onClick={() => choose({ sort: nextSort(sort, 'amount') })}
            >
              Amount
              <span className="sort-mark" aria-hidden="true">
                {sortMark(sort, 'amount')}
              </span>
            </button>
          </span>
          <span>Status</span>
          <span className="num">
            <button
              type="button"
              className={`inbox-sort${isSorted(sort, 'created') ? ' is-on' : ''}`}
              aria-label={sortLabel(sort, 'created')}
              title={sortLabel(sort, 'created')}
              onClick={() => choose({ sort: nextSort(sort, 'created') })}
            >
              Created
              <span className="sort-mark" aria-hidden="true">
                {sortMark(sort, 'created')}
              </span>
            </button>
          </span>
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
            {filtered || sort ? (
              <button
                type="button"
                className="btn btn-secondary btn-small"
                onClick={() => { setSearch(''); choose({ status: '', agent: '', sort: '', q: '' }) }}
              >
                Show all requests
              </button>
            ) : null}
          </div>
        ) : null}

        {rows && rows.length > 0 ? (
          <ul className="inbox-rows">
            {rows.map((row) => (
              <li key={row.id}>
                <Link className="inbox-row" to={`/inbox/${row.id}`} title={row.title}>
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
                    <StatusMark
                      status={row.status}
                      label={row.consent_expired ? 'Consent expired' : undefined}
                    />
                  </span>
                  <span className="inbox-when mono">{when(row.created_at)}</span>
                </Link>
              </li>
            ))}
          </ul>
        ) : null}

        {rows && rows.length ? (
          <div className="inbox-summary">
            <span className="mono">
              {filtered ? `1–${rows.length} of ${total}` : `1–${rows.length} of ${total}`}
            </span>
            <span className="inbox-summary-total mono">{money(sumCents)} total</span>
          </div>
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
