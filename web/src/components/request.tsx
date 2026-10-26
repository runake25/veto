/**
 * One vocabulary for a request, shared by Inbox, Record and Overview: status words,
 * rule names, money and times. A fact lives here once and is rendered everywhere.
 */
import type { RequestStatus } from '../api'
import { State, type Tone } from './state'

export const STATUS: Record<
  RequestStatus,
  { label: string; tone: Tone; note: string }
> = {
  awaiting: {
    label: 'Needs you',
    tone: 'wait',
    note: 'The rules held this cart for a signature.',
  },
  approved: {
    label: 'Approved',
    tone: 'wait',
    note: 'Signed, with the capture still owed.',
  },
  captured: {
    label: 'Captured',
    tone: 'pos',
    note: 'The money moved through PayPal.',
  },
  blocked: {
    label: 'Blocked',
    tone: 'neg',
    note: 'The rules refused this cart before consent. No money moved.',
  },
  vetoed: {
    label: 'Vetoed',
    tone: 'neg',
    note: 'A person refused this cart. No money moved.',
  },
  voided: {
    label: 'Voided',
    tone: 'quiet',
    note: 'Withdrawn before any capture.',
  },
}

export const STATUS_ORDER: RequestStatus[] = [
  'awaiting',
  'approved',
  'captured',
  'blocked',
  'vetoed',
  'voided',
]

export const RULE_WORD: Record<string, string> = {
  catalogue: 'catalogue',
  category_allowlist: 'category',
  vendor_allowlist: 'merchant',
  monthly_cap: 'monthly cap',
  per_order_cap: 'per-order cap',
  per_unit_cap: 'per-item cap',
  approval_threshold: 'signature',
}

export function ruleWord(rule: string): string {
  return RULE_WORD[rule] ?? rule.replaceAll('_', ' ')
}

export const ORIGIN: Record<string, string> = {
  slack: 'Slack',
  web: 'Web',
  api: 'API key',
  web_sim: 'Dry run',
  seed: 'Seed',
}

export function originLabel(origin: string | null | undefined, channel?: string | null): string {
  if (channel) return channel
  if (!origin) return '—'
  return ORIGIN[origin] ?? origin
}

/**
 * Where a request came from. A standing job outranks the transport that carried
 * it: a routine run is a routine run even though the runner posts it over HTTP.
 */
export function sourceLabel(
  origin: string | null | undefined,
  routineName?: string | null,
): string {
  if (routineName) return 'Routine'
  return originLabel(origin)
}

export function requestLabel(seq: number): string {
  return `REQ-${seq}`
}

const dayShort = new Intl.DateTimeFormat('en-US', { day: 'numeric', month: 'short' })
const dayFull = new Intl.DateTimeFormat('en-US', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
})
const clock = new Intl.DateTimeFormat('en-US', {
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

/** "23 Oct · 14:12" — the one-line timestamp used in lists. */
export function when(iso: string | null | undefined): string {
  const date = iso ? new Date(iso) : null
  if (!date || Number.isNaN(date.getTime())) return '—'
  return `${dayShort.format(date)} · ${clock.format(date)}`
}

/** "23 Oct 2026 · 14:12" — the record's full stamp. */
export function whenFull(iso: string | null | undefined): string {
  const date = iso ? new Date(iso) : null
  if (!date || Number.isNaN(date.getTime())) return '—'
  return `${dayFull.format(date)} · ${clock.format(date)}`
}

/** "23 Oct" — for day axes. */
export function dayLabel(iso: string): string {
  const date = new Date(`${iso}T00:00:00Z`)
  if (Number.isNaN(date.getTime())) return iso
  return dayShort.format(date)
}

export function StatusMark({ status, label }: { status: RequestStatus; label?: string }) {
  const meta = STATUS[status] ?? STATUS.voided
  return <State tone={meta.tone}>{label ?? meta.label}</State>
}

/** The severity flag the policy engine attaches to a rule. */
export function RuleFlag({ rule, severity }: { rule: string; severity: 'block' | 'review' }) {
  return (
    <span className={`rule-flag ${severity === 'block' ? 'is-neg' : 'is-wait'}`}>
      {ruleWord(rule)}
    </span>
  )
}

/** How long something has been waiting, in the shortest honest unit: "6h", "3d". */
export function age(iso: string | null | undefined): string {
  const then = iso ? new Date(iso).getTime() : Number.NaN
  if (Number.isNaN(then)) return '—'
  const minutes = Math.max(0, Math.round((Date.now() - then) / 60_000))
  if (minutes < 1) return 'now'
  if (minutes < 60) return `${minutes}m`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h`
  const days = Math.round(hours / 24)
  if (days < 30) return `${days}d`
  return `${Math.round(days / 30)}mo`
}
