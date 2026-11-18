// Small display helpers. All times are shown in the company timezone
// (Asia/Kolkata), whatever timezone the manager's browser is in.
import { COMPANY_TZ } from '../config'

const timeFmt = new Intl.DateTimeFormat('en-GB', {
  timeZone: COMPANY_TZ,
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

const dateTimeFmt = new Intl.DateTimeFormat('en-GB', {
  timeZone: COMPANY_TZ,
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

const dateFmt = new Intl.DateTimeFormat('en-GB', {
  timeZone: COMPANY_TZ,
  weekday: 'short',
  day: '2-digit',
  month: 'short',
  year: 'numeric',
})

/** "2026-03-10T03:32:00+00:00" -> "09:02" (IST) */
export function formatTime(iso: string): string {
  return timeFmt.format(new Date(iso))
}

/** "2026-03-10T03:32:00+00:00" -> "10 Mar 2026, 09:02 IST" */
export function formatDateTime(iso: string): string {
  return `${dateTimeFmt.format(new Date(iso))} IST`
}

/** "2026-03-10" -> "Tue, 10 Mar 2026" (a calendar day in the company timezone) */
export function formatDate(day: string): string {
  return dateFmt.format(new Date(`${day}T12:00:00+05:30`))
}

/** True for strings that look like an ISO date-time, e.g. "2026-03-10T12:30:00+00:00" */
export function isIsoDateTime(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value) &&
    !Number.isNaN(Date.parse(value))
  )
}

/** Formats any evidence / field value for display. */
export function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (isIsoDateTime(value)) return formatDateTime(value)
  if (typeof value === 'number') return String(Math.round(value * 100) / 100)
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

/** "MISSED_PUNCH" -> "Missed punch", "scheduled_shift_end" -> "Scheduled shift end" */
export function humanize(code: string): string {
  const words = code.replace(/[_.]+/g, ' ').trim().toLowerCase()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/** Short, readable label for a UUID: "22222222-...-000000000001" -> "…0001" */
export function shortId(id: string): string {
  return `…${id.slice(-4)}`
}

/** 0.9 -> "90%" */
export function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`
}
