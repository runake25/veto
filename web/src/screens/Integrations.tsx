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

/**
 * MONEY: the one card that moves money. A connected sandbox vault is the state
 * that makes every capture in the record real, so it leads the page and its
 * facts sit in one strip rather than a definition list.
 */
function WalletCard({ data }: { data: IntegrationsData }) {
  const { wallet } = data
  const connected = wallet.status === 'connected'
  return (
    <section className="card integ-card integ-wallet">
      <header className="card-head">
        <h2>PayPal wallet</h2>
        <State tone={connected ? 'pos' : 'wait'}>
          {connected ? 'Connected' : 'Not connected'}
        </State>
      </header>

      <dl className="integ-facts">
        <div className="integ-fact">
          <dt>Mode</dt>
          <dd>{paymentMode(wallet.mode)}</dd>
        </div>
        {connected ? (
          <>
            <div className="integ-fact">
              <dt>Vault</dt>
              <dd className="mono" title={wallet.vault_id ?? undefined}>
                {maskId(wallet.vault_id)}
              </dd>
            </div>
            {wallet.payer_email ? (
              <div className="integ-fact">
                <dt>Payer</dt>
                <dd>{wallet.payer_email}</dd>
              </div>
            ) : null}
            <div className="integ-fact">
              <dt>Since</dt>
              <dd className="mono">{when(wallet.connected_at)}</dd>
            </div>
            <div className="integ-fact">
              <dt>Capture</dt>
              <dd>order → authorization → capture</dd>
            </div>
          </>
        ) : null}
      </dl>

      {connected ? null : (
        <>
          <p className="integ-note">
            No vault yet — a request that passes the rules stops at{' '}
            <span className="mono">428 wallet_required</span>; nothing is charged.
          </p>
          <div className="integ-wallet-cta">
            <a className="btn btn-primary" href={wallet.connect_url}>
              Connect the sandbox wallet
            </a>
          </div>
        </>
      )}
    </section>
  )
}

