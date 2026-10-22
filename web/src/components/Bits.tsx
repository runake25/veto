import { money } from '../api'

/** Outcome pill: one shape, three tones. Red is a refusal signal, never decoration. */
export function OutcomePill({
  outcome,
  dry = false,
}: {
  outcome: 'auto' | 'review' | 'block'
  dry?: boolean
}) {
  const label = dry
    ? outcome === 'auto'
      ? 'Would pay automatically'
      : outcome === 'review'
        ? 'Would wait for a signature'
        : 'Refused by a rule'
    : outcome === 'auto'
      ? 'Auto-approved'
      : outcome === 'review'
        ? 'Needs a signature'
        : 'Blocked'
  const tone = outcome === 'auto' ? 'is-pos' : outcome === 'review' ? 'is-wait' : 'is-neg'
  return <span className={`pill ${tone}`}>{label}</span>
}

/**
 * The charter track from DESIGN.md: money already spent, money committed but
 * unsigned (paler), and an overrun past the end in the refusal colour.
 */
export function Track({
  spentCents,
  committedCents,
  capCents,
}: {
  spentCents: number
  committedCents: number
  capCents: number | null
}) {
  const cap = capCents && capCents > 0 ? capCents : 0
  const spentPct = cap ? Math.min(100, (spentCents / cap) * 100) : 0
  const committedPct = cap ? Math.min(100 - spentPct, (committedCents / cap) * 100) : 0
  const over = cap > 0 ? Math.max(0, spentCents + committedCents - cap) : 0

  return (
    <div className="track-wrap">
      <div className="track" role="img" aria-label="Budget used, committed and remaining">
        <span className="track-fill" style={{ width: `${spentPct}%` }} />
        <span
          className="track-hold"
          style={{ left: `${spentPct}%`, width: `${committedPct}%` }}
        />
        {over > 0 ? <span className="track-over" /> : null}
      </div>
      <div className="track-legend mono">
        <span>{money(spentCents)} spent</span>
        <span>{money(committedCents)} unsigned</span>
        <span>{cap ? `${money(Math.max(0, cap - spentCents - committedCents))} left` : 'no cap'}</span>
      </div>
    </div>
  )
}
