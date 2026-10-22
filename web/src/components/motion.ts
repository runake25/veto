import type { Variants } from 'motion/react'

/**
 * Stone-and-paper handfeel: content rises a few pixels and settles.
 * Large areas never overshoot; only small marks get any spring.
 */
export const stagger: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.06, delayChildren: 0.05 } },
}

export const riseIn: Variants = {
  hidden: { opacity: 0, y: 10 },
  show: { opacity: 1, y: 0, transition: { duration: 0.45, ease: [0.16, 1, 0.3, 1] } },
}

export const fadeIn: Variants = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { duration: 0.4, ease: [0.16, 1, 0.3, 1] } },
}

/** The sheet arriving once on first paint. No scale on something this large. */
export const sheetIn: Variants = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: { duration: 0.5, ease: [0.16, 1, 0.3, 1] } },
}

export const still: Variants = {
  hidden: { opacity: 1 },
  show: { opacity: 1 },
}

export const stillIn: Variants = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { duration: 0 } },
}

export function motionSet(reduce: boolean | null) {
  return reduce
    ? { stagger: still, riseIn: stillIn, fadeIn: stillIn, sheetIn: stillIn }
    : { stagger, riseIn, fadeIn, sheetIn }
}
