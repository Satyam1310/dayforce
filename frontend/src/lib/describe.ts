// Turns raw values (UUIDs, field codes) into words a manager understands, using the
// case's own timecard to look ids up. Unknown values fall back to formatValue.
import { formatTime, formatValue, humanize, shortId } from './format'
import type { Timecard } from '../types/api'

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

const FIELD_LABELS: Record<string, string> = {
  'punch.IN': 'Clock-in punch',
  'punch.OUT': 'Clock-out punch',
  'punch.BREAK_START': 'Break start punch',
  'punch.BREAK_END': 'Break end punch',
  'punch.timestamp': 'Punch time',
  break_minutes: 'Break minutes',
  'shift.employee_id': 'Shift assigned to',
}

const RULE_LABELS: Record<string, string> = {
  MISSING_BREAK: 'Missing meal break',
  MIN_REST: 'Minimum rest',
  DAILY_MAX: 'Day over 12 hours',
}

/** "punch.OUT" -> "Clock-out punch"; unknown codes are humanized. */
export function fieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? humanize(field)
}

export function describeValue(value: unknown, timecard?: Timecard): string {
  if (typeof value === 'string' && UUID.test(value)) {
    if (timecard) {
      const shift = timecard.shifts.find((s) => s.id === value)
      if (shift) return `Shift ${formatTime(shift.start)}–${formatTime(shift.end)}, ${shift.location_id}`
      const punch = timecard.punches.find((p) => p.id === value)
      if (punch) return `${humanize(punch.type)} punch at ${formatTime(punch.timestamp)}`
      if (value === timecard.employee_id) return `Employee ${shortId(value)}`
      if (value === timecard.id) return 'This timecard'
    }
    return value.startsWith('22222222') ? `Employee ${shortId(value)}` : `Record ${shortId(value)}`
  }
  if (value === 'UNASSIGNED') return 'Unassigned (open for cover)'
  if (typeof value === 'string' && value in RULE_LABELS) return RULE_LABELS[value]
  return formatValue(value)
}
