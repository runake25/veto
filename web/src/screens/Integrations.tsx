import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion, useReducedMotion } from 'motion/react'
import { api, ApiError, type IntegrationsData, type Me } from '../api'
import { CopyValue, InlineError } from '../components/Bits'
import { FreshnessButton, useFreshness } from '../components/freshness'
import { motionSet } from '../components/motion'
import { when } from '../components/request'
import { State, paymentMode } from '../components/state'

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
        <State tone={ready ? 'pos' : 'wait'}>
          {ready ? 'Commands configured' : 'Not configured'}
        </State>
      </header>
      <p className="card-lede">
        <span className="mono">/veto</span> buys through the rules; <span className="mono">
        /veto-check</span> shows the shortlist so a person can pick the exact offer; the rest
        show the manager&rsquo;s side — what waits for a signature, this month&rsquo;s money,
        and the standing jobs. One Request URL serves all of them.
      </p>

      <div className="endpoints">
        <p className="route-title">Commands</p>
        <ul className="command-list">
          {slack.commands.map((command) => (
            <li key={command.command}>
              <span className="command-name mono">{command.command}</span>
              <span className="command-text">
                <span className="command-desc">{command.description}</span>
                {command.usage_hint ? (
                  <span className="command-hint mono">{command.usage_hint}</span>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
        <div className="endpoint">
          <span className="endpoint-label">Request URL</span>
          <CopyValue value={slack.slash_url} label="Copy the Request URL" />
        </div>
        <div className="endpoint">
          <span className="endpoint-label">Interactivity</span>
          <CopyValue value={slack.interact_url} label="Copy the Interactivity URL" />
        </div>
        <div className="endpoint">
          <span className="endpoint-label">Signature</span>
          <State tone={slack.signing_secret ? 'pos' : 'neg'}>
            {slack.signing_secret ? 'Verified' : 'Secret missing'}
          </State>
        </div>
      </div>

      {!ready ? (
        <p className="integration-warn">
          Set <span className="mono">SLACK_SIGNING_SECRET</span> and{' '}
          <span className="mono">SLACK_BOT_TOKEN</span> in <span className="mono">.env</span>, then
          paste both URLs into the Slack app.
        </p>
      ) : null}
      <p className="card-footnote">
        On the hosted demo both URLs are stable. A self-hosted runner behind a quick tunnel gets
        a new host when it restarts, and both URLs have to be pasted again.
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
        <State tone={connected ? 'pos' : 'wait'}>
          {connected ? 'Connected' : 'Not connected'}
        </State>
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
            <dd>{paymentMode(wallet.mode)}</dd>
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

/** Where a Slack request without a named agent is judged. */
function RoutingCard({ data }: { data: IntegrationsData }) {
  return (
    <section className="card integration-card">
      <header className="card-head">
        <h2>Model routing</h2>
        <span className="card-note">who judges a request</span>
      </header>
      <p className="card-lede">
        Each request is matched by what is being bought, so a cart typed in
        <span className="mono"> #ops</span> can still be judged by the pantry agent.
      </p>
      {data.agents.length ? (
        <ul className="route-list">
          {data.agents.map((agent) => (
            <li key={agent.id}>
              <span className="route-agent-line">
                <Link className="route-agent" to={`/agents/${agent.id}`}>
                  {agent.name}
                </Link>
                <span className="route-scope">
                  {agent.categories.length ? agent.categories.join(', ') : 'any category'}
                  {agent.merchants.length
                    ? ` · ${agent.merchants.length} merchants`
                    : ' · no merchants'}
                </span>
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="record-none">No agents yet.</p>
      )}
      {data.slack.default_agent ? (
        <p className="card-footnote">
          If the model finds no fit, {data.slack.default_agent.name} judges the request.
        </p>
      ) : null}
    </section>
  )
}

function KeysCard({ data, me }: { data: IntegrationsData; me: Me }) {
  const admin = me.user.role === 'admin'
  const [secret, setSecret] = useState<{ agent: string; token: string } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [keys, setKeys] = useState(data.keys)
  const [confirmRevoke, setConfirmRevoke] = useState<string | null>(null)
  const [flash, setFlash] = useState<string | null>(null)

  useEffect(() => setKeys(data.keys), [data.keys])

  const reload = async () => {
    const next = await api.integrations()
    setKeys(next.keys)
  }

  const mint = async (agentId: string, agentName: string) => {
    setBusy(agentId)
    setError(null)
    setFlash(null)
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
    setFlash(null)
    try {
      await api.revokeKey(agentId, keyId)
      setConfirmRevoke(null)
      setFlash('Key revoked. That agent’s next request is refused with 401.')
      window.setTimeout(() => setFlash(null), 6000)
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

      {error ? <InlineError>{error}</InlineError> : null}
      {flash ? (
        <p className="record-flash" role="status">
          {flash}
        </p>
      ) : null}

      {secret ? (
        <div className="key-secret">
          <p className="key-secret-title">
            New key for {secret.agent} — shown once, copy it now.
          </p>
          <div className="key-secret-row">
            <CopyValue value={secret.token} label="Copy the new agent key" />
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
                        className={`btn ${
                          confirmRevoke === key.id ? 'btn-danger' : 'btn-quiet'
                        } btn-small key-revoke`}
                        onClick={() => {
                          if (confirmRevoke !== key.id) {
                            setConfirmRevoke(key.id)
                            return
                          }
                          void revoke(agent.id, key.id)
                        }}
                        disabled={!admin || busy !== null}
                        title={
                          confirmRevoke === key.id
                            ? 'Confirm: this agent key stops working immediately'
                            : 'Revoke this key'
                        }
                      >
                        {busy === key.id
                          ? 'Revoking…'
                          : confirmRevoke === key.id
                            ? 'Confirm revoke'
                            : 'Revoke'}
                      </button>
                      {confirmRevoke === key.id ? (
                        <button
                          type="button"
                          className="btn btn-quiet btn-small"
                          onClick={() => setConfirmRevoke(null)}
                        >
                          Cancel
                        </button>
                      ) : null}
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
 * Integrations: the three doors into the record — Slack, the PayPal wallet and
 * the agent API — each with the state it is actually in. The wallet and routing
 * share a column so neither is a lonely card beside a tall one.
 */
export function Integrations({ me }: { me: Me }) {
  const [data, setData] = useState<IntegrationsData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reloadTick, setReloadTick] = useState(0)
  const { tick, markLoaded } = useFreshness()
  const reduce = useReducedMotion()
  const m = motionSet(reduce)

  useEffect(() => {
    let live = true
    setError(null)
    api
      .integrations()
      .then((next) => {
        if (!live) return
        setData(next)
        markLoaded()
      })
      .catch((err) => {
        if (!live) return
        setError(err instanceof ApiError ? err.message : 'The integrations did not load.')
      })
    return () => {
      live = false
    }
  }, [tick, reloadTick, markLoaded])

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
        <div className="head-tools">
          <FreshnessButton />
        </div>
      </motion.header>

      {error ? (
        <InlineError onRetry={() => setReloadTick((value) => value + 1)}>{error}</InlineError>
      ) : null}

      {!data && !error ? (
        <div className="integrations-grid" aria-hidden="true">
          <div className="card">
            <span className="skel" style={{ width: '28%', height: 16 }} />
            <span className="skel" style={{ width: '100%', height: 34, marginTop: 14 }} />
            <span className="skel" style={{ width: '100%', height: 140, marginTop: 12 }} />
          </div>
          <div className="integrations-side">
            <div className="card">
              <span className="skel" style={{ width: '36%', height: 16 }} />
              <span className="skel" style={{ width: '100%', height: 96, marginTop: 14 }} />
            </div>
            <div className="card">
              <span className="skel" style={{ width: '30%', height: 16 }} />
              <span className="skel" style={{ width: '100%', height: 64, marginTop: 14 }} />
            </div>
          </div>
        </div>
      ) : null}

      {data ? (
        <>
          <div className="integrations-grid">
            <SlackCard data={data} />
            <div className="integrations-side">
              <WalletCard data={data} />
              <RoutingCard data={data} />
            </div>
          </div>
          <KeysCard data={data} me={me} />
        </>
      ) : null}
    </motion.div>
  )
}
