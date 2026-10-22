import { useState, type FormEvent, type ReactNode } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { api, ApiError, type Me, type SessionData } from '../api'
import { FlowRail } from '../components/FlowRail'
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

/**
 * The door. Two ways in: a real account (B2B sign-up) or the seeded demo workspace,
 * which exists so a judge can interact in seconds. The left half is the product
 * explaining its own loop rather than a marketing panel.
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

  const switchMode = (next: Mode) => {
    if (busy || next === mode) return
    setError(null)
    setMode(next)
  }

  const stamp = reduce ? {} : { scale: status === 'success' ? [1, 1.015, 1] : 1 }

  return (
    <main className="ground auth-ground">
      <motion.div className="auth-sheet" variants={m.sheetIn} initial="hidden" animate="show">
        <motion.section
          className="auth-left"
          variants={m.stagger}
          initial="hidden"
          animate="show"
          aria-labelledby="auth-title"
        >
          <motion.div variants={m.riseIn}>
            <Wordmark />
          </motion.div>

          <motion.h1 id="auth-title" className="auth-title" variants={m.riseIn}>
            Agents spend.
            <br />
            Humans govern.
          </motion.h1>

          <motion.p className="auth-lead" variants={m.riseIn}>
            Veto sits between an AI agent and the company wallet. Every purchase is priced
            against real products, judged by that agent&apos;s own rules, and signed by a
            person before money moves.
          </motion.p>

          <FlowRail />

          <motion.footer className="auth-meta" variants={m.riseIn}>
            <span className="chip">PayPal sandbox</span>
            <span className="chip">USD</span>
            <span className="auth-meta-note mono">no real charges</span>
          </motion.footer>
        </motion.section>

        <motion.section className="auth-right" animate={stamp} transition={{ duration: 0.28 }}>
          <motion.div
            className="auth-panel"
            variants={m.stagger}
            initial="hidden"
            animate="show"
          >
            <motion.div className="seg" role="tablist" aria-label="Access" variants={m.riseIn}>
              <button
                type="button"
                role="tab"
                aria-selected={mode === 'signin'}
                className={`seg-btn${mode === 'signin' ? ' is-on' : ''}`}
                onClick={() => switchMode('signin')}
                disabled={busy}
              >
                {mode === 'signin' && (
                  <motion.span
                    layoutId="seg-thumb"
                    className="seg-thumb"
                    transition={
                      reduce ? { duration: 0 } : { type: 'spring', stiffness: 380, damping: 32 }
                    }
                  />
                )}
                <span className="seg-label">Sign in</span>
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={mode === 'signup'}
                className={`seg-btn${mode === 'signup' ? ' is-on' : ''}`}
                onClick={() => switchMode('signup')}
                disabled={busy}
              >
                {mode === 'signup' && (
                  <motion.span
                    layoutId="seg-thumb"
                    className="seg-thumb"
                    transition={
                      reduce ? { duration: 0 } : { type: 'spring', stiffness: 380, damping: 32 }
                    }
                  />
                )}
                <span className="seg-label">Create workspace</span>
              </button>
            </motion.div>

            <form className="auth-form" onSubmit={submit} noValidate>
              <AnimatePresence mode="wait" initial={false}>
                <motion.div
                  key={mode}
                  className="auth-fields"
                  initial={{ opacity: 0, y: reduce ? 0 : 5 }}
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
                          placeholder="Priya Raman"
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

              <PressButton type="submit" loading={status === 'busy'} className="submit">
                <AnimatePresence mode="wait" initial={false}>
                  <motion.span
                    key={status}
                    className="btn-face"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0, transition: { duration: 0.08 } }}
                    transition={{ duration: 0.14 }}
                  >
                    {status === 'idle' && (mode === 'signin' ? 'Sign in' : 'Create workspace')}
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

            <motion.div className="auth-sep" variants={m.riseIn}>
              <span>or</span>
            </motion.div>

            <motion.div variants={m.riseIn}>
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
              <p className="auth-small">Seeded example workspace and agents. No sign-up needed.</p>
            </motion.div>
          </motion.div>
        </motion.section>
      </motion.div>

      <p className="auth-legend">
        Sandbox environment · money moves only from the vaulted <span className="mono">PayPal</span>{' '}
        wallet
      </p>
    </main>
  )
}
