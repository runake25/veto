import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { motion, useReducedMotion } from 'motion/react'
import { api, money, type Analytics as AnalyticsData } from '../api'
import { motionSet } from '../components/motion'
import { RuleFlag, dayLabel } from '../components/request'

const RANGES = [
  { value: 'month', label: 'This month' },
  { value: 'last', label: 'Last month' },
  { value: '90d', label: 'Last 90 days' },
] as const

type RangeKind = (typeof RANGES)[number]['value']

const pad = (value: number) => String(value).padStart(2, '0')
const ymd = (date: Date) =>
  `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
const monthYear = (date: Date) =>
  new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric' }).format(date)

/** "1–24 Oct 2026", "26 Jul – 24 Oct 2026" — the range in words, not ISO. */
function wordsRange(from: string, to: string): string {
  const a = new Date(`${from}T00:00:00`)
  const b = new Date(`${to}T00:00:00`)
  const month = new Intl.DateTimeFormat('en-US', { month: 'short' })
  if (a.getMonth() === b.getMonth() && a.getFullYear() === b.getFullYear()) {
    return `${a.getDate()}–${b.getDate()} ${month.format(b)} ${b.getFullYear()}`
  }
  return `${a.getDate()} ${month.format(a)} – ${b.getDate()} ${month.format(b)} ${b.getFullYear()}`
}

function rangeFor(kind: RangeKind): { from: string; to: string; label: string } {
  const now = new Date()
  if (kind === 'last') {
    const first = new Date(now.getFullYear(), now.getMonth() - 1, 1)
    const last = new Date(now.getFullYear(), now.getMonth(), 0)
    return { from: ymd(first), to: ymd(last), label: monthYear(first) }
  }
  if (kind === '90d') {
    const first = new Date(now)
    first.setDate(first.getDate() - 89)
    return { from: ymd(first), to: ymd(now), label: 'Last 90 days' }
  }
  const first = new Date(now.getFullYear(), now.getMonth(), 1)
  return { from: ymd(first), to: ymd(now), label: monthYear(now) }
}

/** Compact money for a bar label: $242, $1.2k, $13.96. */
function shortMoney(cents: number): string {
  if (cents === 0) return '$0'
  const dollars = cents / 100
  if (dollars >= 1000) return `$${(dollars / 1000).toFixed(1)}k`
  return `$${dollars.toFixed(dollars % 1 === 0 ? 0 : 2)}`
}

function Stat({
  label,
  value,
  note,
  tone,
}: {
  label: string
  value: string
  note: string
  tone?: 'pos' | 'wait' | 'neg'
}) {
  return (
    <div className="stat">
      <p className="stat-label">{label}</p>
      <p className={`stat-value${tone ? ` is-${tone}` : ''}`}>{value}</p>
      <p className="stat-note">{note}</p>
    </div>
  )
}

function SeriesChart({ data }: { data: AnalyticsData }) {
  if (!data.series.length) return null
  const max = Math.max(...data.series.map((day) => day.captured_cents), 0)
  const days = data.series
  const labelAt = new Set([0, Math.floor((days.length - 1) / 2), days.length - 1])
  const activeDays = days.filter((day) => day.captured_cents > 0).length

  return (
    <section className="card">
      <header className="card-head">
        <h2>Settled by day</h2>
        <span className="card-note mono">
          {max ? `${money(data.totals.captured_cents)} captured` : 'no captures in range'}
        </span>
      </header>
      <div className="chart-wrap">
        <div className="chart" role="img" aria-label="Captured spend per day, in dollars">
          {days.map((day, index) => {
            const height = max ? Math.max(2, Math.round((day.captured_cents / max) * 100)) : 2
            return (
              <span
                className={`chart-col${day.captured_cents ? ' is-on' : ''}`}
                key={day.date}
                title={`${day.date}: ${money(day.captured_cents)} · ${day.requests} ${
                  day.requests === 1 ? 'request' : 'requests'
                }`}
              >
                <span className="chart-bar" style={{ height: `${height}%` }} />
                {day.captured_cents && activeDays <= 4 ? (
                  <span
                    className={`chart-value mono${index === 0 ? ' is-first' : ''}${
                      index === days.length - 1 ? ' is-last' : ''
                    }`}
                    style={{ bottom: `calc(${height}% + 4px)` }}
                  >
                    {shortMoney(day.captured_cents)}
                  </span>
                ) : null}
                {labelAt.has(index) ? (
                  <span className="chart-tick mono">{dayLabel(day.date)}</span>
                ) : null}
              </span>
            )
          })}
        </div>
      </div>
      <p className="card-footnote">
        A capture is counted on the day the money moved, not the day the request was written.
      </p>
    </section>
  )
}

function AgentRows({ data }: { data: AnalyticsData }) {
  if (!data.by_agent.length) {
    return <p className="record-none">No agent spent anything in this range.</p>
  }
  const max = Math.max(...data.by_agent.map((row) => row.captured_cents), 1)
  return (
    <ul className="agg-rows">
      {data.by_agent.map((row) => (
        <li key={row.key}>
          <Link className="agg-row" to={`/inbox?agent=${encodeURIComponent(row.key)}`}>
            <span className="agg-name">
              <b>{row.label}</b>
              <span className="agg-sub mono">
                {row.tag || 'untagged'} · {row.requests}{' '}
                {row.requests === 1 ? 'request' : 'requests'}
              </span>
            </span>
            <span className="agg-bar-wrap" aria-hidden="true">
              <span
                className="agg-bar"
                style={{ width: `${Math.round((row.captured_cents / max) * 100)}%` }}
              />
            </span>
            <span className="agg-figure mono">{money(row.captured_cents)}</span>
          </Link>
        </li>
      ))}
    </ul>
  )
}

function SpendRows({
  rows: allRows,
  empty,
}: {
  rows: AnalyticsData['by_vendor']
  empty: string
}) {
  const rows = allRows.filter((row) => row.captured_cents > 0)
  if (!rows.length) return <p className="record-none">{empty}</p>
  return (
    <ul className="agg-rows">
      {rows.map((row) => (
        <li key={row.key || 'unknown'}>
          <span className="agg-row agg-row-static">
            <span className="agg-name">
              <b>{row.label}</b>
            </span>
            <span className="agg-figure">{money(row.captured_cents)}</span>
          </span>
        </li>
      ))}
    </ul>
  )
}

function RuleRows({ data }: { data: AnalyticsData }) {
  if (!data.by_rule.length) {
    return <p className="record-none">No rule fired in this range.</p>
  }
  return (
    <ul className="rule-rows">
      {data.by_rule.map((row) => (
        <li key={`${row.key}-${row.severity}`}>
          <RuleFlag rule={row.key} severity={row.severity} />
          <span className="rule-count">
            <b>{row.requests}</b> {row.requests === 1 ? 'request' : 'requests'}
            {row.blocked ? ` · ${row.blocked} blocked` : ''}
            {row.review ? ` · ${row.review} human` : ''}
          </span>
          {row.example ? <span className="rule-example">{row.example}</span> : null}
        </li>
      ))}
    </ul>
  )
}

/**
 * Analytics: the same stored rows, added up — never a second set of numbers.
 */
export function Analytics() {
  const [params, setParams] = useSearchParams()
  const kind = (RANGES.find((range) => range.value === params.get('range'))?.value ??
    'month') as RangeKind
  const range = rangeFor(kind)

  const [data, setData] = useState<AnalyticsData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const reduce = useReducedMotion()
  const m = motionSet(reduce)

  useEffect(() => {
    let live = true
    setData(null)
    setError(null)
    api
      .analytics(range.from, range.to)
      .then((next) => {
        if (live) setData(next)
      })
      .catch(() => {
        if (live) setError('The aggregates did not load.')
      })
    return () => {
      live = false
    }
  }, [range.from, range.to])

  const totals = data?.totals

  return (
    <motion.div className="screen" variants={m.stagger} initial="hidden" animate="show">
      <motion.header className="screen-head" variants={m.riseIn}>
        <div>
          <h1>Analytics</h1>
          <p>The same requests, added up. Captured money only counts when it moved.</p>
        </div>
        <div className="range-tabs" role="group" aria-label="Range">
          {RANGES.map((item) => (
            <button
              key={item.value}
              type="button"
              className={`range-tab${kind === item.value ? ' is-on' : ''}`}
              aria-pressed={kind === item.value}
              onClick={() => setParams(item.value === 'month' ? {} : { range: item.value })}
            >
              {item.label}
            </button>
          ))}
        </div>
      </motion.header>

      <motion.p className="range-label mono" variants={m.riseIn}>
        {wordsRange(range.from, range.to)}
      </motion.p>

      {error ? (
        <p className="inline-error" role="alert">
          {error}
        </p>
      ) : null}

      <motion.div className="stats" variants={m.riseIn}>
        <Stat
          label="Settled"
          value={totals ? money(totals.captured_cents) : '—'}
          note={totals ? `${totals.captured} captured through PayPal` : ' '}
          tone="pos"
        />
        <Stat
          label="Held for a signature"
          value={totals ? money(totals.pending_cents) : '—'}
          note={
            totals
              ? totals.pending_cents > 0
                ? `${totals.awaiting + totals.approved} ${
                    totals.awaiting + totals.approved === 1 ? 'request' : 'requests'
                  } not yet captured`
                : 'nothing is held right now'
              : ' '
          }
          tone={totals && totals.pending_cents > 0 ? 'wait' : undefined}
        />
        <Stat
          label="Stopped"
          value={totals ? money(totals.blocked_cents) : '—'}
          note={
            totals
              ? `${totals.blocked} blocked · ${totals.vetoed} vetoed · zero moved`
              : ' '
          }
        />
        <Stat
          label="Requests"
          value={totals ? String(totals.requests) : '—'}
          note={
            totals
              ? `${totals.auto} auto · ${totals.review} sent for review · ${totals.block} blocked`
              : ' '
          }
        />
      </motion.div>

      {!data && !error ? (
        <div className="card">
          <span className="skel" style={{ width: '100%', height: 140 }} />
        </div>
      ) : null}

      {data ? (
        <>
          <motion.div variants={m.riseIn}>
            <SeriesChart data={data} />
          </motion.div>

          <div className="record-cols analytics-cols">
            <div className="record-col">
              <motion.section className="card" variants={m.riseIn}>
                <header className="card-head">
                  <h2>By agent</h2>
                  <span className="card-note mono">settled</span>
                </header>
                <AgentRows data={data} />
                <p className="card-footnote">
                  Each figure is the sum of captured requests that agent made in this range.
                </p>
              </motion.section>

              <motion.section className="card" variants={m.riseIn}>
                <header className="card-head">
                  <h2>By vendor</h2>
                  <span className="card-note mono">settled</span>
                </header>
                <SpendRows rows={data.by_vendor} empty="No merchant was paid in this range." />
                <p className="card-footnote">
                  Settled money only: a held, refused or voided cart is not spent.
                </p>
              </motion.section>

              <motion.section className="card" variants={m.riseIn}>
                <header className="card-head">
                  <h2>By category</h2>
                  <span className="card-note mono">settled</span>
                </header>
                <SpendRows
                  rows={data.by_category}
                  empty="No category was paid in this range."
                />
                <p className="card-footnote">
                  One request can carry several lines; each captured line is counted once, in its
                  own category.
                </p>
              </motion.section>
            </div>

            <div className="record-col">
              <motion.section className="card" variants={m.riseIn}>
                <header className="card-head">
                  <h2>What the rules caught</h2>
                  <span className="card-note mono">triggers</span>
                </header>
                <RuleRows data={data} />
                <p className="card-footnote">
                  One request can trigger more than one rule, so these counts overlap. Blocked
                  means the rule refused on its own; human means the cart reached the inbox.
                </p>
              </motion.section>

              <motion.section className="card" variants={m.riseIn}>
                <header className="card-head">
                  <h2>Open the record</h2>
                  <span className="card-note mono">Inbox</span>
                </header>
                <p className="card-lede">
                  Every figure above has rows behind it. The Inbox lists the requests; a record
                  opens the cart, the consent and the capture.
                </p>
                <Link className="text-link mono record-link" to="/inbox">
                  open the inbox →
                </Link>
              </motion.section>
            </div>
          </div>
        </>
      ) : null}
    </motion.div>
  )
}
