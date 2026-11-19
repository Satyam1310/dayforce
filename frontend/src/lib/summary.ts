// One plain-English line per case for the list, built from the detector's evidence.
// Evidence keys are not a fixed list (docs/frontend-mock/README.md), so every branch
// falls back to a generic sentence when a key is missing.
import { formatTime, isIsoDateTime } from './format'
import { POLICY_CLAUSES } from './policy'
import type { DetectedException } from '../types/api'

const num = (v: unknown): number | null => (typeof v === 'number' ? v : null)

export function summarize(e: DetectedException): string {
  const ev = e.evidence
  switch (e.type) {
    case 'MISSED_PUNCH': {
      const inAt = isIsoDateTime(ev.clock_in) ? formatTime(ev.clock_in) : null
      if (inAt && ev.scheduled_shift_end === null) return `Clocked in at ${inAt}, no clock-out, no shift scheduled`
      return inAt ? `Clocked in at ${inAt}, no clock-out` : 'Clock-in without a clock-out'
    }
    case 'OVERTIME_RISK': {
      const projected = num(ev.projected_hours)
      const limit = num(ev.risk_threshold_hours) ?? 38
      return projected !== null
        ? `Week projected at ${projected} h, alert level is ${limit} h`
        : 'Week is heading into overtime'
    }
    case 'SCHEDULE_CONFLICT': {
      const minutes = num(ev.overlap_minutes)
      return minutes !== null ? `Two shifts overlap by ${minutes} min` : 'Two shifts overlap'
    }
    case 'POLICY_BREACH': {
      const worked = num(ev.worked_hours)
      const rest = num(ev.rest_hours)
      if (ev.rule === 'MISSING_BREAK' && worked !== null) return `${worked} h shift with no break logged`
      if (ev.rule === 'MIN_REST' && rest !== null) return `Only ${rest} h rest between shifts`
      const clause = POLICY_CLAUSES[e.policy_refs[0] ?? '']
      return clause ? `${clause.title} rule broken` : 'Company policy broken'
    }
  }
}
