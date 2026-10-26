import { useEffect, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { motion, useReducedMotion } from 'motion/react'
import { api, ApiError, money, type Me, type RequestDetail, type RequestEvent } from '../api'
import { CopyValue } from '../components/Bits'
import { FreshnessButton, useFreshness } from '../components/freshness'
import { motionSet } from '../components/motion'
import {
  RuleFlag,
  StatusMark,
  STATUS,
  sourceLabel,
  requestLabel,
  when,
  whenFull,
} from '../components/request'
import { State, paymentMode, paymentStatus } from '../components/state'

type Action = 'approve' | 'veto' | 'void'

/** Rule messages are audit text; the rule's own figures sit beside them. */
function ReasonList({ record, showHints }: { record: RequestDetail; showHints: boolean }) {
  if (!record.reasons.length) {
    return (
      <p className="verdict-clean">
        No rule fired; the cart was inside every cap and allowlist.
      </p>
    )
  }
  return (
    <ul className="reason-list verdict-reasons">
      {record.reasons.map((reason, index) => (
        <li key={`${reason.rule}-${index}`}>
          <RuleFlag rule={reason.rule} severity={reason.severity} />
          <span className="reason-text">{reason.message}</span>
          {reason.observed_cents !== null && reason.observed_cents !== undefined ? (
            <span className="reason-figure mono">
              observed {money(reason.observed_cents)}
              {reason.limit_cents !== null && reason.limit_cents !== undefined
                ? ` · limit ${money(reason.limit_cents)}`
                : ''}
            </span>
          ) : null}
          {showHints && reason.unblock ? <span className="reason-unblock">{reason.unblock}</span> : null}
        </li>
      ))}
    </ul>
  )
}

/** The last human step, as the trail recorded it. */
function actorEvent(record: RequestDetail): RequestEvent | undefined {
  return [...record.events]
    .reverse()
    .find((event) => event.kind === 'approve' || event.kind === 'veto' || event.kind === 'void')
}

function actorOf(record: RequestDetail): string | null {
  const event = actorEvent(record)
  if (!event || !event.detail) return null
  const who = (event.detail.replace(/^by /, '').split(' · ')[0] ?? '').trim()
  return who || null
}

function consentLapsed(record: RequestDetail): boolean {
  const expires = record.consent?.expires_at
  return Boolean(record.status === 'awaiting' && expires && new Date(expires) <= new Date())
}

function verdictHeadline(record: RequestDetail): string {
  const actor = actorOf(record)
  if (record.status === 'captured') {
    if (actor) return `${actor} signed it; PayPal captured the money.`
    return 'Inside every rule; PayPal captured the money.'
  }
  if (record.status === 'blocked') return 'The rules stopped this cart.'
  if (record.status === 'vetoed') {
    return actor ? `${actor} vetoed this cart. Nothing moved.` : 'Vetoed. Nothing moved.'
  }
  if (record.status === 'voided') {
    return actor ? `${actor} voided this request. Nothing was captured.` : 'Withdrawn before capture.'
  }
  if (record.status === 'approved') return 'Signed; the capture is still owed.'
  if (consentLapsed(record)) return 'The consent expired before anyone signed.'
  return 'Waiting for a signature.'
}

/** A document section: title on the left, one small fact on the right. */
function DocSection({
  title,
  note,
  children,
  className,
}: {
  title: string
  note?: string
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`doc-section${className ? ` ${className}` : ''}`}>
      <header className="doc-section-head">
        <h2>{title}</h2>
        {note ? <span className="doc-note mono">{note}</span> : null}
      </header>
      {children}
    </section>
  )
}

function PayPalSection({ record }: { record: RequestDetail }) {
  const payment = record.payment
  const status = payment ? paymentStatus(payment.status) : null
  const mode = paymentMode(payment?.mode)
  const steps = [
    { label: 'Order', id: payment?.order_id ?? null, at: payment?.created_at ?? null },
    { label: 'Authorization', id: payment?.authorization_id ?? null, at: payment?.updated_at ?? null },
    { label: 'Capture', id: payment?.capture_id ?? null, at: payment?.updated_at ?? null },
  ]
  if (!payment) {
    return (
      <DocSection title="PayPal">
        <p className="doc-none">No PayPal object exists; no money moved.</p>
      </DocSection>
    )
  }
  return (
    <DocSection
      title="PayPal"
      note={`${mode}${payment.updated_at ? ` · ${when(payment.updated_at)}` : ''}`}
    >
      {status ? (
        <div className="pay-status">
          <State tone={status.tone}>PayPal {status.label}</State>
        </div>
      ) : null}
      <ol className="pay-steps">
        {steps.map((step) => (
          <li className="pay-step" key={step.label}>
            <span className="pay-label">{step.label}</span>
            {step.id ? (
              <CopyValue value={step.id} label={`Copy the PayPal ${step.label.toLowerCase()} id`} />
            ) : (
              <span className="pay-missing">Not created</span>
            )}
            {step.id && step.at ? <span className="pay-at mono">{when(step.at)}</span> : null}
          </li>
        ))}
      </ol>
      <p className="doc-footnote">Only the capture moves money.</p>
    </DocSection>
  )
}

