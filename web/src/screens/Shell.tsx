import { NavLink, Outlet } from 'react-router-dom'
import { initials, type Me } from '../api'
import { Wordmark } from '../components/Logo'

const LATER = [
  { label: 'Inbox' },
  { label: 'Agents' },
  { label: 'Analytics' },
  { label: 'Integrations' },
]

export function Shell({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const walletOn = me.wallet.status === 'connected'
  const period = new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric' }).format(new Date())

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand-row">
          <Wordmark />
          <span className="brand-workspace">{me.workspace.name}</span>
        </div>

        <nav className="nav" aria-label="Sections">
          <div className="nav-group">
            <p className="nav-label">Control</p>
            <NavLink
              to="/"
              end
              className={({ isActive }) => `nav-item${isActive ? ' is-active' : ''}`}
            >
              Overview
            </NavLink>
            {LATER.map((item) => (
              <span className="nav-item is-soon" key={item.label} aria-disabled="true">
                {item.label}
                <span className="soon mono">soon</span>
              </span>
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

        <main className="sheet">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
