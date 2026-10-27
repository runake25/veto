/**
 * The mark: the V of the name with the seal stamped onto its right shoulder.
 * The stroke and the seal are one object — a stamp landing on a page — so the
 * mark reads at 16px (favicon) and at 44px (auth). No enclosing frame: a framed
 * square with a tick inside reads as a checkbox, which is the opposite of what
 * this product does.
 */
export function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      className="logo-mark"
    >
      <path
        d="M3.4 5.2 11.6 19.4 17.6 8.2"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <rect className="logo-seal" x="14.6" y="1.4" width="6.6" height="6.6" rx="1.9" />
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