function ConsentSection({ record }: { record: RequestDetail }) {
  const consent = record.consent
  if (!consent) {
    return (
      <DocSection title="Consent">
        <p className="doc-none">No consent was issued.</p>
      </DocSection>
    )
  }
  const used = consent.used_at
    ? whenFull(consent.used_at)
    : consent.expires_at && new Date(consent.expires_at) <= new Date()
      ? 'Expired, unused'
      : 'Not yet'
  return (
    <DocSection title="Consent" note="Single use · 10 minutes" className="consent-seal">
      <div className="seal-hash">
        <span className="seal-label">Cart hash</span>
        <CopyValue value={consent.cart_hash} label="Copy the cart hash" />
      </div>
      <dl className="seal-grid">
        <div>
          <dt>Issued</dt>
          <dd className="mono">{whenFull(consent.issued_at)}</dd>
        </div>
        <div>
          <dt>Expires</dt>
          <dd className="mono">{whenFull(consent.expires_at)}</dd>
        </div>
        <div>
          <dt>Used</dt>
          <dd className="mono">{used}</dd>
        </div>
      </dl>
      <p className="doc-footnote">Its nonce is PayPal&apos;s idempotency key.</p>
    </DocSection>
  )
}

function TrailSection({ record }: { record: RequestDetail }) {
  return (
    <DocSection title="Trail" note={`${record.events.length} entries · append-only`}>
      <ol className="doc-trail">
        {record.events.map((event) => (
          <li key={event.seq}>
            <span className="trail-seq mono">{String(event.seq).padStart(2, '0')}</span>
            <span className="trail-body">
              <span className="trail-label">{event.label}</span>
              {event.detail ? <span className="trail-detail">{event.detail}</span> : null}
            </span>
            <span className="trail-at mono">{when(event.at)}</span>
          </li>
        ))}
      </ol>
    </DocSection>
  )
}

/**
 * Record: one request as a signed page — the amount and decision first, then the
 * cart, the PayPal objects, the consent seal and the append-only trail.
 */
