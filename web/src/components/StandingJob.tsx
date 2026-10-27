import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import {
  api,
  ApiError,
  capMoney,
  money,
  type Agent,
  type Routine,
  type RoutineInput,
  type RoutineOutcome,
  type RoutineRun,
  type RoutineRunResult,
  type RoutineStreamFrame,
} from '../api'
import { Track } from './Bits'
import { PipelineLive, type Live, type LiveItem } from './live'
import { PressButton, Spinner } from './PressButton'
import { requestLabel, when } from './request'
import { State, routineState, runState } from './state'

const dollars = (cents: number | null | undefined): string =>
  cents === null || cents === undefined || cents === 0 ? '' : (cents / 100).toFixed(2)

const cents = (value: string): number | null => {
  const clean = value.replace(/[^0-9.]/g, '')
  if (!clean) return null
  const n = Number(clean)
  return Number.isFinite(n) ? Math.round(n * 100) : null
}

/** The five runs a job card shows; the record itself keeps every one. */
const RUNS_PER_JOB = 5

/** What a finished run did, in the same words a record would use. A skip or a
    failure explains itself through the note the runner recorded. */
function runHeadline(result: RoutineRunResult): string {
  const amount = result.run.amount_cents ? money(result.run.amount_cents) : ''
  if (
    result.run.note &&
    (result.run.outcome === 'skipped' || result.run.outcome === 'failed')
  ) {
    return result.run.note
  }
  switch (result.run.outcome) {
    case 'captured':
      return amount ? `Captured ${amount}.` : 'Captured.'
    case 'awaiting':
      return amount
        ? `Held ${amount} — the request waits in the Inbox.`
        : 'The request waits in the Inbox.'
    case 'approved':
      return amount
        ? `${amount} approved; the capture waits for the wallet.`
        : 'The capture waits for the wallet.'
    case 'blocked':
      return 'Refused by the rules. Nothing was ordered.'
    case 'skipped':
      return 'Nothing was ordered.'
    default:
      return 'The run did not finish.'
  }
}

function runsWithNotes(outcome: RoutineOutcome): boolean {
  return outcome === 'skipped' || outcome === 'failed' || outcome === 'awaiting'
}

/** A chevron that says whether the card is open. */
function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      className={`job-chevron${open ? ' is-open' : ''}`}
      width="16"
      height="16"
      viewBox="0 0 16 16"
      aria-hidden="true"
    >
      <path
        d="M4.5 6.25 8 9.75l3.5-3.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

type MenuItem = {
  label: string
  onSelect: () => void
  disabled?: boolean
  danger?: boolean
  title?: string
}

/**
 * The "..." menu on a job card. It holds the quiet actions (edit, delete, hide
 * details); run and pause stay on the card because they are the frequent ones.
 * Closes on an outside click and on Escape, like a native menu.
 */
