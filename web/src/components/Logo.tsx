/**
 * The mark: the V of the name with the seal on its shoulder. No enclosing frame —
 * a framed square with a tick inside reads as a checkbox, which is the opposite of
 * what this product does.
 */
export function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 20 20"
      fill="none"
      aria-hidden="true"
      className="logo-mark"
    >
      <path
        d="M3.4 4.6L10 16.4l6.6-11.8"
        stroke="currentColor"
        strokeWidth="2.1"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <rect x="13.5" y="2.2" width="4.6" height="4.6" rx="1.5" fill="var(--accent)" />
    </svg>
  )
}

export function Wordmark() {
  return (
    <span className="wordmark">
      <Logo />
      <span>Veto</span>
    </span>
  )
}