export function Record({ me }: { me: Me }) {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const [record, setRecord] = useState<RequestDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [approveUrl, setApproveUrl] = useState<string | null>(null)
  const [flash, setFlash] = useState<string | null>(null)
  const [busy, setBusy] = useState<Action | null>(null)
  const [pendingAction, setPendingAction] = useState<Action | null>(null)
  const [reloadTick, setReloadTick] = useState(0)
  const [resending, setResending] = useState(false)
  const { tick, markLoaded } = useFreshness()
  const reduce = useReducedMotion()
  const m = motionSet(reduce)

  useEffect(() => {
    setRecord(null)
    setError(null)
  }, [id])

  useEffect(() => {
    let live = true
    api
      .request(id)
      .then((data) => {
        if (!live) return
        setRecord(data)
        markLoaded()
      })
      .catch((err) => {
        if (!live) return
        setError(err instanceof ApiError ? err.message : 'That record could not be opened.')
      })
    return () => {
      live = false
    }
  }, [id, reloadTick, tick, markLoaded])

  const askAct = (action: Action) => {
    setActionError(null)
    setPendingAction(action)
  }

  const again = async () => {
    if (!record) return
    setResending(true)
    setActionError(null)
    try {
      const next = await api.createRequest(record.text, record.agent.id)
      navigate(`/inbox/${next.id}`)
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'The request was not sent again.')
    } finally {
      setResending(false)
    }
  }

  const act = async (action: Action) => {
    setPendingAction(null)
    setBusy(action)
    setActionError(null)
    setApproveUrl(null)
    setFlash(null)
    try {
      const next =
        action === 'approve'
          ? await api.decide(id, 'approve')
          : action === 'veto'
            ? await api.decide(id, 'veto')
            : await api.voidRequest(id)
      setRecord(next)
      setFlash(action === 'approve' ? 'Captured.' : action === 'veto' ? 'Vetoed.' : 'Voided.')
    } catch (err) {
      if (err instanceof ApiError) {
        setActionError(err.message)
        setApproveUrl(err.approveUrl ?? null)
      } else {
        setActionError('The action did not go through.')
      }
    } finally {
      setBusy(null)
    }
  }

  const canAct = me.user.role === 'admin' || me.user.role === 'approver'
  const expired = Boolean(record && consentLapsed(record))
  const awaiting = record?.status === 'awaiting'
  const approved = record?.status === 'approved'
  const canDecide = canAct && awaiting && !expired
  const canVeto = canAct && awaiting
  const canVoid = canAct && (awaiting || approved)
  const canResend = canAct && awaiting && expired
  const showActions = Boolean(awaiting || (approved && canAct))
  const tone = record ? (STATUS[record.status]?.tone ?? 'quiet') : 'quiet'
  const expiredLine = 'Consent expired — send it again for a new window.'

  return (
    <motion.div
      className="screen record-doc"
      variants={m.stagger}
      initial="hidden"
      animate="show"
    >
      <motion.div className="back-row" variants={m.riseIn}>
        <Link className="back-link" to="/inbox">
          ← Inbox
        </Link>
        <FreshnessButton />
      </motion.div>

      {error ? (
        <div className="empty record-error">
          <h1 className="display">Record not found</h1>
          <p className="empty-title">That record is not here.</p>
          <p className="empty-note">{error}</p>
          <div className="empty-actions">
            <button
              type="button"
              className="btn btn-secondary btn-small"
              onClick={() => {
                setError(null)
                setReloadTick((value) => value + 1)
              }}
            >
              Retry
            </button>
            <Link className="text-link mono" to="/inbox">
              back to the inbox →
            </Link>
          </div>
        </div>
      ) : null}

      {!record && !error ? (
        <div className="doc-skel" aria-hidden="true">
          <span className="skel" style={{ width: 170, height: 11 }} />
          <span className="skel" style={{ width: '46%', height: 34, marginTop: 12 }} />
          <span className="skel" style={{ width: '34%', height: 12, marginTop: 12 }} />
          <span className="skel" style={{ width: '100%', height: 108, marginTop: 24 }} />
          <span className="skel" style={{ width: '100%', height: 190, marginTop: 24 }} />
          <span className="skel" style={{ width: '100%', height: 140, marginTop: 24 }} />
        </div>
      ) : null}

      {record ? (
        <motion.article className="doc" variants={m.riseIn}>
          <header className="doc-head">
            <div className="doc-ident">
              <p className="doc-kicker">
                <CopyValue value={record.id} label="Copy the request id">
                  {requestLabel(record.seq)}
                </CopyValue>
                <span className="doc-dot" aria-hidden="true" />
                <span>{sourceLabel(record.origin, record.routine?.name)}</span>
                {record.channel ? (
                  <>
                    <span className="doc-dot" aria-hidden="true" />
                    <span className="mono">{record.channel}</span>
                  </>
                ) : null}
              </p>
              <h1 className="display doc-title">{record.title}</h1>
              <div className="doc-agent">
                <span className="doc-agent-name">{record.agent.name}</span>
                <span className="chip-quiet mono">{record.agent.tag}</span>
                <span className="doc-dot" aria-hidden="true" />
                <span className="doc-meta">
                  asked by {record.requester ?? 'an agent'}
                  <span className="doc-dot" aria-hidden="true" />
                  <span className="mono">{whenFull(record.created_at)}</span>
                </span>
              </div>
              {record.text ? <p className="doc-ask">“{record.text}”</p> : null}
            </div>
            <div className="doc-amount">
              <span className="doc-total">
                {record.lines.length ? money(record.total_cents, record.currency) : 'No cart priced'}
              </span>
              <StatusMark status={record.status} />
            </div>
          </header>

          <section className={`verdict is-${tone}`}>
            <div className="verdict-main">
              <p className="verdict-label">Verdict</p>
              <p className="verdict-title">{verdictHeadline(record)}</p>
              <ReasonList
                record={record}
                showHints={awaiting || approved || record.status === 'blocked'}
              />
              {record.status === 'captured' && record.reasons.length ? (
                <p className="verdict-clean">
                  The signature was the exception; no cap was edited.
                </p>
              ) : null}
            </div>

            {showActions ? (
            <div className="verdict-actions">
              {canAct && awaiting ? (
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => askAct('approve')}
                  disabled={!canDecide || busy !== null || pendingAction !== null}
                >
                  {busy === 'approve' ? 'Capturing…' : 'Approve & capture'}
                </button>
              ) : null}
              {canResend ? (
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => void again()}
                  disabled={busy !== null || resending}
                >
                  {resending ? 'Sending…' : 'Send it again'}
                </button>
              ) : null}
              {canVeto ? (
                <button
                  type="button"
                  className="btn btn-danger"
                  onClick={() => askAct('veto')}
                  disabled={busy !== null || pendingAction !== null}
                >
                  {busy === 'veto' ? 'Vetoing…' : 'Veto'}
                </button>
              ) : null}
              {canVoid ? (
                <button
                  type="button"
                  className="btn btn-quiet"
                  onClick={() => askAct('void')}
                  disabled={busy !== null || pendingAction !== null}
                >
                  {busy === 'void' ? 'Voiding…' : 'Void'}
                </button>
              ) : null}
              {!canAct && awaiting ? (
                <p className="verdict-role">An approver or admin signs this cart.</p>
              ) : null}
              {expired ? <p className="verdict-lock">{expiredLine}</p> : null}

              {pendingAction ? (
                <div className="verdict-confirm">
                  <p className="verdict-confirm-copy">
                    {pendingAction === 'approve'
                      ? `Capture ${money(record.total_cents, record.currency)} from the workspace wallet? This moves money.`
                      : pendingAction === 'veto'
                        ? 'Refuse this request? The veto is written to the trail.'
                        : 'Withdraw this request? The void stays in the record.'}
                  </p>
                  <div className="verdict-confirm-actions">
                    <button
                      type="button"
                      className="btn btn-primary"
                      onClick={() => void act(pendingAction)}
                      disabled={busy !== null}
                    >
                      {busy !== null
                        ? 'Working…'
                        : pendingAction === 'approve'
                          ? 'Confirm capture'
                          : pendingAction === 'veto'
                            ? 'Confirm veto'
                            : 'Confirm void'}
                    </button>
                    <button
                      type="button"
                      className="btn btn-quiet"
                      onClick={() => setPendingAction(null)}
                      disabled={busy !== null}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
            ) : null}
          </section>

          {actionError ? (
            <p className="inline-error" role="alert">
              {actionError}{' '}
              {approveUrl ? (
                <a className="text-link" href={approveUrl} target="_blank" rel="noreferrer">
                  Connect the wallet →
                </a>
              ) : null}
            </p>
          ) : null}
          {flash ? (
            <p className="record-flash" role="status">
              {flash}
            </p>
          ) : null}

          <DocSection
            title="Cart"
            note={`${record.lines.length} ${record.lines.length === 1 ? 'line' : 'lines'}`}
          >
            {record.lines.length ? (
              <div className="cart-lines">
                <div className="cart-head" aria-hidden="true">
                  <span>Item</span>
                  <span>Vendor</span>
                  <span className="num">Qty</span>
                  <span className="num">Unit</span>
                  <span className="num">Amount</span>
                </div>
                {record.lines.map((line, index) => (
                  <div className="cart-line" key={`${line.sku ?? line.title}-${index}`}>
                    <span className="cart-item">
                      <b>{line.title}</b>
                      <span className="cart-meta mono">
                        {line.category ?? 'uncategorised'}
                        {line.brand ? ` · ${line.brand}` : ''}
                      </span>
                    </span>
                    <span className="cart-vendor">{line.vendor ?? 'unknown'}</span>
                    <span className="cart-num cart-qty">
                      <span className="cart-label">Qty</span>
                      {line.qty}
                    </span>
                    <span className="cart-num cart-unit">
                      <span className="cart-label">Unit</span>
                      {money(line.unit_cents)}
                    </span>
                    <span className="cart-num cart-amount">
                      <span className="cart-label">Amount</span>
                      {money(line.line_cents)}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="doc-none">No cart was priced.</p>
            )}
          </DocSection>

          <PayPalSection record={record} />
          <ConsentSection record={record} />
          <TrailSection record={record} />
        </motion.article>
      ) : null}
    </motion.div>
  )
}
