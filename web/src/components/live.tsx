/**
 * The live panel both a dry run and a real job run show while the pipeline
 * works. The frames are the server's own progress events — pipeline stages,
 * then (for a real run) the consent, the PayPal order, authorization and
 * capture — so the console never invents a step that did not happen.
 */
export type LiveStep = { kind: string; label: string; detail?: string | null }
export type LiveItem = { index: number; name: string; ok: boolean; title?: string | null }
export type Live = { steps: LiveStep[]; items: LiveItem[] }

/* What comes next, keyed by the stage that just landed. */
const NEXT_LABEL: Record<string, string> = {
  extract: 'Searching and choosing…',
  catalogue: 'Pricing the cart…',
  price: 'Applying the rules…',
  decision: 'Recording the request…',
  record: 'Writing the consent…',
  consent: 'Settling with PayPal…',
  wallet: 'Waiting for the wallet…',
  order: 'Authorizing the payment…',
  auth: 'Capturing the money…',
  capture: 'Recording the result…',
  hold: 'Recording the hold…',
  block: 'Recording the refusal…',
  capture_failed: 'Recording the failure…',
}

export function liveNextLabel(steps: LiveStep[]): string {
  if (!steps.length) return 'Reading the request…'
  const last = steps[steps.length - 1]?.kind ?? ''
  return NEXT_LABEL[last] ?? 'Finishing…'
}

export function PipelineLive({
  steps,
  items,
  title,
  note,
}: {
  steps: LiveStep[]
  items: LiveItem[]
  title: string
  note: string
}) {
  return (
    <div className="dry-live" aria-live="polite">
      <div className="dry-live-head">
        <span className="live-dot" aria-hidden="true" />
        <span>{title}</span>
        <span className="dry-live-note">{note}</span>
      </div>
      {items.length ? (
        <ul className="live-items">
          {items.map((item) => (
            <li key={item.index} className={item.ok ? 'is-ok' : 'is-miss'}>
              <span className="mono live-index">
                {String(item.index + 1).padStart(2, '0')}
              </span>
              <span className="live-name">{item.ok ? item.title : item.name}</span>
              <span className="live-state">{item.ok ? 'found' : 'no match'}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <ol className="mini-rail">
        {steps.map((step, i) => (
          <li key={`${step.kind}-${i}`}>
            <span className="mini-step mono">{String(i + 1).padStart(2, '0')}</span>
            <span className="mini-label">{step.label}</span>
            {step.detail ? <span className="mini-detail mono">{step.detail}</span> : null}
          </li>
        ))}
        <li className="is-pending">
          <span className="mini-step mono">{String(steps.length + 1).padStart(2, '0')}</span>
          <span className="mini-label">{liveNextLabel(steps)}</span>
        </li>
      </ol>
    </div>
  )
}
