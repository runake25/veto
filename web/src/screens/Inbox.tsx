import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { motion, useReducedMotion } from 'motion/react'
import { api, money, type Agent, type RequestSummary } from '../api'
import { InlineError } from '../components/Bits'
import { FreshnessButton, useFreshness } from '../components/freshness'
import { motionSet } from '../components/motion'
import {
  StatusMark,
  requestLabel,
  sourceLabel,
  age,
  when as whenText,
} from '../components/request'
import { STATE_GROUPS, stateGroupFor } from '../components/state'

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

/** One title and one line per group: an empty queue needs no philosophy. */
function emptyCopy(group: string): { title: string; note: string } {
  if (group === 'needs')
    return { title: 'Nothing needs you.', note: 'A request lands here when a rule needs a signature.' }
  if (group === 'settled')
    return {
      title: 'Nothing settled yet.',
      note: 'Captured money carries its PayPal order and capture id.',
    }
  if (group === 'refused')
    return { title: 'Nothing was refused.', note: 'A rule or a person stopped it before any capture.' }
  if (group === 'withdrawn')
    return { title: 'Nothing was withdrawn.', note: 'Voided requests ended before any capture.' }
  return { title: 'No requests yet.', note: 'Ask an agent to buy something; it appears here.' }
}

/** The footer's one word for the money column, per group. */
function sumWord(group: string): string {
  if (group === 'needs') return 'held'
  if (group === 'settled') return 'settled'
  if (group === 'refused') return 'refused'
  if (group === 'withdrawn') return 'voided'
  return 'total'
}

/**
 * Inbox: the only list of requests. The tabs are the five state groups, in the
 * order that matters — money waiting on a signature first, history after. The
 * whole view lives in the URL, and every row opens its full record.
 */
