import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { motion, useReducedMotion } from 'motion/react'
import { api, money, type Agent, type RequestSummary } from '../api'
import { InlineError } from '../components/Bits'
import { FreshnessButton, useFreshness } from '../components/freshness'
import { motionSet } from '../components/motion'
import { StatusMark, requestLabel, sourceLabel, when as whenText } from '../components/request'

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

/**
 * The calendar ranges the queue offers. Days are the viewer's own, and the
 * offset travels with the request so a capture after local midnight belongs to
 * the right day.
 */
const WHEN_OPTIONS = [
  { value: '', label: 'Any time' },
  { value: 'today', label: 'Today' },
  { value: '7d', label: 'Last 7 days' },
  { value: '30d', label: 'Last 30 days' },
  { value: 'month', label: 'This month' },
]

type Column = 'agent' | 'amount' | 'created'

const pad = (value: number) => String(value).padStart(2, '0')
const ymd = (date: Date) =>
  `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`

/** The two local calendar days a range covers, or null for "any time". */
function rangeDays(kind: string): { from: string; to: string } | null {
  if (!kind) return null
  const now = new Date()
  const to = ymd(now)
  if (kind === 'today') return { from: to, to }
  if (kind === 'month') {
    return { from: ymd(new Date(now.getFullYear(), now.getMonth(), 1)), to }
  }
  const start = new Date(now)
  if (kind === '7d') start.setDate(start.getDate() - 6)
  else if (kind === '30d') start.setDate(start.getDate() - 29)
  else return null
  return { from: ymd(start), to }
}

function nextSort(current: string, column: Column): string {
  if (column === 'agent') return current === 'agent' ? 'agent_desc' : 'agent'
  if (column === 'amount') return current === 'amount_desc' ? 'amount_asc' : 'amount_desc'
  return current === 'newest' ? 'oldest' : 'newest'
}

function sortLabel(current: string, column: Column): string {
  if (column === 'agent') {
    return current === 'agent_desc' ? 'Sort agent, Z to A' : 'Sort agent, A to Z'
  }
  if (column === 'amount') {
    return current === 'amount_asc' ? 'Sort amount, low to high' : 'Sort amount, high to low'
  }
  return current === 'oldest' ? 'Sort oldest first' : 'Sort newest first'
}

function sortMark(current: string, column: Column): string {
  if (column === 'agent' && current === 'agent') return '↑'
  if (column === 'agent' && current === 'agent_desc') return '↓'
  if (column === 'amount' && current === 'amount_desc') return '↓'
  if (column === 'amount' && current === 'amount_asc') return '↑'
  if (column === 'created' && current === 'newest') return '↓'
  if (column === 'created' && current === 'oldest') return '↑'
  return '↕'
}

function isSorted(current: string, column: Column): boolean {
  if (column === 'agent') return current === 'agent' || current === 'agent_desc'
  if (column === 'amount') return current === 'amount_desc' || current === 'amount_asc'
  return current === 'newest' || current === 'oldest'
}

