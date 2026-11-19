import { NavLink, Outlet } from 'react-router-dom'
import { CURRENT_MANAGER_ID, USE_MOCK } from '../config'

export function Layout() {
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            NW
          </span>
          <span className="brand-text">
            <span className="brand-name">Northwind Retail</span>
            <span className="brand-sub">Timecard exceptions</span>
          </span>
        </div>
        <nav className="nav" aria-label="Main">
          <NavLink to="/exceptions">Review queue</NavLink>
          <NavLink to="/audit">Audit log</NavLink>
        </nav>
        <div className="topbar-right">
          {USE_MOCK && (
            <span className="mock-badge" title="Running on in-browser sample data, not the backend">
              Mock data
            </span>
          )}
          <span className="signed-in">Signed in as {CURRENT_MANAGER_ID}</span>
        </div>
      </header>
      <main className="content">
        <Outlet />
      </main>
    </div>
  )
}
