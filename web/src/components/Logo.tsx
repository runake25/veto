/**
 * The mark: a V that is also a tick, with two eyes punched into its chin.
 *
 * The shape says both things the product means. Read as a letter it is the V of
 * Veto; read as a gesture it is a tick — approval — with the short arm on the
 * left and the long one sweeping up to the right. The two slots are the eyes,
 * so the mark is a face that approves, and the white pillar between them is what
 * keeps the chin from reading as a plain letter. It is drawn as one filled path
 * (no strokes, no runtime mask), so it stays crisp from 16px to 512px.
 *
 * The tile is the only place the brand teal appears: money state owns green,
 * red and amber, and the mark must never be mistaken for one of them.
 */
export function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      aria-hidden="true"
      className="logo-mark"
    >
      <rect width="24" height="24" rx="4.6" fill="var(--brand-tile)" />
      <path
        d="M4.59 8.38 L7.21 7.82 Q7.65 7.72 7.93 8.07 L10.87 11.80 Q11.30 12.35 11.71 11.78 L15.74 6.12 Q16.00 5.75 16.45 5.71 L20.00 5.42 L13.56 19.04 C12.85 20.62 10.05 20.62 9.32 19.04 L4.30 8.99 Q4.05 8.50 4.59 8.38 Z"
        fill="var(--brand-ink)"
      />
      <rect x="9.80" y="12.85" width="1.22" height="5.25" rx="0.42" fill="var(--brand-tile)" />
      <rect x="11.98" y="12.85" width="1.26" height="5.25" rx="0.42" fill="var(--brand-tile)" />
    </svg>
  )
}

export function Wordmark() {
  return (
    <span className="wordmark">
      <Logo />
      <span>veto</span>
    </span>
  )
}
