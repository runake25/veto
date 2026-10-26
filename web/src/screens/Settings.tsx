import { useId, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { motion, useReducedMotion } from 'motion/react'
import { api, ApiError, type Me } from '../api'
import { CopyValue } from '../components/Bits'
import { motionSet } from '../components/motion'
import { State } from '../components/state'

/** What the engine says when the current password is wrong, in a person's words. */
function passwordError(err: unknown): string {
  if (!(err instanceof ApiError)) return 'The password was not changed.'
  if (err.status === 401 || err.code === 'invalid_password') {
    return 'That current password does not match this account.'
  }
  return err.message
}

function AccountCard({ me, onMeChange, onSignOut }: { me: Me; onMeChange: (me: Me) => void; onSignOut: () => void }) {
  const [name, setName] = useState(me.user.name)
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fieldId = useId()
  const dirty = name.trim() !== me.user.name

  const save = async () => {
    if (!dirty || busy) return
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      await api.updateMe(name.trim())
      onMeChange(await api.me())
      setSaved(true)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The name was not saved.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="settings-card">
      <header className="settings-card-head">
        <h2>Account</h2>
        <span className="card-note">{me.user.role}</span>
      </header>

      <div className="settings-grid">
        <div className="setting-field is-wide">
          <label className="setting-label" htmlFor={fieldId}>
            Display name
          </label>
          <input
            id={fieldId}
            className="input"
            value={name}
            maxLength={80}
            autoComplete="name"
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? `${fieldId}-error` : undefined}
            onChange={(event) => {
              setName(event.target.value)
              setSaved(false)
              setError(null)
            }}
          />
          {error ? (
            <p className="field-error" id={`${fieldId}-error`}>
              {error}
            </p>
          ) : (
            <p className="setting-hint">Shown on signed consent and the request trail.</p>
          )}
        </div>

        <div className="setting-field">
          <span className="setting-label">Email</span>
          <p className="setting-value">{me.user.email}</p>
        </div>
      </div>

      <div className="setting-actions">
        <button
          type="button"
          className="btn btn-primary btn-small"
          onClick={() => void save()}
          disabled={!dirty || busy}
        >
          {busy ? 'Saving…' : 'Save name'}
        </button>
        {saved ? <span className="settings-flash">Saved.</span> : null}
        <button
          type="button"
          className="btn btn-quiet btn-small"
          style={{ marginLeft: 'auto' }}
          onClick={onSignOut}
        >
          Sign out
        </button>
      </div>
    </section>
  )
}

function WorkspaceCard({ me, onMeChange }: { me: Me; onMeChange: (me: Me) => void }) {
  const admin = me.user.role === 'admin'
  const [name, setName] = useState(me.workspace.name)
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fieldId = useId()
  const dirty = name.trim() !== me.workspace.name

  const save = async () => {
    if (!dirty || busy) return
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      await api.updateWorkspace(name.trim())
      onMeChange(await api.me())
      setSaved(true)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The workspace was not renamed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="settings-card">
      <header className="settings-card-head">
        <h2>Workspace</h2>
        <span className="card-note">One workspace, one approver</span>
      </header>

      <div className="settings-grid">
        <div className="setting-field is-wide">
          <label className="setting-label" htmlFor={fieldId}>
            Workspace name
          </label>
          {admin ? (
            <input
              id={fieldId}
              className="input"
              value={name}
              maxLength={80}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? `${fieldId}-error` : undefined}
              onChange={(event) => {
                setName(event.target.value)
                setSaved(false)
                setError(null)
              }}
            />
          ) : (
            <p className="setting-value">{me.workspace.name}</p>
          )}
          {error ? (
            <p className="field-error" id={`${fieldId}-error`}>
              {error}
            </p>
          ) : admin ? null : (
            <p className="setting-hint">Only an admin renames the workspace.</p>
          )}
        </div>

        <div className="setting-field">
          <span className="setting-label">Workspace ID</span>
          <CopyValue value={me.workspace.id} label="Copy the workspace id" />
        </div>

        <div className="setting-field">
          <span className="setting-label">Currency</span>
          <p className="setting-value mono">{me.workspace.currency}</p>
        </div>
      </div>

      {admin ? (
        <div className="setting-actions">
          <button
            type="button"
            className="btn btn-primary btn-small"
            onClick={() => void save()}
            disabled={!dirty || busy}
          >
            {busy ? 'Saving…' : 'Save workspace'}
          </button>
          {saved ? <span className="settings-flash">Saved.</span> : null}
        </div>
      ) : null}
    </section>
  )
}

