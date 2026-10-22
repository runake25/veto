import type { ReactNode } from 'react'
import { motion, useReducedMotion } from 'motion/react'

type Variant = 'primary' | 'secondary' | 'quiet'

interface PressButtonProps {
  children: ReactNode
  variant?: Variant
  type?: 'button' | 'submit'
  disabled?: boolean
  loading?: boolean
  className?: string
  onClick?: () => void
  'aria-label'?: string
}

/**
 * One press handfeel for the whole app: the control sinks 3% for 80ms and springs
 * back. Stone on paper — a click, not a bounce.
 */
export function PressButton({
  children,
  variant = 'primary',
  type = 'button',
  disabled,
  loading,
  className,
  onClick,
  ...rest
}: PressButtonProps) {
  const reduce = useReducedMotion()
  return (
    <motion.button
      type={type}
      className={`btn btn-${variant}${className ? ` ${className}` : ''}`}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      onClick={onClick}
      whileTap={reduce || disabled ? undefined : { scale: 0.97 }}
      transition={{ duration: 0.08 }}
      {...rest}
    >
      {children}
    </motion.button>
  )
}

export function Spinner({ label = 'working' }: { label?: string }) {
  return (
    <span className="spinner" role="status" aria-label={label}>
      <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2" />
        <motion.circle
          cx="8"
          cy="8"
          r="6"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeDasharray="10 28"
          animate={{ rotate: 360 }}
          transition={{ duration: 0.9, repeat: Infinity, ease: 'linear' }}
          style={{ transformOrigin: '8px 8px' }}
        />
      </svg>
    </span>
  )
}

/** The check mark drawn on completion (pathLength 0→1), shared by every "saved" state. */
export function Check({ size = 14 }: { size?: number }) {
  return (
    <svg className="check" viewBox="0 0 16 16" width={size} height={size} aria-hidden="true">
      <motion.path
        d="M3 8.4l3.2 3.2L13 4.8"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        initial={{ pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: 0.24, ease: 'easeOut' }}
      />
    </svg>
  )
}
