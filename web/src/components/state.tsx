/**
 * One status language for the whole console: a dot and a word, four tones.
 *
 * The rule the screens already followed — red is a refusal signal, never
 * decoration — is enforced here, once. Request status, agent state, job state,
 * run outcome, integration state and payment status all run through `State`,
 * so a colour cannot mean two things and a state cannot be a pill on one screen
 * and a dot on the next.
 */
import type { ReactNode } from 'react'
import type { Agent, RoutineOutcome, RoutineSummary } from '../api'

export type Tone = 'pos' | 'wait' | 'neg' | 'quiet'

export function State({
  tone,
  children,
  lead = false,
  className,
}: {
  tone: Tone
  children: ReactNode
  /** The one larger instance: a dry run's verdict, read before the details. */
  lead?: boolean
  className?: string
}) {
  return (
    <span className={`state is-${tone}${lead ? ' is-lead' : ''}${className ? ` ${className}` : ''}`}>
      <span className="state-dot" aria-hidden="true" />
      {children}
    </span>
  )
}

/* A state word starts a sentence, so it is capitalised; the stored value is not. */
function word(raw: string): string {
  return raw
    .toLowerCase()
    .replaceAll('_', ' ')
    .replace(/^\w/, (letter) => letter.toUpperCase())
}

/** Active, paused, or a charter that cannot buy anything at all. */
export function agentState(agent: Pick<Agent, 'status' | 'rules' | 'categories' | 'vendors'>): {
  label: string
  tone: Tone
} {
  if (agent.status === 'paused') return { label: 'Paused', tone: 'quiet' }
  const on = new Set(agent.rules)
  const cannotBuy =
    (on.has('category_allowlist') && agent.categories.length === 0) ||
    (on.has('vendor_allowlist') && agent.vendors.length === 0)
  if (cannotBuy) return { label: "Can't buy", tone: 'neg' }
  return { label: 'Active', tone: 'pos' }
}

export function routineState(routine: Pick<RoutineSummary, 'status'>): {
  label: string
  tone: Tone
} {
  return routine.status === 'active'
    ? { label: 'Active', tone: 'pos' }
    : { label: 'Paused', tone: 'quiet' }
}

/**
 * A run outcome in the words the record would use. A run that captured says
 * "Ordered"; a run that only reached a human says so; a skip is quiet because
 * nothing happened and no money moved.
 */
export const RUN_STATE: Record<RoutineOutcome, { label: string; tone: Tone }> = {
  captured: { label: 'Ordered', tone: 'pos' },
  awaiting: { label: 'Needs signature', tone: 'wait' },
  approved: { label: 'Approved', tone: 'wait' },
  blocked: { label: 'Blocked', tone: 'neg' },
  skipped: { label: 'Skipped', tone: 'quiet' },
  failed: { label: 'Failed', tone: 'neg' },
}

export function runState(outcome: RoutineOutcome): { label: string; tone: Tone } {
  return RUN_STATE[outcome] ?? { label: word(outcome), tone: 'quiet' }
}

const PAYMENT_WORD: Record<string, string> = {
  COMPLETED: 'Completed',
  APPROVED: 'Approved',
  CREATED: 'Created',
  PENDING: 'Pending',
  PARTIALLY_CAPTURED: 'Partially captured',
  DECLINED: 'Declined',
  FAILED: 'Failed',
  VOIDED: 'Voided',
  WALLET_REQUIRED: 'Wallet required',
}

/** PayPal's enum, as a person reads it. The raw value stays in the title text. */
export function paymentStatus(status: string): { label: string; tone: Tone } {
  const key = status.toUpperCase()
  if (key === 'COMPLETED') return { label: 'Completed', tone: 'pos' }
  if (key === 'FAILED' || key === 'DECLINED') {
    return { label: PAYMENT_WORD[key] ?? word(status), tone: 'neg' }
  }
  if (key === 'WALLET_REQUIRED' || key === 'PENDING') {
    return { label: PAYMENT_WORD[key] ?? word(status), tone: 'wait' }
  }
  return { label: PAYMENT_WORD[key] ?? word(status), tone: 'quiet' }
}

/** "sandbox" is a PayPal sandbox; "mock" is the in-process simulator. */
export function paymentMode(mode: string | null | undefined): string {
  if (!mode) return 'No payment'
  const key = mode.toLowerCase()
  if (key === 'sandbox') return 'PayPal sandbox'
  if (key === 'live' || key === 'paypal') return 'PayPal live'
  if (key === 'mock' || key === 'simulator') return 'In-process simulator'
  return word(mode)
}
