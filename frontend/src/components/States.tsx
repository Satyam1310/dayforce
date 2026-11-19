// Shared loading / error / empty states, used by every page.
import type { ReactNode } from 'react'
import { ApiError } from '../api'

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="skeleton" role="status">
      <span className="visually-hidden">{label}</span>
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="skeleton-row" aria-hidden="true">
          <span className="skeleton-circle" />
          <span className="skeleton-line" style={{ width: `${60 - i * 8}%` }} />
          <span className="skeleton-line short" />
        </div>
      ))}
    </div>
  )
}

export function ErrorState({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  const requestId = error instanceof ApiError ? error.requestId : null
  return (
    <div className="state state-error" role="alert">
      <strong>Something went wrong.</strong> {error.message}
      {requestId && <div className="muted small">Request ID: {requestId}</div>}
      {onRetry && (
        <div>
          <button type="button" className="btn" onClick={onRetry}>
            Try again
          </button>
        </div>
      )}
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="state state-empty">{children}</div>
}
