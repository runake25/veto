import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

/**
 * Freshness: one place that knows the store changed, so a Slack approval lands
 * on an open Inbox without a manual refresh.
 *
 * The primary signal is the server's change stream (`GET /api/v1/events`), which
 * the store publishes to whenever a request, agent, job or key is written.
 * A 30-second poll and a refetch whenever the tab becomes visible are the
 * fallbacks: a sleeping free instance, a proxy that buffers the stream, or an
 * old browser must never leave the board silently stale.
 */
type FreshnessValue = {
  /** Bumped whenever anything may have changed; screens use it as a fetch dep. */
  tick: number
  /** When the last successful read finished, for the header label. */
  updatedAt: Date | null
  /** True while the change stream is connected. */
  live: boolean
  /** Fetch now (the Refresh link). */
  refresh: () => void
  /** Called by a screen after its data arrives. */
  markLoaded: () => void
}

const FreshnessContext = createContext<FreshnessValue | null>(null)

const RETRY_MS = 8_000

export function FreshnessProvider({ children }: { children: ReactNode }) {
  const [tick, setTick] = useState(0)
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const [live, setLive] = useState(false)

  const bump = useCallback(() => setTick((value) => value + 1), [])
  const markLoaded = useCallback(() => setUpdatedAt(new Date()), [])

  useEffect(() => {
    let source: EventSource | null = null
    let retry: number | undefined
    let closed = false

    const connect = () => {
      if (closed) return
      source = new EventSource('/api/v1/events')
      source.onopen = () => setLive(true)
      source.onmessage = (event) => {
        let frame: { type?: string } = {}
        try {
          frame = JSON.parse(event.data) as { type?: string }
        } catch {
          // a frame we cannot read is not a reason to break the board
          return
        }
        if (frame.type === 'change') bump()
      }
      source.onerror = () => {
        // The server closed the stream (restart, sleep) or it never opened.
        // Reconnect with a pause instead of hammering a cold instance.
        setLive(false)
        source?.close()
        source = null
        if (!closed) retry = window.setTimeout(connect, RETRY_MS)
      }
    }

    connect()

    const onVisible = () => {
      if (document.visibilityState === 'visible') bump()
    }
    const onOnline = () => bump()
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('online', onOnline)

    const poll = window.setInterval(() => {
      if (document.visibilityState === 'visible') bump()
    }, 30_000)

    return () => {
      closed = true
      if (retry) window.clearTimeout(retry)
      source?.close()
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('online', onOnline)
      window.clearInterval(poll)
    }
  }, [bump])

  const value = useMemo<FreshnessValue>(
    () => ({ tick, updatedAt, live, refresh: bump, markLoaded }),
    [tick, updatedAt, live, bump, markLoaded],
  )

  return <FreshnessContext.Provider value={value}>{children}</FreshnessContext.Provider>
}

export function useFreshness(): FreshnessValue {
  const value = useContext(FreshnessContext)
  if (!value) throw new Error('useFreshness must be used inside FreshnessProvider')
  return value
}

/**
 * The header's one freshness control: when the board last read, whether the
 * change stream is up, and a way to read now.
 */
export function FreshnessButton() {
  const { updatedAt, live, refresh } = useFreshness()
  const stamp = updatedAt
    ? updatedAt.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
    : null
  return (
    <button
      type="button"
      className="refresh-link"
      onClick={refresh}
      title={
        live
          ? 'Live — approvals from Slack appear here by themselves. Click to read now.'
          : 'Reconnecting to live updates; this button still reads the record now.'
      }
    >
      <span className={`freshness-dot${live ? ' is-live' : ''}`} aria-hidden="true" />
      {/* the stamp is machine voice; the stream state is not, so only the time is mono */}
      <span className="mono">{stamp ? `Updated ${stamp}` : 'Not read yet'}</span>
      <span aria-hidden="true">·</span>
      <span>{live ? 'Live' : 'Refresh'}</span>
    </button>
  )
}