export function Inbox() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const stateParam = params.get('state') ?? ''
  const legacyStatus = params.get('status') ?? ''
  const agentId = params.get('agent') ?? ''
  const sort = params.get('sort') ?? ''
  const q = params.get('q') ?? ''
  const period = params.get('when') ?? ''

  // An old `?status=awaiting` link still resolves to the group that now holds
  // that status; the tab is the group, never a raw status.
  const tab = useMemo(() => {
    if (STATE_GROUPS.some((group) => group.value === stateParam)) return stateParam
    return legacyStatus ? stateGroupFor(legacyStatus) : ''
  }, [stateParam, legacyStatus])

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
  }, [tab, agentId, sort, q, period])

  const query = useMemo(() => {
    const next = new URLSearchParams({ limit: '50' })
    if (tab) next.set('state', tab)
    if (agentId) next.set('agent_id', agentId)
    if (sort) next.set('sort', sort)
    if (q) next.set('q', q)
    const days = rangeDays(period)
    if (days) {
      next.set('from', days.from)
      next.set('to', days.to)
      next.set('tz', String(-new Date().getTimezoneOffset()))
    }
    return next.toString()
  }, [tab, agentId, sort, q, period])

  useEffect(() => {
    let live = true
    setError(null)
    api
      .requests(query)
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
  }, [query, tick, reloadTick, markLoaded])

  const choose = useCallback(
    (next: {
      state?: string
      agent?: string
      sort?: string
      q?: string
      when?: string
    }) => {
      const params = new URLSearchParams()
      const nextState = next.state ?? tab
      const nextAgent = next.agent ?? agentId
      const nextSort = next.sort ?? sort
      const nextQuery = next.q ?? q
      const nextWhen = next.when ?? period
      if (nextState) params.set('state', nextState)
      if (nextAgent) params.set('agent', nextAgent)
      if (nextSort) params.set('sort', nextSort)
      if (nextQuery) params.set('q', nextQuery)
      if (nextWhen) params.set('when', nextWhen)
      setParams(params, { replace: true })
    },
    [tab, agentId, sort, q, period, setParams],
  )

  // an old status link becomes the group link, so a bookmark keeps working
  useEffect(() => {
    if (stateParam || !legacyStatus) return
    const next = new URLSearchParams(params)
    next.delete('status')
    const mapped = stateGroupFor(legacyStatus)
    if (mapped) next.set('state', mapped)
    setParams(next, { replace: true })
  }, [stateParam, legacyStatus, params, setParams])

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
      const next = new URLSearchParams(query)
      next.set('offset', String(rows.length))
      const data = await api.requests(next.toString())
      setRows([...rows, ...data.requests])
    } catch {
      setError('The next page did not load.')
    } finally {
      setMoreBusy(false)
    }
  }

  const empty = q
    ? { title: `No request matches “${q}”.`, note: 'Try a shorter fragment.' }
    : emptyCopy(tab)
  const filtered = Boolean(tab || agentId || q || period)
  const count = rows ? `${total} ${total === 1 ? 'request' : 'requests'}` : ''
  const open = (id: string) => navigate(`/inbox/${id}`)

  return (
    <motion.div className="screen" variants={m.stagger} initial="hidden" animate="show">
      <motion.header className="screen-head" variants={m.riseIn}>
        <div>
          <h1>Inbox</h1>
          <p>Every request, and where the money went.</p>
        </div>
        <div className="head-tools">
          {count ? <span className="head-count mono">{count}</span> : null}
          <FreshnessButton />
        </div>
      </motion.header>

      <motion.div className="inbox-toolbar" variants={m.riseIn}>
        <div className="filter-tabs" role="group" aria-label="Request state">
          {STATE_GROUPS.map((group) => (
            <button
              key={group.value || 'all'}
              type="button"
              className={`filter-tab${tab === group.value ? ' is-on' : ''}`}
              aria-pressed={tab === group.value}
              onClick={() => choose({ state: group.value })}
              title={group.hint}
            >
              {group.label}
            </button>
          ))}
        </div>
        <div className="inbox-filters">
          <label className="inbox-search">
            <span className="sr-only">Search requests</span>
            <input
              className="input"
              type="search"
              value={search}
              placeholder="Search title, merchant, agent"
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <label className="inbox-filter">
            <span className="sr-only">Filter by agent</span>
            <select
              className="select"
              value={agentId}
              aria-label="Filter by agent"
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
          <label className="inbox-filter">
            <span className="sr-only">Filter by day</span>
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
          <label className="inbox-filter">
            <span className="sr-only">Order requests</span>
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
                    <th scope="col" className="col-request">
                      Request
                    </th>
                    <th scope="col" className="num col-amount">
                      Amount
                    </th>
                    <th scope="col" className="col-agent">
                      Agent
                    </th>
                    <th scope="col" className="col-source">
                      Source
                    </th>
                    <th scope="col" className="col-status">
                      Status
                    </th>
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
                      <td className="num">
                        <span className="skel" style={{ width: 52, height: 10 }} />
                      </td>
                      <td className="col-agent">
                        <span className="skel" style={{ width: '72%', height: 10 }} />
                      </td>
                      <td className="col-source">
                        <span className="skel" style={{ width: '54%', height: 10 }} />
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
            {filtered ? (
              <button
                type="button"
                className="btn btn-secondary btn-small"
                onClick={() => {
                  setSearch('')
                  choose({ state: '', agent: '', sort: '', q: '', when: '' })
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
                    <th scope="col" className="col-request">
                      Request
                    </th>
                    <th scope="col" className="num col-amount" aria-sort={ariaSort(sort, 'amount')}>
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
                    <th scope="col" className="col-agent" aria-sort={ariaSort(sort, 'agent')}>
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
                    <th scope="col" className="col-status">
                      Status
                    </th>
                    <th
                      scope="col"
                      className="num col-created"
                      aria-sort={ariaSort(sort, 'created')}
                    >
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
                      <td className="num inbox-amount">
                        {row.line_count ? money(row.total_cents, row.currency) : '—'}
                      </td>
                      <td className="col-agent inbox-agent">{row.agent.name}</td>
                      <td className="col-source inbox-source">
                        {sourceLabel(row.origin, row.routine_name)}
                      </td>
                      <td className="col-status inbox-status">
                        <StatusMark
                          status={row.status}
                          label={row.consent_expired ? 'Consent expired' : undefined}
                        />
                      </td>
                      <td className="num col-created inbox-when">
                        {whenText(row.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* the same rows as a list on a phone: title and money first */}
            <ul className="inbox-mobile">
              {rows.map((row) => (
                <li key={row.id}>
                  <Link className="inbox-mobile-row" to={`/inbox/${row.id}`} title={row.title}>
                    <span className="inbox-mobile-main">
                      <b>{row.title}</b>
                      <span className="inbox-meta mono">
                        {requestLabel(row.seq)} · {row.agent.name} · {age(row.created_at)}
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
            <span className="inbox-summary-total mono">
              {money(sumCents)} {sumWord(tab)}
            </span>
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
