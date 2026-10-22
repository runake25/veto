import { motion, useReducedMotion } from 'motion/react'
import { Wordmark } from './Logo'

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
