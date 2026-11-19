import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { api } from '../api'
import { CURRENT_MANAGER_ID, USE_MOCK } from '../config'
import { useAsync } from '../hooks/useAsync'
import { Avatar } from './Avatar'
import { Icon } from './Icon'

function pageTitle(path: string): string {
  if (path.startsWith('/exceptions/')) return 'Case'
  if (path.startsWith('/audit')) return 'Audit log'
  return 'Review queue'
}

export function Layout() {
  const location = useLocation()
  // Live count for the sidebar badge; refreshed whenever the page changes.
  const { data } = useAsync(() => api.listCases({}), [location.pathname])
  const waiting = data?.filter((c) => c.proposal?.status === 'PENDING_REVIEW').length

  return (
    <div className="shell">
      <aside className="sidebar">
        <Link to="/exceptions" className="brand">
          <span className="brand-mark" aria-hidden="true">
            NW
          </span>
          <span className="brand-text">
            <span className="brand-name">Northwind Retail</span>
            <span className="brand-sub">Workforce management</span>
          </span>
        </Link>

        <nav className="side-nav" aria-label="Main">
          <span className="side-nav-group">Timecards</span>
          <NavLink to="/exceptions" end={false}>
            <Icon name="queue" />
            <span>Review queue</span>
            {waiting !== undefined && waiting > 0 && (
              <span className="nav-count" aria-label={`${waiting} waiting`}>
                {waiting}
              </span>
            )}
          </NavLink>
          <NavLink to="/audit">
            <Icon name="audit" />
            <span>Audit log</span>
          </NavLink>
        </nav>

        <div className="side-safety">
          <Icon name="shield" />
          <p>AI suggests fixes. Only a manager's approval changes a timecard.</p>
        </div>

        <div className="side-user">
          <Avatar id={CURRENT_MANAGER_ID} />
          <span className="side-user-text">
            <span className="side-user-name">{CURRENT_MANAGER_ID}</span>
            <span className="side-user-role">Store manager</span>
          </span>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <nav className="crumbs" aria-label="Breadcrumb">
            <span>Timecard exceptions</span>
            <Icon name="chevron" size={14} />
            <span className="crumb-current">{pageTitle(location.pathname)}</span>
          </nav>
          <div className="topbar-right">
            <span className="tz-chip" title="All times are shown in the company timezone">
              <Icon name="clock" size={14} /> IST, Asia/Kolkata
            </span>
            <span className={`env-chip ${USE_MOCK ? 'is-mock' : 'is-live'}`}>
              <Icon name="db" size={14} /> {USE_MOCK ? 'Mock data' : 'Live data'}
            </span>
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
