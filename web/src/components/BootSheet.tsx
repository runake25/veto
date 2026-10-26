import { motion, useReducedMotion } from 'motion/react'
import { Wordmark } from './Logo'
import { PressButton } from './PressButton'

/**
 * First paint while the session check is in flight. Same sheet as the rest of the app,
 * so nothing jumps when the real screen arrives.
 */
export function BootSheet() {
  const reduce = useReducedMotion()
  const pulse = reduce
    ? {}
    : {
        animate: { opacity: [1, 0.55, 1] },
        transition: { duration: 1.6, repeat: Infinity, ease: 'easeInOut' as const },
      }

  return (
    <main className="ground">
      <div className="boot">
        <Wordmark />
        <motion.div className="boot-lines" {...pulse}>
          <span className="skel" style={{ width: '52%', height: 18 }} />
          <span className="skel" style={{ width: '34%', height: 12 }} />
          <span className="skel" style={{ width: '44%', height: 12 }} />
        </motion.div>
        <p className="boot-note mono">checking session</p>
      </div>
    </main>
  )
}

/**
 * The session check failed in a way a retry can fix — the engine is asleep,
 * restarting or unreachable — so the app says that instead of showing a sign-in
 * form that cannot work and pretending nothing happened.
 */
export function BootFailure({
  message,
  retryBusy,
  onRetry,
  onSignIn,
}: {
  message: string
  retryBusy: boolean
  onRetry: () => void
  onSignIn: () => void
}) {
  return (
    <main className="ground">
      <div className="boot boot-failure">
        <Wordmark />
        <div className="boot-error">
          <h1 className="boot-title">The console cannot reach its engine.</h1>
          <p className="boot-message">{message}</p>
          <p className="boot-hint">
            A free hosted instance sleeps when it is idle; the first wake can take about a
            minute. Nothing on the page was lost — this is only the session check.
          </p>
        </div>
        <div className="form-actions">
          <PressButton onClick={onRetry} loading={retryBusy}>
            Try again
          </PressButton>
          <PressButton variant="quiet" onClick={onSignIn}>
            Continue to sign in
          </PressButton>
        </div>
      </div>
    </main>
  )
}
