import { useCallback, useEffect, useState } from 'react'
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { api, ApiError, type Me } from './api'
import { Auth } from './screens/Auth'
import { AgentDetail } from './screens/AgentDetail'
import { Agents } from './screens/Agents'
import { NewAgent } from './screens/NewAgent'
import { Overview } from './screens/Overview'
import { Inbox } from './screens/Inbox'
import { Record as RequestRecord } from './screens/Record'
import { Analytics } from './screens/Analytics'
import { Integrations } from './screens/Integrations'
import { Shell } from './screens/Shell'
import { BootFailure, BootSheet } from './components/BootSheet'

type Boot =
  | { state: 'loading' }
  | { state: 'anon' }
  | { state: 'ready'; me: Me }
  | { state: 'error'; message: string }

/** What to tell a person when the session check could not reach the engine. */
function bootErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 0) return 'The network is not answering, or the instance is still waking up.'
    return err.message
  }
  return 'The session check did not finish. This is usually a network problem.'
}

export default function App() {
  const [boot, setBoot] = useState<Boot>({ state: 'loading' })
  const navigate = useNavigate()

  const bootSession = useCallback(async () => {
    setBoot({ state: 'loading' })
    try {
      const me = await api.me()
      setBoot({ state: 'ready', me })
    } catch (err) {
      // 401 is the ordinary "nobody is signed in" answer. A network failure or a
      // server fault gets its own state: sending that to the sign-in screen is a
      // dead end, because the same engine has to answer the login.
      if (err instanceof ApiError && err.status === 401) {
        setBoot({ state: 'anon' })
      } else if (err instanceof ApiError && (err.status === 0 || err.status >= 500)) {
        setBoot({ state: 'error', message: bootErrorMessage(err) })
      } else {
        setBoot({ state: 'anon' })
      }
    }
  }, [])

  useEffect(() => {
    void bootSession()
  }, [bootSession])

  if (boot.state === 'loading') return <BootSheet />

  if (boot.state === 'error') {
    return (
      <BootFailure
        message={boot.message}
        retryBusy={false}
        onRetry={() => void bootSession()}
        onSignIn={() => setBoot({ state: 'anon' })}
      />
    )
  }

  if (boot.state === 'anon') {
    return (
      <Routes>
        <Route
          path="*"
          element={
            <Auth
              onSignedIn={(me) => {
                setBoot({ state: 'ready', me })
                navigate('/', { replace: true })
              }}
            />
          }
        />
      </Routes>
    )
  }

  const signOut = async () => {
    try {
      await api.logout()
    } catch {
      // the cookie may already be gone; the UI state is what matters
    }
    setBoot({ state: 'anon' })
    navigate('/login', { replace: true })
  }

  return (
    <Routes>
      <Route path="/login" element={<Navigate to="/" replace />} />
      <Route element={<Shell me={boot.me} onSignOut={() => void signOut()} />}>
        <Route index element={<Overview />} />
        <Route path="inbox" element={<Inbox />} />
        <Route path="inbox/:id" element={<RequestRecord me={boot.me} />} />
        <Route path="agents" element={<Agents />} />
        <Route path="agents/new" element={<NewAgent />} />
        <Route path="agents/:id" element={<AgentDetail me={boot.me} />} />
        <Route path="analytics" element={<Analytics />} />
        <Route path="integrations" element={<Integrations me={boot.me} />} />
        <Route path="*" element={<Overview />} />
      </Route>
    </Routes>
  )
}
