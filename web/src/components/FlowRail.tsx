import { motion, useReducedMotion } from 'motion/react'
import { motionSet } from './motion'

const STEPS = [
  { title: 'Priced', note: 'Channel3 · real products, real merchants' },
  { title: 'Judged', note: "the agent's own rule set" },
  { title: 'Signed', note: 'a human, only when it breaks policy', seal: true },
  { title: 'Captured', note: 'PayPal, from the vaulted wallet' },
]

/**
 * The one loop, drawn as the product's own artefact: four marks on a line. The third
 * is hollow because that is the step that sometimes needs a person. No invented
 * numbers — this is the product telling you how it works.
 */
export function FlowRail() {
  const reduce = useReducedMotion()
  const m = motionSet(reduce)

  return (
    <div className="flow">
      <ol className="flow-rail">
        {STEPS.map((step) => (
          <motion.li key={step.title} className="flow-step" variants={m.riseIn}>
            <span className={`flow-mark${step.seal ? ' is-seal' : ''}`} aria-hidden="true" />
            <span className="flow-text">
              <b>{step.title}</b>
              <span>{step.note}</span>
            </span>
          </motion.li>
        ))}
      </ol>
      <motion.p className="flow-note" variants={m.riseIn}>
        Refused or vetoed, no PayPal order exists.
      </motion.p>
    </div>
  )
}