/** `aria-sort` belongs only on the column the list is actually ordered by. */
function ariaSort(current: string, column: Column): 'ascending' | 'descending' | undefined {
  if (!isSorted(current, column)) return undefined
  const ascending =
    (column === 'agent' && current === 'agent') ||
    (column === 'amount' && current === 'amount_asc') ||
    (column === 'created' && current === 'oldest')
  return ascending ? 'ascending' : 'descending'
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
 * linked, and every row opens its full record. The queue is a real table with
 * sortable column headers (a phone gets the same rows as a list instead).
 */
export function Inbox() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const status = params.get('status') ?? ''
  const agentId = params.get('agent') ?? ''
  const sort = params.get('sort') ?? ''
  const q = params.get('q') ?? ''
  const period = params.get('when') ?? ''

  const [rows, setRows] = useState<RequestSummary[] | null>(null)
  const [total, setTotal] = useState(0)
  const [sumCents, setSumCents] = useState(0)
  const [reloadTick, setReloadTick] = useState(0)
  const [agents, setAgents] = useState<Agent[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [moreBusy, setMoreBusy] = useState(false)
  const [search, setSearch] = useState(q)
  const { tick, markLoaded } = useFreshness()
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

  // a filter change starts a fresh view; a background refresh keeps the rows
  useEffect(() => {
    setRows(null)
  }, [status, agentId, sort, q, period])

  useEffect(() => {
    let live = true
    setError(null)
    const query = new URLSearchParams({ limit: '50' })
    if (status) query.set('status', status)
    if (agentId) query.set('agent_id', agentId)
    if (sort) query.set('sort', sort)
    if (q) query.set('q', q)
    const days = rangeDays(period)
    if (days) {
      query.set('from', days.from)
      query.set('to', days.to)
      query.set('tz', String(-new Date().getTimezoneOffset()))
    }
    api
      .requests(query.toString())
      .then((data) => {
        if (!live) return
        setRows(data.requests)
        setTotal(data.total)
        setSumCents(data.sum_cents ?? 0)
        markLoaded()
      })
      .catch(() => {
        if (live) setError('The request list did not load.')
      })
    return () => {
      live = false
    }
  }, [status, agentId, sort, q, period, tick, reloadTick, markLoaded])

  const choose = useCallback(
    (next: { status?: string; agent?: string; sort?: string; q?: string; when?: string }) => {
      const params = new URLSearchParams()
      const nextStatus = next.status ?? status
      const nextAgent = next.agent ?? agentId
      const nextSort = next.sort ?? sort
      const nextQuery = next.q ?? q
      const nextWhen = next.when ?? period
      if (nextStatus) params.set('status', nextStatus)
      if (nextAgent) params.set('agent', nextAgent)
      if (nextSort) params.set('sort', nextSort)
      if (nextQuery) params.set('q', nextQuery)
      if (nextWhen) params.set('when', nextWhen)
      setParams(params, { replace: true })
    },
    [status, agentId, sort, q, period, setParams],
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
      const days = rangeDays(period)
      if (days) {
        query.set('from', days.from)
        query.set('to', days.to)
        query.set('tz', String(-new Date().getTimezoneOffset()))
      }
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
  const filtered = Boolean(status || agentId || q || period)
  const count = rows ? (filtered ? `${total} matching` : `${total} in the record`) : ''
  const open = (id: string) => navigate(`/inbox/${id}`)

  return (
    <motion.div className="screen" variants={m.stagger} initial="hidden" animate="show">
      <motion.header className="screen-head" variants={m.riseIn}>
        <div>
          <h1>Inbox</h1>
          <p>Every request an agent made, and what happened to the money.</p>
        </div>
        <div className="head-tools">
          {count ? <span className="head-count mono">{count}</span> : null}
          <FreshnessButton />
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
        <div className="filter-controls">
        <label className="filter-search">
          <span className="filter-agent-label">Search</span>
          <input
            className="input"
            type="search"
            value={search}
            aria-label="Search requests"
            placeholder="Title, vendor, agent or requester"
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
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
        <label className="filter-when">
          <span className="filter-agent-label">When</span>
          <select
            className="select"
            value={period}
            aria-label="Filter by day"
            title="Calendar days in your own time zone"
            onChange={(event) => choose({ when: event.target.value })}
          >
            {WHEN_OPTIONS.map((option) => (
              <option key={option.value || 'any'} value={option.value}>
                {option.label}
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
        </div>
      </motion.div>

      {error ? (
        <InlineError
          className="inbox-error"
          onRetry={() => setReloadTick((value) => value + 1)}
        >
          {error}
        </InlineError>
      ) : null}

      <motion.section className="card card-flush" variants={m.riseIn}>
        {!rows && !error ? (
          <>
            <div className="inbox-table-wrap inbox-skel-table" aria-hidden="true">
              <table className="inbox-table">
                <thead>
                  <tr>
                    <th scope="col">Request</th>
                    <th scope="col">Agent</th>
                    <th scope="col" className="col-source">
                      Source
                    </th>
                    <th scope="col" className="num">
                      Amount
                    </th>
                    <th scope="col">Status</th>
                    <th scope="col" className="num col-created">
                      Created
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {[0, 1, 2, 3, 4].map((i) => (
                    <tr className="inbox-row" key={i}>
                      <td>
                        <span className="skel" style={{ width: '64%', height: 12 }} />
                      </td>
                      <td className="col-agent">
                        <span className="skel" style={{ width: '72%', height: 10 }} />
                      </td>
                      <td className="col-source">
                        <span className="skel" style={{ width: '54%', height: 10 }} />
                      </td>
                      <td className="num">
                        <span className="skel" style={{ width: 52, height: 10 }} />
                      </td>
                      <td>
                        <span className="skel" style={{ width: 68, height: 10 }} />
                      </td>
                      <td className="num col-created">
                        <span className="skel" style={{ width: 58, height: 10 }} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="inbox-mobile inbox-skel-mobile" aria-hidden="true">
              {[0, 1, 2, 3, 4].map((i) => (
                <div className="inbox-mobile-row" key={i}>
                  <span className="skel" style={{ width: '58%', height: 12 }} />
                  <span className="skel" style={{ width: '34%', height: 10 }} />
                </div>
              ))}
            </div>
          </>
        ) : null}

        {rows && rows.length === 0 ? (
          <div className="empty inbox-empty">
            <p className="empty-title">{empty.title}</p>
            <p className="empty-note">{empty.note}</p>
            {filtered || sort ? (
              <button
                type="button"
                className="btn btn-secondary btn-small"
                onClick={() => {
                  setSearch('')
                  choose({ status: '', agent: '', sort: '', q: '', when: '' })
                }}
              >
                Show all requests
              </button>
            ) : null}
          </div>
        ) : null}

        {rows && rows.length > 0 ? (
          <>
            <div className="inbox-table-wrap">
              <table className="inbox-table">
                <caption className="sr-only">
                  Requests in the record. Every row opens its full record.
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Request</th>
                    <th scope="col" aria-sort={ariaSort(sort, 'agent')}>
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
                    </th>
                    <th scope="col" className="col-source">
                      Source
                    </th>
                    <th scope="col" className="num" aria-sort={ariaSort(sort, 'amount')}>
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
                    </th>
                    <th scope="col">Status</th>
                    <th scope="col" className="num col-created" aria-sort={ariaSort(sort, 'created')}>
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
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr
                      className="inbox-row"
                      key={row.id}
                      onClick={(event) => {
                        if ((event.target as HTMLElement).closest('a')) return
                        open(row.id)
                      }}
                    >
                      <td className="inbox-request">
                        <Link
                          className="inbox-row-link"
                          to={`/inbox/${row.id}`}
                          title={row.title}
                        >
                          <b>{row.title}</b>
                        </Link>
                        <span className="inbox-meta mono">
                          {requestLabel(row.seq)} · {row.line_count}{' '}
                          {row.line_count === 1 ? 'line' : 'lines'}
                        </span>
                      </td>
                      <td className="col-agent inbox-agent">
                        {row.agent.name}
                        <span className="inbox-agent-tag mono">{row.agent.tag}</span>
                      </td>
                      <td className="col-source inbox-source">
                        {sourceLabel(row.origin, row.routine_name)}
                      </td>
                      <td className="num inbox-amount">
                        {row.line_count ? money(row.total_cents, row.currency) : '—'}
                      </td>
                      <td className="inbox-status">
                        <StatusMark
                          status={row.status}
                          label={row.consent_expired ? 'Consent expired' : undefined}
                        />
                      </td>
                      <td className="num col-created inbox-when mono">
                        {whenText(row.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* the same rows as a list on a phone: title first, nothing invented */}
            <ul className="inbox-mobile">
              {rows.map((row) => (
                <li key={row.id}>
                  <Link className="inbox-mobile-row" to={`/inbox/${row.id}`} title={row.title}>
                    <span className="inbox-mobile-main">
                      <b>{row.title}</b>
                      <span className="inbox-meta mono">
                        {requestLabel(row.seq)} · {row.agent.name} ·{' '}
                        {sourceLabel(row.origin, row.routine_name)}
                      </span>
                    </span>
                    <span className="inbox-mobile-side">
                      <span className="inbox-amount">
                        {row.line_count ? money(row.total_cents, row.currency) : '—'}
                      </span>
                      <StatusMark
                        status={row.status}
                        label={row.consent_expired ? 'Consent expired' : undefined}
                      />
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </>
        ) : null}

        {rows && rows.length ? (
          <div className="inbox-summary">
            <span className="mono">
              1–{rows.length} of {total}
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
