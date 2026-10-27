import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { motion, useReducedMotion } from 'motion/react'
import { api, money, type Analytics as AnalyticsData } from '../api'
import { InlineError } from '../components/Bits'
import { FreshnessButton, useFreshness } from '../components/freshness'
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

function SeriesChart({ data }: { data: AnalyticsData }) {
  if (!data.series.length) return null
  const max = Math.max(...data.series.map((day) => day.captured_cents), 0)
  const days = data.series
  const labelAt = new Set([0, Math.floor((days.length - 1) / 2), days.length - 1])
  const activeDays = days.filter((day) => day.captured_cents > 0).length

  return (
    <section className="card chart-card">
      <header className="card-head">
        <h2>Settled by day</h2>
        <span className="card-note mono">{days.length} days</span>
      </header>
      {max ? (
        <div className="chart-wrap">
          <div className="chart" role="img" aria-label="Captured spend per day, in dollars">
            {days.map((day, index) => {
              const height = Math.max(2, Math.round((day.captured_cents / max) * 100))
              return (
                <span
                  className={`chart-col${day.captured_cents ? ' is-on' : ''}`}
                  key={day.date}
                  title={`${day.date}: ${money(day.captured_cents)} · ${day.requests} ${
                    day.requests === 1 ? 'request' : 'requests'
                  }`}
                >
                  <span className="chart-bar" style={{ height: `${height}%` }} />
                  {day.captured_cents && height >= 22 && activeDays <= 4 ? (
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
      ) : (
        <p className="chart-empty">No captures in this range.</p>
      )}
      <p className="card-footnote">Counted on the day the money moved.</p>
      <details className="chart-table">
        <summary>Daily numbers</summary>
        <div className="chart-table-scroll">
          <table>
            <caption className="sr-only">Captured spend by day, in dollars</caption>
            <thead>
              <tr>
                <th scope="col">Day</th>
                <th scope="col" className="num">
                  Captured
                </th>
                <th scope="col" className="num">
                  Requests
                </th>
              </tr>
            </thead>
            <tbody>
              {days.map((day) => (
                <tr key={day.date}>
                  <th scope="row" className="mono">
                    {dayLabel(day.date)}
                  </th>
                  <td className="num">{money(day.captured_cents)}</td>
                  <td className="num">{day.requests}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
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

interface DonutSlice {
  label: string
  cents: number
  pct: number
}

/** Top five captured rows plus Other; the centre total is the sum of the rows. */
function buildDonut(rows: { label: string; captured_cents: number }[]): {
  slices: DonutSlice[]
  total: number
} {
  const positive = rows
    .filter((row) => row.captured_cents > 0)
    .sort((a, b) => b.captured_cents - a.captured_cents)
  const total = positive.reduce((sum, row) => sum + row.captured_cents, 0)
  if (!total) return { slices: [], total: 0 }
  const slices: DonutSlice[] = positive.slice(0, 5).map((row) => ({
    label: row.label,
    cents: row.captured_cents,
    pct: (row.captured_cents / total) * 100,
  }))
  if (positive.length > 5) {
    const other = positive.slice(5).reduce((sum, row) => sum + row.captured_cents, 0)
    slices.push({ label: 'Other', cents: other, pct: (other / total) * 100 })
  }
  return { slices, total }
}

/**
 * Captured share, in the same navy family as the rest of the data. Categories are
 * the real split; agent is the fallback when a workspace has no category spend.
 */
function DonutBody({ data, kind }: { data: AnalyticsData; kind: RangeKind }) {
  const category = buildDonut(data.by_category)
  const fallback = buildDonut(data.by_agent)
  const usingAgents = category.total === 0 && fallback.total > 0
  const { slices, total } = usingAgents ? fallback : category
  const dimension = usingAgents ? 'agent' : 'category'
  const when = kind === 'month' ? 'this month' : 'this range'
  const unit = usingAgents
    ? slices.length === 1
      ? 'agent'
      : 'agents'
    : slices.length === 1
      ? 'category'
      : 'categories'
  const note = slices.length > 5 ? 'top 5 + other' : `${slices.length} ${unit}`

  if (!total) {
    return (
      <>
        <header className="card-head">
          <h2>{usingAgents ? 'By agent' : 'By category'}</h2>
        </header>
        <p className="donut-empty">No captured spend in this range.</p>
      </>
    )
  }

  return (
    <>
      <header className="card-head">
        <h2>{usingAgents ? 'By agent' : 'By category'}</h2>
        <span className="card-note mono">{note}</span>
      </header>
      <div className="donut-layout">
        <div
          className="donut-figure"
          role="img"
          aria-label={`Captured spend by ${dimension}: ${money(total)}. ${slices
            .map((slice) => `${slice.label} ${money(slice.cents)}`)
            .join(', ')}`}
        >
          <svg viewBox="0 0 120 120" aria-hidden="true" focusable="false">
            <g transform="rotate(-90 60 60)">
              {slices.map((slice, index) => {
                const offset = slices
                  .slice(0, index)
                  .reduce((sum, prior) => sum + prior.pct, 0)
                return (
                  <circle
                    key={`${slice.label}-${index}`}
                    className={`donut-seg donut-seg-${index}`}
                    cx="60"
                    cy="60"
                    r="48"
                    pathLength={100}
                    strokeDasharray={`${slice.pct} ${100 - slice.pct}`}
                    strokeDashoffset={-offset}
                  />
                )
              })}
            </g>
          </svg>
          <div className="donut-centre" aria-hidden="true">
            <span className="donut-total">{money(total)}</span>
            <span className="donut-centre-label">captured</span>
          </div>
        </div>
        <ul className="donut-legend">
          {slices.map((slice, index) => (
            <li key={`${slice.label}-${index}`}>
              <span className={`donut-swatch donut-swatch-${index}`} aria-hidden="true" />
              <span className="donut-name" title={slice.label}>
                {slice.label}
              </span>
              <span className="donut-amount">{money(slice.cents)}</span>
              <span className="donut-pct">{slice.pct.toFixed(1)}%</span>
            </li>
          ))}
        </ul>
      </div>
      <p className="donut-caption">
        captured by {dimension} · {when}
      </p>
    </>
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
  const [reloadTick, setReloadTick] = useState(0)
  const { tick, markLoaded } = useFreshness()
  const reduce = useReducedMotion()
  const m = motionSet(reduce)

  useEffect(() => {
    let live = true
    setData(null)
    setError(null)
    api
      .analytics(range.from, range.to)
      .then((next) => {
        if (!live) return
        setData(next)
        markLoaded()
      })
      .catch(() => {
        if (live) setError('The aggregates did not load.')
      })
    return () => {
      live = false
    }
  }, [range.from, range.to, tick, reloadTick, markLoaded])

  const totals = data?.totals
  const held = totals ? totals.awaiting + totals.approved : 0

  return (
    <motion.div className="screen analytics" variants={m.stagger} initial="hidden" animate="show">
      <motion.header className="screen-head analytics-head" variants={m.riseIn}>
        <div>
          <h1>Analytics</h1>
          <p>What agents captured, held, and were stopped from spending.</p>
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

      <motion.div className="range-row" variants={m.riseIn}>
        <p className="range-label mono">{wordsRange(range.from, range.to)}</p>
        <FreshnessButton />
      </motion.div>

      {error ? (
        <InlineError onRetry={() => setReloadTick((value) => value + 1)}>{error}</InlineError>
      ) : null}

      {!data && !error ? (
        <div className="analytics-skel" aria-hidden="true">
          <div className="money-lead is-skel">
            <span className="skel" style={{ width: 180, height: 12 }} />
            <span className="skel" style={{ width: 220, height: 40, marginTop: 10 }} />
            <span className="skel" style={{ width: 140, height: 12, marginTop: 8 }} />
          </div>
          <div className="card">
            <span className="skel" style={{ width: '28%', height: 16 }} />
            <span className="skel" style={{ width: '100%', height: 150, marginTop: 14 }} />
          </div>
          <div className="analytics-grid" aria-hidden="true">
            {[0, 1].map((col) => (
              <div className="card" key={col}>
                <span className="skel" style={{ width: '36%', height: 16 }} />
                <span className="skel" style={{ width: '100%', height: 64, marginTop: 12 }} />
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {data && totals ? (
        <>
          <motion.section className="money-lead" variants={m.riseIn}>
            <div className="money-main">
              <span className="money-label">Settled</span>
              <span className={`money-figure${totals.captured_cents > 0 ? ' is-pos' : ''}`}>
                {money(totals.captured_cents)}
              </span>
              <span className="money-note">
                {totals.captured} {totals.captured === 1 ? 'capture' : 'captures'} through PayPal
              </span>
            </div>
            <dl className="money-facts">
              <div>
                <dt>Held for a signature</dt>
                <dd>{money(totals.pending_cents)}</dd>
                <span>
                  {held} {held === 1 ? 'request' : 'requests'} not yet captured
                </span>
              </div>
              <div>
                <dt>Stopped</dt>
                <dd>{money(totals.blocked_cents)}</dd>
                <span>
                  {totals.blocked} blocked · {totals.vetoed} vetoed
                </span>
              </div>
              <div>
                <dt>Requests</dt>
                <dd>{totals.requests}</dd>
                <span>
                  {totals.auto} auto · {totals.review} review · {totals.block} blocked
                </span>
              </div>
            </dl>
          </motion.section>

          <motion.div variants={m.riseIn}>
            <SeriesChart data={data} />
          </motion.div>

          <div className="analytics-grid">
            <motion.section className="card" variants={m.riseIn}>
              <header className="card-head">
                <h2>By agent</h2>
                <span className="card-note mono">settled</span>
              </header>
              <AgentRows data={data} />
            </motion.section>

            <motion.section className="card donut-card" variants={m.riseIn}>
              <DonutBody data={data} kind={kind} />
            </motion.section>

            <motion.section className="card" variants={m.riseIn}>
              <header className="card-head">
                <h2>By vendor</h2>
                <span className="card-note mono">settled</span>
              </header>
              <SpendRows rows={data.by_vendor} empty="No vendor was paid in this range." />
            </motion.section>

            <motion.section className="card" variants={m.riseIn}>
              <header className="card-head">
                <h2>What the rules caught</h2>
                <span className="card-note mono">triggers</span>
              </header>
              <RuleRows data={data} />
              <p className="card-footnote">
                A request can trigger several rules, so these counts overlap.
              </p>
            </motion.section>
          </div>

        </>
      ) : null}
    </motion.div>
  )
}
