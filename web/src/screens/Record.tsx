import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { motion, useReducedMotion } from 'motion/react'
import { api, ApiError, money, type Me, type RequestDetail, type RequestEvent } from '../api'
import { motionSet } from '../components/motion'
import {
  RuleFlag,
  StatusMark,
  STATUS,
  originLabel,
  requestLabel,
  when,
  whenFull,
} from '../components/request'

type Action = 'approve' | 'veto' | 'void'

const DECISION_WORD: Record<string, string> = {
  auto: 'inside the rules',
  review: 'needs a signature',
  block: 'refused by the rules',
}

/** Rule messages are audit text, so they are shown verbatim, with the numbers beside them. */
function ReasonList({ record, showHints }: { record: RequestDetail; showHints: boolean }) {
  if (!record.reasons.length) {
    return (
      <p className="verdict-clean">
        No rule fired. The cart sat inside the monthly cap, the per-order and per-item caps, and
        every merchant and category the agent is allowed to use.
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
          {showHints && reason.unblock ? (
            <span className="reason-unblock">{reason.unblock}</span>
          ) : null}
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

function signedLine(record: RequestDetail): string | null {
  const event = actorEvent(record)
  const actor = actorOf(record)
  if (!event || !actor) return null
  return `${event.label} by ${actor} · ${when(event.at)}`
}

function expiredNote(record: RequestDetail): string | null {
  if (record.status !== 'awaiting') return null
  const expires = record.consent?.expires_at
  if (!expires || new Date(expires) > new Date()) return null
  return `The consent expired at ${when(expires)}. Ask the agent for a new request, or veto this one — no money can move.`
}

function verdictHeadline(record: RequestDetail): string {
  const actor = actorOf(record)
  if (record.status === 'captured') {
    if (record.reasons.length === 0) return 'Inside every rule — captured without a signature.'
    return actor
      ? `${actor} approved the review — the capture went through.`
      : 'Approved, then captured through PayPal.'
  }
  if (record.status === 'blocked') return 'The rules stopped this cart before consent.'
  if (record.status === 'vetoed')
    return actor ? `${actor} vetoed this cart. Nothing moved.` : 'A person vetoed this cart. Nothing moved.'
  if (record.status === 'voided')
    return actor ? `${actor} voided this request before capture.` : 'Withdrawn before any capture.'
  if (record.status === 'approved') return 'Signed — the capture is owed.'
  return 'Waiting for a signature.'
}

/** The money trail: every object PayPal returned, or the fact that none exists. */
function PaymentCard({ record }: { record: RequestDetail }) {
  const payment = record.payment
  return (
    <section className="card">
      <header className="card-head">
        <h2>Payment</h2>
        <span className="card-note mono">{payment ? payment.mode : 'none'}</span>
      </header>
      {payment ? (
        <dl className="record-rows">
          <div className="record-row">
            <dt>PayPal status</dt>
            <dd className="record-state mono">{payment.status}</dd>
          </div>
          <div className="record-row">
            <dt>Amount</dt>
            <dd>{money(payment.amount_cents)}</dd>
          </div>
          <div className="record-row">
            <dt>Order</dt>
            <dd className="mono record-id-value">{payment.order_id ?? '—'}</dd>
          </div>
          <div className="record-row">
            <dt>Authorization</dt>
            <dd className="mono record-id-value">{payment.authorization_id ?? '—'}</dd>
          </div>
          <div className="record-row">
            <dt>Capture</dt>
            <dd className="mono record-id-value">{payment.capture_id ?? '—'}</dd>
          </div>
        </dl>
      ) : (
        <p className="record-none">
          No PayPal object exists for this request. The money never moved.
        </p>
      )}
      <p className="card-footnote">
        Order → authorization → capture. The capture is the only step that moves money.
      </p>
    </section>
  )
}

function ConsentCard({ record }: { record: RequestDetail }) {
  const consent = record.consent
  return (
    <section className="card">
      <header className="card-head">
        <h2>Consent</h2>
        <span className="card-note mono">{consent ? 'one use · 10 min' : 'not issued'}</span>
      </header>
      {consent ? (
        <dl className="record-rows">
          <div className="record-row">
            <dt>Cart hash</dt>
            <dd className="mono record-id-value record-hash" title={consent.cart_hash}>
              {consent.cart_hash}
            </dd>
          </div>
          <div className="record-row">
            <dt>Issued</dt>
            <dd className="mono">{whenFull(consent.issued_at)}</dd>
          </div>
          <div className="record-row">
            <dt>Expires</dt>
            <dd className="mono">{whenFull(consent.expires_at)}</dd>
          </div>
          <div className="record-row">
            <dt>Used</dt>
            <dd className="mono">
              {consent.used_at
                ? whenFull(consent.used_at)
                : consent.expires_at && new Date(consent.expires_at) <= new Date()
                  ? 'expired, unused'
                  : 'not yet'}
            </dd>
          </div>
        </dl>
      ) : (
        <p className="record-none">
          Without consent there is nothing to spend. This request never got one.
        </p>
      )}
      <p className="card-footnote">
        The consent is one sha256 of the cart, single use, ten minutes. Its nonce is the PayPal
        idempotency key, so a retried approval cannot charge twice.
      </p>
    </section>
  )
}

function TrailCard({ record }: { record: RequestDetail }) {
  return (
    <section className="card">
      <header className="card-head">
        <h2>Trail</h2>
        <span className="card-note mono">{record.events.length} entries</span>
      </header>
      <ol className="trail">
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
      <p className="card-footnote">Append-only: a step is never rewritten.</p>
    </section>
  )
}

/**
 * Record: one request as a document — the cart, the decision, the consent, the
 * PayPal objects and the trail all read from the same stored row.
 */
export function Record({ me }: { me: Me }) {
  const { id = '' } = useParams()
  const [record, setRecord] = useState<RequestDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [approveUrl, setApproveUrl] = useState<string | null>(null)
  const [flash, setFlash] = useState<string | null>(null)
  const [busy, setBusy] = useState<Action | null>(null)
  const [pendingAction, setPendingAction] = useState<Action | null>(null)
  const [reloadTick, setReloadTick] = useState(0)
  const reduce = useReducedMotion()
  const m = motionSet(reduce)

  useEffect(() => {
    let live = true
    setRecord(null)
    setError(null)
    api
      .request(id)
      .then((data) => {
        if (live) setRecord(data)
      })
      .catch((err) => {
        if (!live) return
        setError(err instanceof ApiError ? err.message : 'That record could not be opened.')
      })
    return () => {
      live = false
    }
  }, [id, reloadTick])

  const askAct = (action: Action) => {
    setActionError(null)
    setPendingAction(action)
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
      setFlash(
        action === 'approve'
          ? next.payment?.capture_id
            ? `Approved and captured. Capture ${next.payment.capture_id} is in the record.`
            : 'Approved. The capture is in the record.'
          : action === 'veto'
            ? 'Vetoed. No money moved.'
            : 'Voided. No capture was attempted.',
      )
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
  const consentExpired = Boolean(
    record?.consent?.expires_at && new Date(record.consent.expires_at) <= new Date(),
  )
  const canDecide = canAct && record?.status === 'awaiting' && !consentExpired
  // Void is housekeeping, not a decision: it is offered only when it adds something —
  // a stale (expired) consent, or an approval whose capture is still owed.
  const canVoid =
    canAct && (record?.status === 'approved' || (record?.status === 'awaiting' && consentExpired))
  const tone = record ? (STATUS[record.status]?.tone ?? 'quiet') : 'quiet'

  return (
    <motion.div className="screen record" variants={m.stagger} initial="hidden" animate="show">
      <motion.div className="back-row" variants={m.riseIn}>
        <Link className="back-link" to="/inbox">
          ← Inbox
        </Link>
        {record ? <span className="back-id mono">{record.id}</span> : null}
      </motion.div>

      {error ? (
        <div className="empty record-error">
          <h1>Record not found</h1>
          <span className="empty-mark" aria-hidden="true" />
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
        <div className="record-skel">
          <span className="skel" style={{ width: '38%', height: 26 }} />
          <span className="skel" style={{ width: '22%', height: 12 }} />
          <span className="skel" style={{ width: '100%', height: 84 }} />
          <span className="skel" style={{ width: '100%', height: 220 }} />
        </div>
      ) : null}

      {record ? (
        <>
          <motion.header className="record-head" variants={m.riseIn}>
            <div className="record-ident">
              <span className="record-kicker mono">
                {requestLabel(record.seq)} · {originLabel(record.origin)}
                {record.channel ? ` · ${record.channel}` : ''}
              </span>
              <h1>{record.title}</h1>
              <p className="record-meta">
                <b>{record.agent.name}</b>
                <span className="mono record-tag">{record.agent.tag}</span>
                <span className="record-sep" aria-hidden="true" />
                asked by {record.requester ?? 'an agent'}
                <span className="record-sep" aria-hidden="true" />
                <span className="mono">{whenFull(record.created_at)}</span>
              </p>
              {record.text ? <p className="record-ask">“{record.text}”</p> : null}
            </div>
            <div className="record-amount">
              {record.lines.length ? (
                <span className="record-total">{money(record.total_cents, record.currency)}</span>
              ) : (
                <span className="record-nopriced">No cart priced</span>
              )}
              <StatusMark status={record.status} />
            </div>
          </motion.header>

          <motion.section className={`verdict is-${tone}`} variants={m.riseIn}>
            <div className="verdict-main">
              <span className="verdict-kicker mono">
                policy · {DECISION_WORD[record.decision ?? ''] ?? record.decision ?? '—'} ·{' '}
                {STATUS[record.status]?.label ?? record.status}
              </span>
              <p className="verdict-title">{verdictHeadline(record)}</p>
              <ReasonList
                record={record}
                showHints={
                  record.status === 'awaiting' ||
                  record.status === 'approved' ||
                  record.status === 'blocked'
                }
              />
              {record.status === 'captured' && record.reasons.length ? (
                <p className="verdict-clean">
                  The flags above are why this cart needed a signature. The signature is the
                  exception; no cap or threshold was edited.
                </p>
              ) : null}
            </div>

            {canDecide || canVoid ? (
              <div className="verdict-actions">
                {canDecide ? (
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={() => askAct('approve')}
                    disabled={busy !== null || pendingAction !== null}
                  >
                    {busy === 'approve'
                      ? 'Capturing…'
                      : `Approve · ${money(record.total_cents, record.currency)}`}
                  </button>
                ) : null}
                {record?.status === 'awaiting' ? (
                  <button
                    type="button"
                    className="btn btn-danger"
                    onClick={() => askAct('veto')}
                    disabled={busy !== null || pendingAction !== null}
                    title="Refuse this request — the trail records a veto"
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
                    title="Withdraw this request before capture — not a refusal"
                  >
                    {busy === 'void' ? 'Voiding…' : 'Void'}
                  </button>
                ) : null}
                {pendingAction ? (
                  <div className="verdict-confirm">
                    <p className="verdict-confirm-copy">
                      {pendingAction === 'approve'
                        ? `Capture ${money(record.total_cents, record.currency)} now? This moves money from the workspace wallet and cannot be undone.`
                        : pendingAction === 'veto'
                          ? 'Refuse this request? The veto is written to the trail and cannot be re-opened.'
                          : 'Withdraw this request before capture? The void stays in the record.'}
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
                            ? `Confirm · capture ${money(record.total_cents, record.currency)}`
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
                <p className="verdict-legend">
                  {canDecide
                    ? `Approve captures ${money(record.total_cents, record.currency)} from the workspace wallet. Veto refuses the request.`
                    : record.status === 'approved'
                      ? 'The capture is owed. Void cancels it before any money moves.'
                      : 'The consent expired, so approve is closed. Veto records a refusal; Void withdraws the stale request.'}
                </p>
              </div>
            ) : expiredNote(record) ? (
              <p className="verdict-role">{expiredNote(record)}</p>
            ) : record.status === 'blocked' ? (
              <p className="verdict-role">
                No signature was requested, and no PayPal object was created.
              </p>
            ) : me.user.role === 'viewer' && record.status === 'awaiting' ? (
              <p className="verdict-role">
                Your role is viewer — an approver or admin signs this cart.
              </p>
            ) : signedLine(record) ? (
              <p className="verdict-role">{signedLine(record)}</p>
            ) : null}
          </motion.section>

          {actionError ? (
            <p className="inline-error" role="alert">
              {actionError}{' '}
              {approveUrl ? (
                <a className="text-link" href={approveUrl} target="_blank" rel="noreferrer">
                  Connect the workspace wallet →
                </a>
              ) : null}
            </p>
          ) : null}
          {flash ? (
            <p className="record-flash" role="status">
              {flash}
            </p>
          ) : null}

          <div className="record-cols">
            <div className="record-col">
              <motion.section className="card" variants={m.riseIn}>
                <header className="card-head">
                  <h2>Cart</h2>
                  <span className="card-note mono">
                    {record.lines.length} {record.lines.length === 1 ? 'line' : 'lines'}
                  </span>
                </header>
                {record.lines.length ? (
                  <>
                    <div className="line-head" aria-hidden="true">
                      <span>Item</span>
                      <span>Vendor</span>
                      <span className="num">Qty</span>
                      <span className="num">Unit</span>
                      <span className="num">Amount</span>
                    </div>
                    <ul className="record-lines">
                      {record.lines.map((line, index) => (
                        <li className="record-line" key={`${line.sku ?? line.title}-${index}`}>
                          <span className="line-item">
                            <b>{line.title}</b>
                            <span className="line-meta mono">
                              {line.category ?? 'uncategorised'}
                              {line.brand ? ` · ${line.brand}` : ''}
                            </span>
                          </span>
                          <span className="line-vendor">{line.vendor ?? 'unknown'}</span>
                          <span className="line-num">{line.qty}</span>
                          <span className="line-num">{money(line.unit_cents)}</span>
                          <span className="line-num line-amount">{money(line.line_cents)}</span>
                        </li>
                      ))}
                    </ul>
                    <div className="record-total-row">
                      <span>Total</span>
                      <span>{money(record.total_cents, record.currency)}</span>
                    </div>
                  </>
                ) : (
                  <p className="record-none">
                    No cart was priced. The catalogue had no product for this request, so there
                    was nothing to buy and nothing to charge.
                  </p>
                )}
              </motion.section>

              <PaymentCard record={record} />
            </div>

            <div className="record-col">
              <ConsentCard record={record} />
              <TrailCard record={record} />
            </div>
          </div>
        </>
      ) : null}
    </motion.div>
  )
}
