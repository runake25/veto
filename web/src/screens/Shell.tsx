import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { api, initials, type Agent, type Me } from '../api'
import { AgentAvatar } from '../components/AgentAvatar'
import { FreshnessProvider, useFreshness } from '../components/freshness'
import { Wordmark } from '../components/Logo'
import { agentState, State } from '../components/state'

const TITLES: Record<string, string> = {
  '/': 'Overview · Veto',
  '/inbox': 'Inbox · Veto',
  '/agents': 'Agents · Veto',
  '/analytics': 'Analytics · Veto',
  '/integrations': 'Integrations · Veto',
  '/settings': 'Settings · Veto',
}

function titleFor(pathname: string): string {
  if (pathname.startsWith('/inbox/')) return TITLES['/inbox'] ?? 'Inbox · Veto'
  if (pathname.startsWith('/agents/')) return TITLES['/agents'] ?? 'Agents · Veto'
  return TITLES[pathname] ?? 'Veto — spend control'
}

type NavSection = { to: string; label: string; end?: boolean }

const CONTROL: { label: string; sections: NavSection[] } = {
  label: 'Control',
  sections: [
    { to: '/', label: 'Overview', end: true },
    { to: '/inbox', label: 'Inbox' },
    { to: '/agents', label: 'Agents' },
    { to: '/analytics', label: 'Analytics' },
  ],
}

/**
 * Setup is the rail's bottom cluster, beside the account facts: connecting a
 * wallet or renaming a workspace is a visit, not the day's work.
 */
const SETUP: { label: string; sections: NavSection[] } = {
  label: 'Setup',
  sections: [
    { to: '/integrations', label: 'Integrations' },
    { to: '/settings', label: 'Settings' },
  ],
}

const RAIL_KEY = 'veto.instances'

/**
 * The account menu exists for the narrow layout, where the rail (and with it
 * the wallet state and sign-out) is hidden. It carries the same facts the rail
 * does — who you are, which workspace, whether the wallet is connected — plus
 * the way out.
 */
