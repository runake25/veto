import { useState, type ReactNode } from 'react'
import { money } from '../api'

/**
 * A ledger value a person may need elsewhere: the id stays readable and a copy
 * button sits beside it. Clipboard failures are silent on purpose — the value is
 * still on screen to select by hand.
 */
export function CopyValue({
  value,
  label = 'Copy',
  children,
}: {
  value: string
  /** Accessible name for the button: "Copy capture id". */
  label?: string
  children?: ReactNode
}) {
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
    } catch {
      return
    }
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1600)
  }

  return (
    <span className="copy-value">
      <span className="copy-value-text mono" title={value}>
        {children ?? value}
      </span>
      <button
        type="button"
        className="copy-btn mono"
        onClick={() => void copy()}
        aria-label={label}
        title={label}
      >
        {copied ? 'copied' : 'copy'}
      </button>
    </span>
  )
}

/** An error the screen can recover from: the message and the way back. */
export function InlineError({
  children,
  onRetry,
  retryLabel = 'Retry',
  className,
}: {
  children: ReactNode
  onRetry?: () => void
  retryLabel?: string
  className?: string
}) {
  return (
    <p className={`inline-error${className ? ` ${className}` : ''}`} role="alert">
      <span className="inline-error-text">{children}</span>
      {onRetry ? (
        <button type="button" className="btn btn-secondary btn-small" onClick={onRetry}>
          {retryLabel}
        </button>
      ) : null}
    </p>
  )
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