function SecurityCard() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const first = useRef<HTMLInputElement | null>(null)
  const currentId = useId()
  const nextId = useId()
  const confirmId = useId()

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError(null)
    setSaved(false)
    if (!current) {
      setError('Enter the password you signed in with.')
      first.current?.focus()
      return
    }
    if (next.length < 10) {
      setError('Use at least 10 characters for the new password.')
      return
    }
    if (next !== confirm) {
      setError('The two new passwords do not match.')
      return
    }
    setBusy(true)
    try {
      await api.changePassword(current, next)
      setCurrent('')
      setNext('')
      setConfirm('')
      setSaved(true)
    } catch (err) {
      setError(passwordError(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="settings-card">
      <header className="settings-card-head">
        <h2>Security</h2>
        <span className="card-note">This browser stays signed in</span>
      </header>
      <form className="settings-grid" onSubmit={(event) => void submit(event)}>
        <div className="setting-field is-wide">
          <label className="setting-label" htmlFor={currentId}>
            Current password
          </label>
          <input
            ref={first}
            id={currentId}
            className="input"
            type="password"
            value={current}
            autoComplete="current-password"
            onChange={(event) => setCurrent(event.target.value)}
          />
        </div>
        <div className="setting-field">
          <label className="setting-label" htmlFor={nextId}>
            New password
          </label>
          <input
            id={nextId}
            className="input"
            type="password"
            value={next}
            autoComplete="new-password"
            onChange={(event) => setNext(event.target.value)}
          />
          <p className="setting-hint">At least 10 characters.</p>
        </div>
        <div className="setting-field">
          <label className="setting-label" htmlFor={confirmId}>
            Confirm new password
          </label>
          <input
            id={confirmId}
            className="input"
            type="password"
            value={confirm}
            autoComplete="new-password"
            onChange={(event) => setConfirm(event.target.value)}
          />
        </div>
        {error ? (
          <p className="field-error is-wide" style={{ gridColumn: '1 / -1' }}>
            {error}
          </p>
        ) : null}
        <div className="setting-actions is-wide" style={{ gridColumn: '1 / -1' }}>
          <button type="submit" className="btn btn-primary btn-small" disabled={busy}>
            {busy ? 'Changing…' : 'Change password'}
          </button>
          {saved ? <span className="settings-flash">Password changed.</span> : null}
        </div>
      </form>
    </section>
  )
}

function EnvironmentCard({ me }: { me: Me }) {
  const walletOn = me.wallet.status === 'connected'
  return (
    <section className="settings-card">
      <header className="settings-card-head">
        <h2>Environment</h2>
        <span className="card-note">Read-only</span>
      </header>
      <dl className="settings-dl">
        <div>
          <dt>PayPal</dt>
          <dd>
            <State tone={walletOn ? 'pos' : 'wait'}>
              {walletOn ? 'Connected' : 'Not connected'}
            </State>
          </dd>
        </div>
        <div>
          <dt>Rails</dt>
          <dd>Sandbox only in this build</dd>
        </div>
        <div>
          <dt>Agents</dt>
          <dd>{me.agent_count}</dd>
        </div>
        <div>
          <dt>Runner</dt>
          <dd className="mono">{window.location.origin}</dd>
        </div>
      </dl>
      <div className="settings-link-row">
        <Link className="text-link mono" to="/integrations">
          wallet &amp; integrations →
        </Link>
        <Link className="text-link mono" to="/inbox">
          the record →
        </Link>
      </div>
    </section>
  )
}

/**
 * Settings: the four things a person can actually change or read about their own
 * seat — account, workspace, password and the environment this runner is in. Every
 * control writes to the engine; nothing here is a preference that only lives in the
 * browser.
 */
export function Settings({
  me,
  onMeChange,
  onSignOut,
}: {
  me: Me
  onMeChange: (me: Me) => void
  onSignOut: () => void
}) {
  const reduce = useReducedMotion()
  const m = motionSet(reduce)

  return (
    <motion.div
      className="screen settings"
      variants={m.stagger}
      initial="hidden"
      animate="show"
    >
      <motion.header className="screen-head" variants={m.riseIn}>
        <div>
          <h1>Settings</h1>
          <p>Your seat, this workspace and the rails under it.</p>
        </div>
      </motion.header>

      <motion.div className="settings-stack" variants={m.riseIn}>
        <AccountCard me={me} onMeChange={onMeChange} onSignOut={onSignOut} />
        <WorkspaceCard me={me} onMeChange={onMeChange} />
        <SecurityCard />
        <EnvironmentCard me={me} />
      </motion.div>
    </motion.div>
  )
}
