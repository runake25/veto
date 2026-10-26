import { useState, type FormEvent, type ReactNode } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { api, ApiError, type Me, type SessionData } from '../api'
import { Wordmark } from '../components/Logo'
import { Check, PressButton, Spinner } from '../components/PressButton'
import { motionSet } from '../components/motion'

type Mode = 'signin' | 'signup'
type Status = 'idle' | 'busy' | 'success'

function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: ReactNode
}) {
  return (
    <label className="field">
      <span className="field-label">
        {label}
        {hint ? <span className="field-hint mono">{hint}</span> : null}
      </span>
      {children}
    </label>
  )
}

const DEMO = {
  approver: 'caroline@brackenfield.demo',
  admin: 'ops@brackenfield.demo',
  password: 'veto-demo-2026',
}

const FACE: Record<Mode, { title: string; line: string; cta: string; switchTo: string }> = {
  signin: {
    title: 'Sign in',
    line: 'Approve what your agents propose.',
    cta: 'Sign in',
    switchTo: 'Create a workspace',
  },
  signup: {
    title: 'Create a workspace',
    line: 'Set up a workspace for your agents.',
    cta: 'Create workspace',
    switchTo: 'Sign in',
  },
}

/**
 * The door. Two ways in: a real workspace, or the seeded demo. The page says one
 * thing above the form and keeps the rest (demo logins, sandbox facts) quiet.
 */
export function Auth({ onSignedIn }: { onSignedIn: (me: Me) => void }) {
  const [mode, setMode] = useState<Mode>('signin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [workspaceName, setWorkspaceName] = useState('')
  const [status, setStatus] = useState<Status>('idle')
  const [demoBusy, setDemoBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const reduce = useReducedMotion()
  const m = motionSet(reduce)
  const busy = status !== 'idle' || demoBusy
  const face = FACE[mode]

  const land = async (session: SessionData) => {
    setStatus('success')
    const me = await api.me().catch<Me>(() => ({
      ...session,
      agent_count: 0,
      wallet: { status: 'disconnected' },
    }))
    window.setTimeout(() => onSignedIn(me), reduce ? 0 : 420)
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    setError(null)
    setStatus('busy')
    try {
      const session =
        mode === 'signin'
          ? await api.login(email.trim(), password)
          : await api.signup({
              workspace_name: workspaceName.trim(),
              name: name.trim(),
              email: email.trim(),
              password,
            })
      await land(session)
    } catch (err) {
      setStatus('idle')
      setError(
        err instanceof ApiError ? err.message : 'Something went wrong. Try again in a moment.',
      )
    }
  }

  const demo = async () => {
    if (busy) return
    setError(null)
    setDemoBusy(true)
    try {
      const session = await api.demoSession()
      setDemoBusy(false)
      await land(session)
    } catch (err) {
      setDemoBusy(false)
      setError(err instanceof ApiError ? err.message : 'The demo workspace is unavailable.')
    }
  }

  const switchMode = () => {
    if (busy) return
    setError(null)
    setMode((current) => (current === 'signin' ? 'signup' : 'signin'))
  }

  return (
    <main className="ground auth-ground">
      <motion.section
        className="auth-sheet"
        variants={m.sheetIn}
        initial="hidden"
        animate="show"
        aria-labelledby="auth-title"
      >
        <header className="auth-brand">
          <Wordmark />
          <span className="auth-env mono">PayPal sandbox · USD</span>
        </header>

        <div className="auth-body">
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={mode}
              className="auth-intro"
              initial={{ opacity: 0, y: reduce ? 0 : 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, transition: { duration: 0.1 } }}
              transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
            >
              <h1 id="auth-title" className="display auth-title">
                {face.title}
              </h1>
              <p className="auth-line">{face.line}</p>
            </motion.div>
          </AnimatePresence>

          <form className="auth-form" onSubmit={submit} noValidate>
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={mode}
                className="auth-fields"
                initial={{ opacity: 0, y: reduce ? 0 : 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, transition: { duration: 0.1 } }}
                transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
              >
                {mode === 'signup' && (
                  <>
                    <Field label="Workspace" hint="company or team">
                      <input
                        className="input"
                        value={workspaceName}
                        onChange={(e) => setWorkspaceName(e.target.value)}
                        placeholder="Bracken & Field"
                        autoComplete="organization"
                        required
                        disabled={busy}
                      />
                    </Field>
                    <Field label="Your name">
                      <input
                        className="input"
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        placeholder="Caroline Hayes"
                        autoComplete="name"
                        required
                        disabled={busy}
                      />
                    </Field>
                  </>
                )}
                <Field label="Email">
                  <input
                    className="input"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@company.com"
                    autoComplete="email"
                    required
                    disabled={busy}
                  />
                </Field>
                <Field
                  label="Password"
                  hint={mode === 'signup' ? 'at least 8 characters' : undefined}
                >
                  <input
                    className="input"
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="••••••••"
                    autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
                    required
                    minLength={mode === 'signup' ? 8 : undefined}
                    disabled={busy}
                  />
                </Field>
              </motion.div>
            </AnimatePresence>

            <AnimatePresence initial={false}>
              {error ? (
                <motion.p
                  className="form-error"
                  role="alert"
                  initial={{ opacity: 0, y: reduce ? 0 : -4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, transition: { duration: 0.1 } }}
                >
                  {error}
                </motion.p>
              ) : null}
            </AnimatePresence>

            <PressButton type="submit" loading={status === 'busy'} className="auth-submit">
              <AnimatePresence mode="wait" initial={false}>
                <motion.span
                  key={status}
                  className="btn-face"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0, transition: { duration: 0.08 } }}
                  transition={{ duration: 0.14 }}
                >
                  {status === 'idle' && face.cta}
                  {status === 'busy' && (
                    <>
                      <Spinner label="signing in" />
                      {mode === 'signin' ? 'Signing in…' : 'Creating workspace…'}
                    </>
                  )}
                  {status === 'success' && (
                    <>
                      <Check />
                      {mode === 'signin' ? 'Signed in' : 'Workspace created'}
                    </>
                  )}
                </motion.span>
              </AnimatePresence>
            </PressButton>
          </form>

          <p className="auth-switch">
            {mode === 'signin' ? 'New to Veto?' : 'Already have a workspace?'}{' '}
            <button type="button" className="auth-switch-btn" onClick={switchMode} disabled={busy}>
              {face.switchTo}
            </button>
          </p>

          <div className="auth-demo">
            <div className="auth-rule" aria-hidden="true">
              <span>or use the demo</span>
            </div>
            <PressButton variant="secondary" onClick={() => void demo()} loading={demoBusy}>
              {demoBusy ? (
                <>
                  <Spinner label="opening the demo" />
                  Opening the demo…
                </>
              ) : (
                'Open the demo workspace'
              )}
            </PressButton>
            <dl className="auth-demo-cred">
              <div>
                <dt>Approver</dt>
                <dd className="mono">{DEMO.approver}</dd>
              </div>
              <div>
                <dt>Admin</dt>
                <dd className="mono">{DEMO.admin}</dd>
              </div>
              <div>
                <dt>Password</dt>
                <dd className="mono">{DEMO.password}</dd>
              </div>
            </dl>
          </div>
        </div>

        <footer className="auth-foot">
          <span className="mono">priced → judged → signed → captured</span>
          <span>No real charges.</span>
        </footer>
      </motion.section>
    </main>
  )
}