function AccountMenu({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const [open, setOpen] = useState(false)
  const wrapper = useRef<HTMLDivElement | null>(null)
  const toggle = useRef<HTMLButtonElement | null>(null)
  const location = useLocation()
  const walletOn = me.wallet.status === 'connected'

  // a route change is never a reason to keep a popover open
  useEffect(() => {
    setOpen(false)
  }, [location.pathname])

  useEffect(() => {
    if (!open) return
    const onPointer = (event: MouseEvent | TouchEvent) => {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setOpen(false)
      toggle.current?.focus()
    }
    document.addEventListener('mousedown', onPointer)
    document.addEventListener('touchstart', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onPointer)
      document.removeEventListener('touchstart', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div className="account" ref={wrapper}>
      <button
        ref={toggle}
        type="button"
        className={`account-toggle${open ? ' is-open' : ''}`}
        aria-expanded={open}
        aria-controls="account-menu"
        onClick={() => setOpen((value) => !value)}
        title="Account, settings and sign out"
      >
        <span className="avatar" aria-hidden="true">
          {initials(me.user.name)}
        </span>
        <span className="account-caret" aria-hidden="true">
          {open ? '▴' : '▾'}
        </span>
        <span className="sr-only">Account menu</span>
      </button>

      {open ? (
        <div className="account-menu" id="account-menu" role="group" aria-label="Account">
          <div className="account-ident">
            <span className="avatar" aria-hidden="true">
              {initials(me.user.name)}
            </span>
            <span className="account-who">
              <b>{me.user.name}</b>
              <span>{me.user.email}</span>
            </span>
          </div>

          <dl className="account-facts">
            <div>
              <dt>Workspace</dt>
              <dd>{me.workspace.name}</dd>
            </div>
            <div>
              <dt>Role</dt>
              <dd className="account-role">{me.user.role}</dd>
            </div>
            <div>
              <dt>Wallet</dt>
              <dd>
                <State tone={walletOn ? 'pos' : 'wait'}>
                  {walletOn ? 'Connected' : 'Not connected'}
                </State>
              </dd>
            </div>
          </dl>

          <Link className="account-link" to="/integrations">
            Wallet &amp; integrations
          </Link>
          <Link className="account-link" to="/settings">
            Settings
          </Link>
          <button type="button" className="account-signout" onClick={onSignOut}>
            Sign out
          </button>
        </div>
      ) : null}
    </div>
  )
}

/**
 * The fleet, in the rail. An agent is a running instance, so the rail lists the
 * running things — avatar, name, state — under the Agents tab instead of making
 * a person go back to a directory to switch. The disclosure remembers whether a
 * person closed it; inside the Agents section it is open by default.
 */
function InstanceRail({ me }: { me: Me }) {
  const location = useLocation()
  const { tick } = useFreshness()
  const [agents, setAgents] = useState<Agent[] | null>(null)
  const [pref, setPref] = useState<'auto' | 'open' | 'closed'>(() => {
    const saved = window.localStorage.getItem(RAIL_KEY)
    return saved === '1' ? 'open' : saved === '0' ? 'closed' : 'auto'
  })
  const inAgents = location.pathname.startsWith('/agents')
  const open = pref === 'open' || (pref === 'auto' && inAgents)

  useEffect(() => {
    let live = true
    api
      .agents()
      .then((list) => {
        if (live) setAgents(list)
      })
      .catch(() => {
        // the rail is a convenience; the Agents screen owns the failure state
      })
    return () => {
      live = false
    }
  }, [tick, me.workspace.id])

  const toggle = () => {
    const next = open ? 'closed' : 'open'
    setPref(next)
    window.localStorage.setItem(RAIL_KEY, next === 'open' ? '1' : '0')
  }

  return (
    <div className="instances">
      <button
        type="button"
        className={`instances-head${open ? ' is-open' : ''}`}
        aria-expanded={open}
        aria-controls="instance-list"
        onClick={toggle}
      >
        <span className="instances-title">Instances</span>
        <span className="instances-meta" aria-hidden="true">
          <span className="instances-count mono">{agents ? agents.length : '—'}</span>
          <span className="instances-caret">{open ? '▾' : '▸'}</span>
        </span>
        <span className="sr-only">{open ? 'Collapse agent list' : 'Expand agent list'}</span>
      </button>

      {open ? (
        <ul className="instance-list" id="instance-list">
          {(agents ?? []).map((agent) => {
            const state = agentState(agent)
            return (
              <li key={agent.id}>
                <NavLink
                  to={`/agents/${agent.id}`}
                  className={({ isActive }) => `instance${isActive ? ' is-active' : ''}`}
                  title={`${agent.name} · ${state.label}`}
                >
                  <AgentAvatar agent={agent} size={24} />
                  <span className="instance-name">{agent.name}</span>
                </NavLink>
              </li>
            )
          })}
          {agents && agents.length === 0 ? (
            <li className="instance-empty">No agents yet</li>
          ) : null}
          {!agents ? (
            <li className="instance-empty" aria-hidden="true">
              Loading…
            </li>
          ) : null}
        </ul>
      ) : null}
    </div>
  )
}

function ShellInner({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const walletOn = me.wallet.status === 'connected'
  const period = new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric' }).format(new Date())
  const location = useLocation()

  useEffect(() => {
    document.title = titleFor(location.pathname)
  }, [location.pathname])

  return (
    <div className="shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <aside className="sidebar">
        <div className="brand-row">
          <Wordmark />
        </div>

        <nav className="nav" aria-label="Sections">
          <div className="nav-group">
            <p className="nav-label">{CONTROL.label}</p>
            {CONTROL.sections.map((section) => (
              <div className="nav-slot" key={section.to}>
                <NavLink
                  to={section.to}
                  end={section.end}
                  className={({ isActive }) => `nav-item${isActive ? ' is-active' : ''}`}
                >
                  {section.label}
                </NavLink>
                {section.to === '/agents' ? <InstanceRail me={me} /> : null}
              </div>
            ))}
          </div>
        </nav>

        <div className="sidebar-foot">
          <nav className="nav-setup" aria-label="Setup">
            <p className="nav-label">{SETUP.label}</p>
            {SETUP.sections.map((section) => (
              <NavLink
                key={section.to}
                to={section.to}
                className={({ isActive }) => `nav-item${isActive ? ' is-active' : ''}`}
              >
                {section.label}
              </NavLink>
            ))}
          </nav>

          <div className="side-workspace">
            <span className="side-workspace-label">Workspace</span>
            <span className="side-workspace-name">{me.workspace.name}</span>
          </div>

          <div className={`wallet-chip${walletOn ? ' is-on' : ''}`}>
            <span className="dot" aria-hidden="true" />
            <span>{walletOn ? 'Wallet connected' : 'Wallet not connected'}</span>
          </div>
          <button type="button" className="signout" onClick={onSignOut}>
            Sign out
          </button>
        </div>
      </aside>

      <div className="frame">
        <header className="topbar">
          <span className="crumb mono">{period}</span>
          <span className="topbar-brand">
            <Wordmark />
          </span>
          {/* the phone bar carries the working sections; Setup lives in the
              account menu, where sign-out is */}
          <nav className="topnav" aria-label="Sections">
            {CONTROL.sections.map((section) => (
              <NavLink
                key={section.to}
                to={section.to}
                end={section.end}
                className={({ isActive }) => `topnav-item${isActive ? ' is-active' : ''}`}
              >
                {section.label}
              </NavLink>
            ))}
          </nav>
          <div className="topbar-right">
            <span className="env">Sandbox · USD</span>
            <AccountMenu me={me} onSignOut={onSignOut} />
            <span className="user-chip">
              <span className="avatar" aria-hidden="true">
                {initials(me.user.name)}
              </span>
              <span className="user-meta">
                <b>{me.user.name}</b>
                <span>{me.user.role}</span>
              </span>
            </span>
          </div>
        </header>

        <main className="sheet" id="main-content" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  )
}

export function Shell({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  return (
    <FreshnessProvider>
      <ShellInner me={me} onSignOut={onSignOut} />
    </FreshnessProvider>
  )
}
