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

/** What a finished run did, in the same words a record would use. A skip or a
    failure explains itself through the note the runner recorded. */
function runHeadline(result: RoutineRunResult): string {
  const amount = result.run.amount_cents ? ` ${money(result.run.amount_cents)}` : ''
  if (
    result.run.note &&
    (result.run.outcome === 'skipped' || result.run.outcome === 'failed')
  ) {
    return result.run.note
  }
  switch (result.run.outcome) {
    case 'captured':
      return `Ordered and captured${amount}.`
    case 'awaiting':
      return `Needs a signature${amount} — the request waits in the Inbox.`
    case 'approved':
      return `Approved${amount}; the capture waits for the wallet.`
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
 * The standing job: what the agent orders on its own, and the runs that prove it.
 * Every action here is the backend's, not a local mock: pause stops the schedule,
 * run now streams the real pipeline, and the run history is the recorded table.
 */
export function StandingJob({
  agent,
  routine,
  runs,
  role,
  defaultInstruction,
  onRoutineChange,
  onRefresh,
}: {
  agent: Agent
  routine: Routine | null
  runs: RoutineRun[]
  role: string
  defaultInstruction: string
  onRoutineChange: (routine: Routine | null) => void
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

  // A different job on the same agent: the old inline result no longer applies.
  useEffect(() => {
    setResult(null)
    setConfirming(false)
    setEditing(false)
    setError(null)
  }, [routine?.id])

  const save = async (input: RoutineInput) => {
    setSaving(true)
    setFormError(null)
    try {
      onRoutineChange(await api.saveRoutine(agent.id, input))
      setEditing(false)
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : 'The job was not saved.')
    } finally {
      setSaving(false)
    }
  }

  const setJobStatus = async (action: 'pause' | 'resume') => {
    if (!routine) return
    setStatusBusy(true)
    setError(null)
    try {
      onRoutineChange(await api.setRoutineStatus(agent.id, action))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The job state did not change.')
    } finally {
      setStatusBusy(false)
    }
  }

  const deleteJob = async () => {
    if (!routine) return
    setDeleteBusy(true)
    setDeleteError(null)
    try {
      await api.deleteRoutine(agent.id)
      setConfirmDelete(false)
      setResult(null)
      setEditing(false)
      onRoutineChange(null)
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
    if (!routine || running) return
    setConfirming(false)
    setRunning(true)
    setError(null)
    setResult(null)
    setLive({ steps: [], items: [] })
    const controller = new AbortController()
    runAbort.current = controller
    try {
      const done = await api.runRoutineStream(
        agent.id,
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

  const active = routine?.status === 'active'
  const agentPaused = agent.status !== 'active'
  const shownRuns = runs.slice(0, 5)

  return (
    <section className="card routine">
      <header className="card-head">
        <h2>Standing job</h2>
        <span className="card-note">a routine you approve once</span>
      </header>

      {routine ? (
        <>
          <div className="routine-top">
            <div className="routine-ident">
              <h3 className="routine-name">{routine.name}</h3>
              <p className="routine-line">
                <State tone={routineState(routine).tone}>{routineState(routine).label}</State>
                <span className="routine-line-sep" aria-hidden="true" />
                <span>Runs {routine.cadence_word}</span>
                <span className="routine-line-sep" aria-hidden="true" />
                <span className="mono routine-next">
                  {active ? `next run ${when(routine.next_run_at)}` : 'schedule stopped'}
                </span>
              </p>
            </div>
            {canWrite ? (
              <div className="agent-actions">
                {active ? (
                  confirming ? (
                    <>
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
                    </>
                  ) : agentPaused ? (
                    <span className="card-note">agent paused — resume it to run</span>
                  ) : (
                    <PressButton
                      variant="secondary"
                      disabled={running}
                      onClick={() => setConfirming(true)}
                    >
                      Run now
                    </PressButton>
                  )
                ) : (
                  <PressButton
                    variant="secondary"
                    loading={statusBusy}
                    onClick={() => void setJobStatus('resume')}
                  >
                    Resume job
                  </PressButton>
                )}
                {active ? (
                  <PressButton
                    variant="quiet"
                    loading={statusBusy}
                    onClick={() => void setJobStatus('pause')}
                  >
                    Pause job
                  </PressButton>
                ) : null}
                <PressButton variant="quiet" onClick={() => setEditing((v) => !v)}>
                  {editing ? 'Close editor' : 'Edit job'}
                </PressButton>
                <PressButton variant="quiet" onClick={() => setConfirmDelete(true)}>
                  Delete job
                </PressButton>
              </div>
            ) : (
              <span className="card-note">viewer — read only</span>
            )}
          </div>

          {confirming ? (
            <p className="routine-confirm">
              A run creates a real request now and can capture money. It uses the same rules,
              consent and PayPal path as any other request.
            </p>
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

          {!editing ? (
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
                {runs.length > 5 ? (
                  <span className="card-note">the five most recent</span>
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
                        <Link className="routine-run-link mono" to={`/inbox/${run.request_id}`}>
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
        </>
      ) : (
        <>
          <p className="card-lede">
            This agent has no standing job. It buys only when something asks it to; a job gives
            it a recurring instruction, a schedule and its own monthly ceiling.
          </p>
          {canWrite ? (
            editing ? (
              <RoutineForm
                routine={null}
                defaultInstruction={defaultInstruction}
                busy={saving}
                error={formError}
                onSave={(input) => void save(input)}
                onCancel={() => setEditing(false)}
              />
            ) : (
              <div className="routine-empty">
                <PressButton variant="secondary" onClick={() => setEditing(true)}>
                  Set a standing job
                </PressButton>
                <p className="fineprint">
                  You approve the job once; every due run still has to pass the same rules.
                </p>
              </div>
            )
          ) : (
            <p className="fineprint">
              Your role is viewer — an admin or approver sets this job.
            </p>
          )}
        </>
      )}
    </section>
  )
}
