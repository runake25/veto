import { useEffect } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { initials, type Me } from '../api'
import { Wordmark } from '../components/Logo'

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
            <span className="env mono">sandbox · usd</span>
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
