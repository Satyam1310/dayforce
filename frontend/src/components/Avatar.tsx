// Initials avatar for an employee or manager. The contract has no names (only ids), so we
// show a short id-based label with a stable colour per person.
const HUES = [212, 168, 262, 28, 340, 190, 128, 300]

function hueFor(id: string): number {
  let h = 0
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return HUES[h % HUES.length]
}

function avatarLabel(id: string): string {
  const digits = id.replace(/\D/g, '')
  if (id.startsWith('mgr')) return 'M' + (digits.slice(-1) || '')
  return 'E' + (digits.slice(-2) || id.slice(0, 2).toUpperCase())
}

export function Avatar({ id, size = 'm' }: { id: string; size?: 's' | 'm' | 'l' }) {
  const hue = hueFor(id)
  return (
    <span
      className={`avatar avatar-${size}`}
      style={{ background: `hsl(${hue} 55% 92%)`, color: `hsl(${hue} 55% 28%)` }}
      aria-hidden="true"
    >
      {avatarLabel(id)}
    </span>
  )
}
