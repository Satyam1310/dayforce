import { NavLink, Outlet } from 'react-router-dom'
import { CURRENT_MANAGER_ID, USE_MOCK } from '../config'

export function Layout() {
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">NW</span>
          <span>
            Northwind Retail <span className="muted">· Timecard exceptions</span>
          </span>
        </div>
        <nav className="nav">
          <NavLink to="/exceptions">Exceptions</NavLink>
          <NavLink to="/audit">Audit log</NavLink>
        </nav>
        <div className="topbar-right">
          {USE_MOCK && <span className="badge mock-badge">Mock data</span>}
          <span className="muted small">Signed in as {CURRENT_MANAGER_ID}</span>
        </div>
      </header>
      <main className="content">
        <Outlet />
      </main>
    </div>
  )
}
