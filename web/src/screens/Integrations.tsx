import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion, useReducedMotion } from 'motion/react'
import { api, ApiError, type IntegrationsData, type Me } from '../api'
import { motionSet } from '../components/motion'
import { when } from '../components/request'

function CopyButton({ value, label = 'Copy' }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      setCopied(false)
    }
  }
  return (
    <button type="button" className="copy-btn mono" onClick={() => void copy()}>
      {copied ? 'copied' : label}
    </button>
  )
}

function channelLabel(channel: string): string {
  return channel.startsWith('#') ? channel : `#${channel}`
}

function maskId(value: string | null): string {
  if (!value) return '—'
  if (value.length <= 10) return value
  return `${value.slice(0, 4)}…${value.slice(-4)}`
}

function SlackCard({ data }: { data: IntegrationsData }) {
  const { slack } = data
  const ready = slack.app_ready
  return (
    <section className="card integration-card">
      <header className="card-head">
        <h2>Slack</h2>
        <span className={`integration-state ${ready ? 'is-on' : 'is-off'}`}>
          <span className="mark-dot" aria-hidden="true" />
          {ready ? 'commands configured' : 'not configured'}
        </span>
      </header>
      <p className="card-lede">
        <span className="mono">/veto</span> takes a cart in plain words. The agent that answers is
        the one whose channel matches, so the same workspace can run several agents side by side.
      </p>

      <div className="endpoints">
        <p className="route-title">Endpoints</p>
        <div className="endpoint">
          <span className="endpoint-label">Slash command</span>
          <span className="endpoint-value mono">{slack.slash_command}</span>
        </div>
        <div className="endpoint">
          <span className="endpoint-label">Request URL</span>
          <span className="endpoint-value mono">{slack.slash_url}</span>
          <CopyButton value={slack.slash_url} />
        </div>
        <div className="endpoint">
          <span className="endpoint-label">Interactivity</span>
          <span className="endpoint-value mono">{slack.interact_url}</span>
          <CopyButton value={slack.interact_url} />
        </div>
        <div className="endpoint">
          <span className="endpoint-label">Signature</span>
          <span className="endpoint-value mono">
            {slack.signing_secret ? 'verified' : 'secret missing'}
          </span>
        </div>
      </div>

      <div className="route-block">
        <p className="route-title">Channel routing</p>
        {slack.routes.length ? (
          <ul className="route-list">
            {slack.routes.map((route) => (
              <li key={route.agent_id}>
                <span className="mono route-channel">{channelLabel(route.channel)}</span>
                <span className="route-arrow" aria-hidden="true">
                  →
                </span>
                <Link className="route-agent" to={`/agents/${route.agent_id}`}>
                  {route.agent_name}
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="record-none">No agent has a channel yet.</p>
        )}
        {slack.default_agent ? (
          <p className="card-footnote">
            A channel with no match falls back to {slack.default_agent.name}.
          </p>
        ) : null}
      </div>

      {!ready ? (
        <p className="integration-warn">
          Set <span className="mono">SLACK_SIGNING_SECRET</span> and{' '}
          <span className="mono">SLACK_BOT_TOKEN</span> in <span className="mono">.env</span>, then
          paste both URLs into the Slack app.
        </p>
      ) : null}
      <p className="card-footnote">
        The tunnel host changes when the runner restarts. Paste both URLs into the Slack app
        again after a restart.
      </p>
    </section>
  )
}

function WalletCard({ data }: { data: IntegrationsData }) {
  const { wallet } = data
  const connected = wallet.status === 'connected'
  return (
    <section className="card integration-card">
      <header className="card-head">
        <h2>PayPal wallet</h2>
        <span className={`integration-state ${connected ? 'is-on' : 'is-off'}`}>
          <span className="mark-dot" aria-hidden="true" />
          {connected ? `${wallet.mode} · connected` : 'not connected'}
        </span>
      </header>
      <p className="card-lede">
        Veto never charges a card it cannot name. Every capture comes from this vaulted payer, and
        every PayPal object is written into the request record.
      </p>
      {connected ? (
        <dl className="record-rows">
          <div className="record-row">
            <dt>Vault</dt>
            <dd className="mono">{maskId(wallet.vault_id)}</dd>
          </div>
          {wallet.payer_email ? (
            <div className="record-row">
              <dt>Payer</dt>
              <dd>{wallet.payer_email}</dd>
            </div>
          ) : null}
          <div className="record-row">
            <dt>Connected</dt>
            <dd className="mono">{when(wallet.connected_at)}</dd>
          </div>
          <div className="record-row">
            <dt>Mode</dt>
            <dd className="mono">{wallet.mode}</dd>
          </div>
        </dl>
      ) : (
        <p className="record-none">
          Without a vault, a request that passes the rules stops at the wallet with{' '}
          <span className="mono">428 wallet_required</span>. Nothing is charged.
        </p>
      )}
      {!connected ? (
        <a className="text-link mono" href={wallet.connect_url}>
          connect the sandbox wallet →
        </a>
      ) : (
        <p className="card-footnote">
          Sandbox vaulted wallet: order → authorization → capture, with real PayPal ids in the
          record.
        </p>
      )}
    </section>
  )
}

function KeysCard({ data, me }: { data: IntegrationsData; me: Me }) {
  const admin = me.user.role === 'admin'
  const [secret, setSecret] = useState<{ agent: string; token: string } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [keys, setKeys] = useState(data.keys)

  useEffect(() => setKeys(data.keys), [data.keys])

  const reload = async () => {
    const next = await api.integrations()
    setKeys(next.keys)
  }

  const mint = async (agentId: string, agentName: string) => {
    setBusy(agentId)
    setError(null)
    setSecret(null)
    try {
      const created = await api.mintKey(agentId)
      setSecret({ agent: agentName, token: created.secret })
      await reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The key was not minted.')
    } finally {
      setBusy(null)
    }
  }

  const revoke = async (agentId: string, keyId: string) => {
    setBusy(keyId)
    setError(null)
    try {
      await api.revokeKey(agentId, keyId)
      await reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The key was not revoked.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="card integration-card keys-card">
      <header className="card-head">
        <h2>Agent API keys</h2>
        <span className="card-note mono">{keys.filter((key) => !key.revoked_at).length} active</span>
      </header>
      <p className="card-lede">
        A key lets one agent, and only that agent, ask for a cart. It carries{' '}
        <span className="mono">Authorization: Bearer vkey_…</span>; every request it writes is
        scoped to the agent&rsquo;s own charter.
      </p>

      <div className="api-sample">
        <span className="api-sample-label mono">a request with a key</span>
        <pre className="api-code mono">{`curl -X POST ${data.api.base_url}${data.api.requests_path} \\
  -H "Authorization: Bearer vkey_…" \\
  -H "Content-Type: application/json" \\
  -d '{"text":"2 packs of bamboo toilet paper"}'`}</pre>
      </div>

      {error ? (
        <p className="inline-error" role="alert">
          {error}
        </p>
      ) : null}

      {secret ? (
        <div className="key-secret">
          <p className="key-secret-title">
            New key for {secret.agent} — shown once, copy it now.
          </p>
          <div className="key-secret-row">
            <code className="mono key-secret-value">{secret.token}</code>
            <CopyButton value={secret.token} />
          </div>
          <button type="button" className="btn btn-quiet btn-small" onClick={() => setSecret(null)}>
            Done
          </button>
        </div>
      ) : null}

      <ul className="key-rows">
        {data.agents.map((agent) => {
          const agentKeys = keys.filter((key) => key.agent_id === agent.id)
          const active = agentKeys.filter((key) => !key.revoked_at)
          return (
            <li className="key-agent" key={agent.id}>
              <div className="key-agent-head">
                <span className="key-agent-name">
                  <Link className="text-link" to={`/agents/${agent.id}`}>
                    {agent.name}
                  </Link>
                  <span className="mono key-agent-tag">{agent.tag}</span>
                </span>
                <button
                  type="button"
                  className="btn btn-secondary btn-small"
                  onClick={() => void mint(agent.id, agent.name)}
                  disabled={!admin || busy !== null}
                  title={admin ? 'Mint a key for this agent' : 'Only a workspace admin mints keys'}
                >
                  {busy === agent.id ? 'Minting…' : 'New key'}
                </button>
              </div>
              {active.length ? (
                <ul className="key-list">
                  {active.map((key) => (
                    <li key={key.id}>
                      <span className="mono key-prefix">{key.prefix}…</span>
                      <span className="key-meta mono">
                        created {when(key.created_at)}
                        {key.last_used_at ? ` · last used ${when(key.last_used_at)}` : ' · never used'}
                      </span>
                      <button
                        type="button"
                        className="btn btn-quiet btn-small key-revoke"
                        onClick={() => void revoke(agent.id, key.id)}
                        disabled={!admin || busy !== null}
                      >
                        {busy === key.id ? 'Revoking…' : 'Revoke'}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="key-none mono">no active key</p>
              )}
            </li>
          )
        })}
      </ul>

      <p className="card-footnote">
        {admin
          ? 'Keys are stored hashed; the secret is shown once, on mint. Revoking is immediate.'
          : 'Only a workspace admin can mint or revoke a key. Every key is stored hashed.'}
      </p>
    </section>
  )
}

/**
 * Integrations: the three doors into the record — Slack, the agent API and the
 * PayPal wallet — each with the state it is actually in.
 */
export function Integrations({ me }: { me: Me }) {
  const [data, setData] = useState<IntegrationsData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const reduce = useReducedMotion()
  const m = motionSet(reduce)

  useEffect(() => {
    let live = true
    api
      .integrations()
      .then((next) => {
        if (live) setData(next)
      })
      .catch((err) => {
        if (!live) return
        setError(err instanceof ApiError ? err.message : 'The integrations did not load.')
      })
    return () => {
      live = false
    }
  }, [])

  return (
    <motion.div className="screen" variants={m.stagger} initial="hidden" animate="show">
      <motion.header className="screen-head" variants={m.riseIn}>
        <div>
          <h1>Integrations</h1>
          <p>
            Where requests come from, and the wallet that settles them. A request made with this
            console's session is recorded with source <span className="mono">Web</span>.
          </p>
        </div>
      </motion.header>

      {error ? (
        <p className="inline-error" role="alert">
          {error}
        </p>
      ) : null}

      {!data && !error ? (
        <div className="integrations-grid">
          {[0, 1].map((i) => (
            <div className="card" key={i}>
              <span className="skel" style={{ width: '40%', height: 16 }} />
              <span className="skel" style={{ width: '100%', height: 90, marginTop: 14 }} />
            </div>
          ))}
        </div>
      ) : null}

      {data ? (
        <>
          <div className="integrations-grid">
            <SlackCard data={data} />
            <WalletCard data={data} />
          </div>
          <KeysCard data={data} me={me} />
        </>
      ) : null}
    </motion.div>
  )
}