function JobMenu({ label, items }: { label: string; items: MenuItem[] }) {
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onPointer = (event: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div className="job-menu" ref={wrap}>
      <button
        type="button"
        className="job-menu-trigger"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="3.25" cy="8" r="1.35" fill="currentColor" />
          <circle cx="8" cy="8" r="1.35" fill="currentColor" />
          <circle cx="12.75" cy="8" r="1.35" fill="currentColor" />
        </svg>
      </button>
      {open ? (
        <div className="job-menu-list" role="menu">
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              className={`job-menu-item${item.danger ? ' is-danger' : ''}`}
              disabled={item.disabled}
              title={item.title}
              onClick={() => {
                setOpen(false)
                item.onSelect()
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}

/**
 * The job form. One instruction, one cadence, one envelope, one reorder window:
 * the same four facts the backend stores, nothing invented.
 */
function RoutineForm({
  routine,
  defaultInstruction,
  busy,
  error,
  onSave,
  onCancel,
}: {
  routine: Routine | null
  defaultInstruction: string
  busy: boolean
  error: string | null
  onSave: (input: RoutineInput) => void
  onCancel: () => void
}) {
  const [instruction, setInstruction] = useState(routine?.instruction ?? defaultInstruction)
  const [cadence, setCadence] = useState<RoutineInput['cadence']>(routine?.cadence ?? 'weekly')
  const [envelope, setEnvelope] = useState(dollars(routine?.envelope_cents))
  const [dedupe, setDedupe] = useState(String(routine?.dedupe_days ?? 0))
  const [localError, setLocalError] = useState<string | null>(null)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    if (instruction.trim().length < 3) {
      setLocalError('Say what the job should order, in plain words.')
      return
    }
    setLocalError(null)
    const window = Math.max(0, Math.min(365, Math.trunc(Number(dedupe) || 0)))
    onSave({
      instruction: instruction.trim(),
      cadence,
      envelope_cents: Math.max(0, cents(envelope) ?? 0),
      dedupe_days: window,
    })
  }

  return (
    <form className="routine-form" onSubmit={submit} noValidate>
      <label className="field">
        <span className="field-label">
          What the job orders <span className="field-hint mono">like a request</span>
        </span>
        <textarea
          className="input textarea"
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          rows={2}
          placeholder="2 packs of toilet paper and dish soap"
          disabled={busy}
        />
      </label>
      <div className="form-row routine-form-row">
        <label className="field">
          <span className="field-label">How often</span>
          <select
            className="select"
            value={cadence}
            onChange={(e) => setCadence(e.target.value as RoutineInput['cadence'])}
            disabled={busy}
          >
            <option value="daily">every day</option>
            <option value="weekly">every week</option>
            <option value="monthly">every month</option>
          </select>
        </label>
        <label className="field">
          <span className="field-label">
            Envelope each month <span className="field-hint mono">blank = none</span>
          </span>
          <input
            className="input mono"
            inputMode="decimal"
            value={envelope}
            onChange={(e) => setEnvelope(e.target.value)}
            placeholder="250.00"
            disabled={busy}
          />
        </label>
        <label className="field">
          <span className="field-label">
            Won&rsquo;t reorder within <span className="field-hint mono">days</span>
          </span>
          <input
            className="input mono"
            inputMode="numeric"
            value={dedupe}
            onChange={(e) => setDedupe(e.target.value)}
            placeholder="0"
            disabled={busy}
          />
        </label>
      </div>
      {localError || error ? (
        <p className="inline-error" role="alert">
          {localError ?? error}
        </p>
      ) : null}
      <p className="fineprint">
        A run is a real request: the same rules, consent and capture apply. The envelope is the
        job&rsquo;s own monthly ceiling; a cart that would cross it waits for a signature.
      </p>
      <div className="form-actions">
        <PressButton type="submit" loading={busy}>
          {busy ? (
            <>
              <Spinner label="saving" /> Saving…
            </>
          ) : (
            'Save job'
          )}
        </PressButton>
        <PressButton variant="quiet" onClick={onCancel}>
          Cancel
        </PressButton>
      </div>
    </form>
  )
}

/**
 * One scheduled job as a card. Run now and Pause job stay on the card (they are
 * the frequent actions); the "..." menu keeps edit, delete and "hide details",
 * which collapses the card down to its header to save the page's height.
 */
function JobCard({
  agent,
  routine,
  runs,
  role,
  defaultInstruction,
  expanded,
  onExpandedChange,
  onUpsert,
  onRemove,
  onRefresh,
}: {
  agent: Agent
  routine: Routine
  runs: RoutineRun[]
  role: string
  defaultInstruction: string
  expanded: boolean
  onExpandedChange: (open: boolean) => void
  onUpsert: (routine: Routine) => void
  onRemove: (routineId: string) => void
  onRefresh: () => Promise<void>
}) {
  const canWrite = role === 'admin' || role === 'approver'
  const [editing, setEditing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [statusBusy, setStatusBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [live, setLive] = useState<Live | null>(null)
  const [result, setResult] = useState<RoutineRunResult | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const runAbort = useRef<AbortController | null>(null)

  useEffect(() => () => runAbort.current?.abort(), [])

  const save = async (input: RoutineInput) => {
    setSaving(true)
    setFormError(null)
    try {
      onUpsert(await api.updateRoutine(routine.id, input))
      setEditing(false)
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : 'The job was not saved.')
    } finally {
      setSaving(false)
    }
  }

  const setJobStatus = async (action: 'pause' | 'resume') => {
    setStatusBusy(true)
    setError(null)
    try {
      onUpsert(await api.setRoutineStatus(routine.id, action))
    } catch (err) {
      onExpandedChange(true)
      setError(err instanceof ApiError ? err.message : 'The job state did not change.')
    } finally {
      setStatusBusy(false)
    }
  }

  const deleteJob = async () => {
    setDeleteBusy(true)
    setDeleteError(null)
    try {
      await api.deleteRoutine(routine.id)
      setConfirmDelete(false)
      setResult(null)
      setEditing(false)
      onRemove(routine.id)
      try {
        await onRefresh()
      } catch {
        // the job is gone; the page can be re-read on the next visit
      }
    } catch (err) {
      setDeleteError(
        err instanceof ApiError ? err.message : 'The job was not deleted. Nothing changed.',
      )
    } finally {
      setDeleteBusy(false)
    }
  }

  const runNow = async () => {
    if (running) return
    onExpandedChange(true)
    setConfirming(false)
    setRunning(true)
    setError(null)
    setResult(null)
    setLive({ steps: [], items: [] })
    const controller = new AbortController()
    runAbort.current = controller
    try {
      const done = await api.runRoutineStream(
        routine.id,
        (frame: RoutineStreamFrame) => {
          if (frame.type === 'event') {
            setLive((prev) =>
              prev ? { ...prev, steps: [...prev.steps, frame.event] } : prev,
            )
          } else if (frame.type === 'item') {
            const item: LiveItem = {
              index: frame.index,
              name: frame.name,
              ok: frame.ok,
              title: frame.title,
            }
            setLive((prev) => {
              if (!prev) return prev
              const items = [...prev.items, item].sort((a, b) => a.index - b.index)
              return { ...prev, items }
            })
          }
        },
        controller.signal,
      )
      setResult(done)
      try {
        await onRefresh()
      } catch {
        // the run is recorded; the page can be re-read on the next visit
      }
    } catch (err) {
      if (!controller.signal.aborted) {
        setError(err instanceof ApiError ? err.message : 'The job did not finish.')
      }
    } finally {
      if (runAbort.current === controller) {
        runAbort.current = null
        setRunning(false)
        setLive(null)
      }
    }
  }

  const active = routine.status === 'active'
  const agentPaused = agent.status !== 'active'
  const jobRuns = runs.filter((run) => run.routine_id === routine.id)
  const shownRuns = jobRuns.slice(0, RUNS_PER_JOB)
  const usedCents = routine.spent_cents + routine.committed_cents

  const menuItems: MenuItem[] = []
  if (canWrite) {
    menuItems.push({
      label: 'Edit job',
      disabled: running,
      onSelect: () => {
        onExpandedChange(true)
        setFormError(null)
        setEditing(true)
      },
    })
  }
  menuItems.push({
    label: expanded ? 'Hide details' : 'Show details',
    onSelect: () => onExpandedChange(!expanded),
  })
  if (canWrite) {
    menuItems.push({
      label: 'Delete job',
      danger: true,
      disabled: running || deleteBusy,
      onSelect: () => {
        onExpandedChange(true)
        setConfirmDelete(true)
      },
    })
  }

  return (
    <article className={`job-card${expanded ? ' is-open' : ''}`}>
      <header className="job-card-head">
        <div className="job-card-ident">
          <div className="job-card-title">
            <State tone={routineState(routine).tone}>{routineState(routine).label}</State>
            <h3 className="job-card-name">{routine.name}</h3>
          </div>
          <p className="job-card-meta">
            <span>Runs {routine.cadence_word}</span>
            <span className="mono">
              {active ? `next run ${when(routine.next_run_at)}` : 'schedule stopped'}
            </span>
            {agentPaused ? <span>agent paused</span> : null}
            {routine.envelope_cents > 0 ? (
              <span className="mono job-card-envelope">
                {capMoney(usedCents)} of {capMoney(routine.envelope_cents)} this month
              </span>
            ) : null}
          </p>
          {!expanded ? (
            <p className="job-card-preview">{routine.instruction}</p>
          ) : null}
        </div>

        <div className="job-card-actions">
          {canWrite ? (
            active ? (
              <PressButton
                variant="secondary"
                disabled={running || agentPaused || confirming}
                onClick={() => {
                  onExpandedChange(true)
                  setConfirming(true)
                  setError(null)
                }}
              >
                Run now
              </PressButton>
            ) : (
              <PressButton
                variant="secondary"
                loading={statusBusy}
                disabled={running}
                onClick={() => void setJobStatus('resume')}
              >
                Resume job
              </PressButton>
            )
          ) : null}
          {canWrite && active ? (
            <PressButton
              variant="quiet"
              loading={statusBusy}
              disabled={running}
              onClick={() => void setJobStatus('pause')}
            >
              Pause job
            </PressButton>
          ) : null}
          <button
            type="button"
            className="job-card-toggle"
            aria-label={expanded ? 'Hide details' : 'Show details'}
            aria-expanded={expanded}
            onClick={() => onExpandedChange(!expanded)}
          >
            <Chevron open={expanded} />
          </button>
          {canWrite ? <JobMenu label={`More actions for ${routine.name}`} items={menuItems} /> : null}
        </div>
      </header>

      {expanded ? (
        <div className="job-card-body">
          {confirming ? (
            <div className="routine-confirm">
              <p>
                A run creates a real request now and can capture money. It uses the same rules,
                consent and PayPal path as any other request.
              </p>
              <div className="form-actions">
                <PressButton
                  variant="secondary"
                  loading={running}
                  onClick={() => void runNow()}
                >
                  Confirm run
                </PressButton>
                <PressButton variant="quiet" onClick={() => setConfirming(false)}>
                  Cancel
                </PressButton>
              </div>
            </div>
          ) : null}

          {error ? (
            <p className="inline-error" role="alert">
              {error}
            </p>
          ) : null}

          {running && live ? (
            <PipelineLive
              steps={live.steps}
              items={live.items}
              title="Running the job"
              note="real request · may capture"
            />
          ) : null}

          {result && !running ? (
            <div className="routine-result">
              <div className="routine-result-main">
                <State tone={runState(result.run.outcome).tone}>
                  {runState(result.run.outcome).label}
                </State>
                <p className="routine-result-head">{runHeadline(result)}</p>
              </div>
              {result.request ? (
                <Link className="text-link mono" to={`/inbox/${result.request.id}`}>
                  Open {requestLabel(result.request.seq)}
                </Link>
              ) : null}
            </div>
          ) : null}

          {editing ? (
            <div className="routine-editor">
              <RoutineForm
                routine={routine}
                defaultInstruction={defaultInstruction}
                busy={saving}
                error={formError}
                onSave={(input) => void save(input)}
                onCancel={() => setEditing(false)}
              />
            </div>
          ) : null}

          {confirmDelete ? (
            <div className="danger-confirm">
              <p className="danger-copy">
                Delete this standing job and its run history? The requests it already made stay
                in the record. The agent keeps its rules and can still buy when something asks
                it to.
              </p>
              {deleteError ? (
                <p className="inline-error" role="alert">
                  {deleteError}
                </p>
              ) : null}
              <div className="form-actions">
                <button
                  type="button"
                  className="btn btn-danger"
                  onClick={() => void deleteJob()}
                  disabled={deleteBusy || running}
                >
                  {deleteBusy ? 'Deleting…' : 'Confirm delete'}
                </button>
                <button
                  type="button"
                  className="btn btn-quiet"
                  onClick={() => setConfirmDelete(false)}
                  disabled={deleteBusy || running}
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : null}

          {!confirmDelete && deleteError ? (
            <p className="inline-error" role="alert">
              {deleteError}
            </p>
          ) : null}

          {!editing && !confirmDelete ? (
            <div className="routine-body">
              <div className="routine-spec">
                <p className="spec-label">Standing instruction</p>
                <blockquote className="routine-quote">{routine.instruction}</blockquote>
                {routine.envelope_cents > 0 ? (
                  <div className="routine-envelope">
                    <div className="routine-envelope-head">
                      <span className="spec-label">Envelope this month</span>
                      <span className="mono routine-envelope-figure">
                        {capMoney(routine.envelope_cents)}
                      </span>
                    </div>
                    <Track
                      spentCents={routine.spent_cents}
                      committedCents={routine.committed_cents}
                      capCents={routine.envelope_cents}
                    />
                    {routine.remaining_cents === 0 ? (
                      <p className="fineprint">
                        The envelope is used up; due runs skip until the month turns.
                      </p>
                    ) : null}
                  </div>
                ) : (
                  <p className="routine-note">
                    No envelope on this job — each run is judged by the agent&rsquo;s own caps.
                  </p>
                )}
                <p className="routine-note">
                  {routine.dedupe_days > 0
                    ? `Won't reorder within ${routine.dedupe_days} days.`
                    : 'No reorder window — every due run becomes a request.'}{' '}
                  The same rules, consent and capture apply.
                </p>
                {!active ? (
                  <p className="routine-note">
                    Paused — the schedule will not run until you resume the job.
                  </p>
                ) : null}
                {agentPaused ? (
                  <p className="routine-note">
                    The agent is paused, so a due run records a skip and orders nothing.
                  </p>
                ) : null}
              </div>

              <div className="routine-runs">
                <header className="routine-runs-head">
                  <h3>Recent runs</h3>
                  {jobRuns.length > RUNS_PER_JOB ? (
                    <span className="card-note">the {RUNS_PER_JOB} most recent</span>
                  ) : null}
                </header>
                {shownRuns.length ? (
                  <ol className="routine-run-list">
                    {shownRuns.map((run) => (
                      <li className="routine-run" key={run.id}>
                        <span className="routine-run-when mono">
                          {when(run.started_at ?? run.due_at)}
                        </span>
                        <span className="routine-run-state">
                          <State tone={runState(run.outcome).tone}>
                            {runState(run.outcome).label}
                          </State>
                        </span>
                        <span className="routine-run-money mono">
                          {run.amount_cents
                            && run.outcome !== 'skipped'
                            && run.outcome !== 'failed'
                            ? money(run.amount_cents)
                            : ''}
                        </span>
                        {run.request_id ? (
                          <Link
                            className="routine-run-link mono"
                            to={`/inbox/${run.request_id}`}
                          >
                            {run.request_id}
                          </Link>
                        ) : (
                          <span className="routine-run-link mono is-void">no request</span>
                        )}
                        {run.note && runsWithNotes(run.outcome) ? (
                          <p className="routine-run-note">{run.note}</p>
                        ) : null}
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="fineprint">
                    No runs yet. The first one is due {when(routine.next_run_at)}.
                  </p>
                )}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </article>
  )
}

/**
 * The standing jobs: one card per scheduled job in the agent. Each card can be
 * collapsed from its "..." menu (or the chevron) so several jobs still fit the
 * page; opening the agent shows the cards, not a wall of instructions.
 */
export function StandingJobs({
  agent,
  routines,
  runs,
  role,
  defaultInstruction,
  onRoutinesChange,
  onRefresh,
}: {
  agent: Agent
  routines: Routine[]
  runs: RoutineRun[]
  role: string
  defaultInstruction: string
  onRoutinesChange: (updater: (prev: Routine[]) => Routine[]) => void
  onRefresh: () => Promise<void>
}) {
  const canWrite = role === 'admin' || role === 'approver'
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [adding, setAdding] = useState(false)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  const toggle = (routineId: string, open: boolean) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (open) next.add(routineId)
      else next.delete(routineId)
      return next
    })
  }

  const upsert = (routine: Routine) => {
    onRoutinesChange((prev) => {
      const exists = prev.some((item) => item.id === routine.id)
      return exists
        ? prev.map((item) => (item.id === routine.id ? routine : item))
        : [...prev, routine]
    })
  }

  const remove = (routineId: string) => {
    onRoutinesChange((prev) => prev.filter((item) => item.id !== routineId))
    setExpanded((prev) => {
      if (!prev.has(routineId)) return prev
      const next = new Set(prev)
      next.delete(routineId)
      return next
    })
  }

  const create = async (input: RoutineInput) => {
    setCreating(true)
    setCreateError(null)
    try {
      const routine = await api.createRoutine(agent.id, input)
      onRoutinesChange((prev) => [...prev, routine])
      setAdding(false)
      setExpanded((prev) => {
        const next = new Set(prev)
        next.add(routine.id)
        return next
      })
      try {
        await onRefresh()
      } catch {
        // the job exists; the card above already shows it
      }
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.message : 'The job was not created.')
    } finally {
      setCreating(false)
    }
  }

  const jobCount = routines.length
  const countNote =
    jobCount === 0
      ? 'none yet'
      : jobCount === 1
        ? 'one scheduled job'
        : `${jobCount} scheduled jobs`

  return (
    <section className="card routine standing-jobs">
      <header className="card-head">
        <h2>Standing jobs</h2>
        <div className="job-head-tools">
          <span className="card-note">{countNote}</span>
          {canWrite ? (
            <button
              type="button"
              className="btn btn-quiet job-add"
              onClick={() => {
                setAdding((value) => !value)
                setCreateError(null)
              }}
            >
              {adding ? 'Close' : 'New job'}
            </button>
          ) : null}
        </div>
      </header>

      {routines.length === 0 && !adding ? (
        <>
          <p className="card-lede">
            This agent has no standing jobs. Each job is a recurring instruction, a schedule and
            its own monthly ceiling; a job still has to pass the same rules on every run.
          </p>
          {canWrite ? (
            <div className="routine-empty">
              <PressButton variant="secondary" onClick={() => setAdding(true)}>
                Set a standing job
              </PressButton>
              <p className="fineprint">
                You approve the job once; every due run still has to pass the same rules.
              </p>
            </div>
          ) : (
            <p className="fineprint">
              Your role is viewer — an admin or approver sets this job.
            </p>
          )}
        </>
      ) : null}

      {adding ? (
        <div className="job-card is-new">
          <div className="job-card-body">
            <RoutineForm
              routine={null}
              defaultInstruction={defaultInstruction}
              busy={creating}
              error={createError}
              onSave={(input) => void create(input)}
              onCancel={() => {
                setAdding(false)
                setCreateError(null)
              }}
            />
          </div>
        </div>
      ) : null}

      {jobCount ? (
        <div className="job-list">
          {routines.map((routine) => (
            <JobCard
              key={routine.id}
              agent={agent}
              routine={routine}
              runs={runs}
              role={role}
              defaultInstruction={defaultInstruction}
              expanded={expanded.has(routine.id)}
              onExpandedChange={(open) => toggle(routine.id, open)}
              onUpsert={upsert}
              onRemove={remove}
              onRefresh={onRefresh}
            />
          ))}
        </div>
      ) : null}

      {jobCount ? (
        <p className="fineprint job-foot">
          Run now creates a real request and can capture money. Pause stops the schedule; the
          job keeps its rules and its monthly ceiling.
        </p>
      ) : null}
    </section>
  )
}
