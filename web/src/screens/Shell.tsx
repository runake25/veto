import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { initials, type Me } from '../api'
import { FreshnessProvider } from '../components/freshness'
import { Wordmark } from '../components/Logo'
import { State } from '../components/state'

const TITLES: Record<string, string> = {
  '/': 'Overview · Veto',
  '/inbox': 'Inbox · Veto',
  '/agents': 'Agents · Veto',
  '/analytics': 'Analytics · Veto',
  '/integrations': 'Integrations · Veto',
}

function titleFor(pathname: string): string {
  if (pathname.startsWith('/inbox/')) return TITLES['/inbox'] ?? 'Inbox · Veto'
  if (pathname.startsWith('/agents/')) return TITLES['/agents'] ?? 'Agents · Veto'
  return TITLES[pathname] ?? 'Veto — spend control'
}

const SECTIONS = [
  { to: '/', label: 'Overview', end: true },
  { to: '/inbox', label: 'Inbox' },
  { to: '/agents', label: 'Agents' },
  { to: '/analytics', label: 'Analytics' },
  { to: '/integrations', label: 'Integrations' },
]

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
        title="Account, wallet and sign out"
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
          <button type="button" className="account-signout" onClick={onSignOut}>
            Sign out
          </button>
        </div>
      ) : null}
    </div>
  )
}

export function Shell({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
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
          <span className="brand-workspace">{me.workspace.name}</span>
        </div>

        <nav className="nav" aria-label="Sections">
          <div className="nav-group">
            <p className="nav-label">Control</p>
            {SECTIONS.map((section) => (
              <NavLink
                key={section.to}
                to={section.to}
                end={section.end}
                className={({ isActive }) => `nav-item${isActive ? ' is-active' : ''}`}
              >
                {section.label}
              </NavLink>
            ))}
          </div>
        </nav>

        <div className="sidebar-foot">
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
          <nav className="topnav" aria-label="Sections">
            {SECTIONS.map((section) => (
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
          <FreshnessProvider>
            <Outlet />
          </FreshnessProvider>
        </main>
      </div>
    </div>
  )
}