/**
 * ACCESS: the working card. Minting is the primary action on the page; keys are
 * per-agent, shown once on mint and revoked in two steps. Nothing here changes
 * for a viewer except that the write controls are disabled and the footnote
 * says why.
 */
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

  const activeCount = keys.filter((key) => !key.revoked_at).length
  // The agents that already hold a key are the working state of this card.
  const agents = [...data.agents].sort((a, b) => {
    const aOn = keys.some((key) => key.agent_id === a.id && !key.revoked_at) ? 0 : 1
    const bOn = keys.some((key) => key.agent_id === b.id && !key.revoked_at) ? 0 : 1
    return aOn - bOn
  })

  return (
    <section className="card integ-card integ-keys">
      <header className="card-head">
        <h2>Agent API keys</h2>
        <span className="integ-head-meta mono">
          {activeCount === 0
            ? 'No active keys'
            : `${activeCount} active key${activeCount === 1 ? '' : 's'}`}
        </span>
      </header>

      <div className="integ-curl">
        <span className="integ-curl-label">A request with a key</span>
        <code className="integ-curl-code">
          {`curl -X POST ${data.api.base_url}${data.api.requests_path} -H "Authorization: Bearer vkey_…" -d '{"text":"2 packs of toilet paper"}'`}
        </code>
      </div>

      {error ? <InlineError>{error}</InlineError> : null}
      {flash ? (
        <p className="integ-flash" role="status">
          {flash}
        </p>
      ) : null}

      {secret ? (
        <div className="integ-secret">
          <p className="integ-secret-title">
            New key for {secret.agent} — copy it now, it is shown once.
          </p>
          <div className="integ-secret-row">
            <CopyValue value={secret.token} label="Copy the new agent key" />
            <button
              type="button"
              className="btn btn-quiet btn-small"
              onClick={() => setSecret(null)}
            >
              Done
            </button>
          </div>
        </div>
      ) : null}

      {agents.length ? (
        <ul className="integ-key-rows">
          {agents.map((agent) => {
            const active = keys.filter((key) => key.agent_id === agent.id && !key.revoked_at)
            return (
              <li className="integ-key-row" key={agent.id}>
                <div className="integ-key-head">
                  <Link className="integ-key-name" to={`/agents/${agent.id}`}>
                    {agent.name}
                  </Link>
                  <span className="integ-key-tag mono">{agent.tag}</span>
                  <button
                    type="button"
                    className="btn btn-secondary btn-small integ-key-mint"
                    onClick={() => void mint(agent.id, agent.name)}
                    disabled={!admin || busy !== null}
                    title={admin ? `Mint a key for ${agent.name}` : 'Only a workspace admin mints keys'}
                  >
                    {busy === agent.id ? 'Minting…' : 'New key'}
                  </button>
                </div>
                {active.length ? (
                  <ul className="integ-key-tokens">
                    {active.map((key) => (
                      <li className="integ-key-token" key={key.id}>
                        <span className="mono integ-key-prefix">{key.prefix}…</span>
                        <span className="integ-key-meta">
                          created {when(key.created_at)}
                          {key.last_used_at
                            ? ` · last used ${when(key.last_used_at)}`
                            : ' · never used'}
                        </span>
                        <button
                          type="button"
                          className={`btn ${
                            confirmRevoke === key.id ? 'btn-danger' : 'btn-quiet'
                          } btn-small integ-key-revoke`}
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
                ) : null}
              </li>
            )
          })}
        </ul>
      ) : (
        <p className="integ-empty">No agents yet.</p>
      )}

      <p className="card-footnote">
        {admin
          ? 'Keys are stored hashed; the secret is shown once and revoking is immediate.'
          : 'Only an admin can mint or revoke keys; keys are stored hashed.'}
      </p>
    </section>
  )
}

/**
 * ACCESS: the reference card. Commands in two columns, the one caveat about an
 * unrouted request as a footnote, and nothing that repeats the page subtitle.
 */
function SlackCard({ data }: { data: IntegrationsData }) {
  const { slack } = data
  return (
    <section className="card integ-card integ-slack">
      <header className="card-head">
        <h2>Slack</h2>
        <State tone={slack.app_ready ? 'pos' : 'wait'}>
          {slack.app_ready ? 'Configured' : 'Not configured'}
        </State>
      </header>

      <ul className="integ-commands">
        {slack.commands.map((command) => (
          <li key={command.command}>
            <span className="mono integ-command">{command.command}</span>
            <span className="integ-command-desc">{command.description}</span>
          </li>
        ))}
      </ul>

      <div className="integ-endpoints">
        <div className="integ-endpoint">
          <span className="integ-endpoint-label">Request URL</span>
          <CopyValue value={slack.slash_url} label="Copy the Request URL" />
        </div>
        <div className="integ-endpoint">
          <span className="integ-endpoint-label">Interactivity URL</span>
          <CopyValue value={slack.interact_url} label="Copy the Interactivity URL" />
        </div>
        <div className="integ-endpoint">
          <span className="integ-endpoint-label">Signature</span>
          <State tone={slack.signing_secret ? 'pos' : 'wait'}>
            {slack.signing_secret ? 'Verified' : 'Missing'}
          </State>
        </div>
      </div>

      {!slack.app_ready ? (
        <p className="integ-warn">
          Set <span className="mono">SLACK_SIGNING_SECRET</span> and{' '}
          <span className="mono">SLACK_BOT_TOKEN</span>, then paste both URLs into the Slack app.
        </p>
      ) : null}

      {slack.default_agent ? (
        <p className="card-footnote">
          A request that names no agent is judged by {slack.default_agent.name}.
        </p>
      ) : null}
    </section>
  )
}

/**
 * Integrations = two groups. MONEY is the wallet that settles; ACCESS is the
 * two doors in — agent keys first (the working card), Slack second (reference).
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
    <motion.div className="screen integrations-screen" variants={m.stagger} initial="hidden" animate="show">
      <motion.header className="screen-head" variants={m.riseIn}>
        <div>
          <h1>Integrations</h1>
          <p>Connect the wallet, mint agent keys, wire up Slack.</p>
        </div>
        <div className="head-tools">
          <FreshnessButton />
        </div>
      </motion.header>

      {error ? (
        <InlineError onRetry={() => setReloadTick((value) => value + 1)}>{error}</InlineError>
      ) : null}

      {!data && !error ? (
        <div className="integ-groups" aria-hidden="true">
          <div className="integ-group">
            <span className="skel integ-label-skel" />
            <div className="card integ-card">
              <span className="skel" style={{ width: '24%', height: 16 }} />
              <span className="skel" style={{ width: '100%', height: 40, marginTop: 4 }} />
            </div>
          </div>
          <div className="integ-group">
            <span className="skel integ-label-skel" />
            <div className="card integ-card">
              <span className="skel" style={{ width: '32%', height: 16 }} />
              <span className="skel" style={{ width: '100%', height: 110, marginTop: 4 }} />
            </div>
          </div>
        </div>
      ) : null}

      {data ? (
        <motion.div className="integ-groups" variants={m.stagger} initial="hidden" animate="show">
          <motion.section
            className="integ-group"
            variants={m.riseIn}
            aria-labelledby="integ-group-money"
          >
            <p className="integ-group-label" id="integ-group-money">
              Money
            </p>
            <WalletCard data={data} />
          </motion.section>

          <motion.section
            className="integ-group"
            variants={m.riseIn}
            aria-labelledby="integ-group-access"
          >
            <p className="integ-group-label" id="integ-group-access">
              Access
            </p>
            <KeysCard data={data} me={me} />
            <SlackCard data={data} />
          </motion.section>
        </motion.div>
      ) : null}
    </motion.div>
  )
}
