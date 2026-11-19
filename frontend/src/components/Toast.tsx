// A short confirmation that slides in after an action and disappears by itself.
import { useEffect, type ReactNode } from 'react'
import { Icon } from './Icon'

interface Props {
  message: string
  tone?: 'success' | 'neutral'
  action?: ReactNode
  onClose: () => void
  /** milliseconds before it closes itself */
  duration?: number
}

export function Toast({ message, tone = 'success', action, onClose, duration = 6000 }: Props) {
  useEffect(() => {
    const t = setTimeout(onClose, duration)
    return () => clearTimeout(t)
  }, [onClose, duration])

  return (
    <div className={`toast toast-${tone}`} role="status">
      <Icon name={tone === 'success' ? 'shield' : 'clock'} />
      <span className="toast-message">{message}</span>
      {action}
      <button type="button" className="toast-close" onClick={onClose} aria-label="Dismiss">
        ×
      </button>
    </div>
  )
}
