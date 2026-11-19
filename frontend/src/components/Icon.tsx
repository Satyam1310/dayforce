// Small inline stroke icons (no icon library). Decorative unless a label is given.
const PATHS = {
  queue: 'M4 6h16M4 12h16M4 18h10',
  audit: 'M9 4h6l1 2h3v14H5V6h3l1-2zM9 11h6M9 15h4',
  chevron: 'M9 6l6 6-6 6',
  back: 'M15 6l-6 6 6 6',
  shield: 'M12 3l7 3v6c0 4-3 7.5-7 9-4-1.5-7-5-7-9V6l7-3zM9 12l2 2 4-4',
  alert: 'M12 4l9 16H3l9-16zM12 10v4M12 17h.01',
  clock: 'M12 21a9 9 0 100-18 9 9 0 000 18zM12 7v5l3 2',
  spark: 'M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6',
  user: 'M12 12a4 4 0 100-8 4 4 0 000 8zM4 21c1.5-4 4.5-6 8-6s6.5 2 8 6',
  db: 'M12 4c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3zM4 7v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
} as const

export type IconName = keyof typeof PATHS

export function Icon({
  name,
  label,
  size = 18,
}: {
  name: IconName
  label?: string
  size?: number
}) {
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      <path d={PATHS[name]} />
    </svg>
  )
}
