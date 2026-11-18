// Clause titles and text, so the detail page can show the full clause next to
// the AI's quote. Copied from data/policies/time_attendance_policy.md, which
// is the source of truth (owned by Harsh). If that file changes, update this.
// Later this can come from GET /api/v1/policies/search instead.

export interface PolicyClause {
  id: string
  title: string
  text: string
}

export const POLICY_CLAUSES: Record<string, PolicyClause> = {
  'POL-TA-01': {
    id: 'POL-TA-01',
    title: 'Missed punch',
    text: 'If an employee has a clock-in but no clock-out, the clock-out can be corrected to the scheduled shift end, as long as the resulting shift stays 12 hours or less. If there is no schedule for that day, the case is escalated to the manager.',
  },
  'POL-TA-02': {
    id: 'POL-TA-02',
    title: 'Meal break',
    text: 'Shifts longer than 6 hours need an unpaid break of at least 30 minutes. If no break is logged, deduct 30 minutes from the shift, unless the employee states that no break was taken, in which case the manager decides.',
  },
  'POL-TA-03': {
    id: 'POL-TA-03',
    title: 'Daily overtime and daily maximum',
    text: 'Hours beyond 8 per day are overtime. A day over 12 hours is a policy breach and needs manager review.',
  },
  'POL-TA-04': {
    id: 'POL-TA-04',
    title: 'Weekly overtime risk',
    text: 'Overtime applies beyond 40 hours per week. If worked hours plus remaining scheduled hours are projected at 38 hours or more, raise an overtime risk alert. The suggested action is to reduce or reassign upcoming shifts.',
  },
  'POL-TA-05': {
    id: 'POL-TA-05',
    title: 'Schedule conflict',
    text: 'An employee cannot have two overlapping shifts. Resolution: keep the earliest-assigned shift and propose reassigning the other shift, with manager approval.',
  },
  'POL-TA-06': {
    id: 'POL-TA-06',
    title: 'Minimum rest',
    text: 'There must be at least 10 hours between the end of one shift and the start of the next. Violating this is a policy breach.',
  },
  'POL-TA-07': {
    id: 'POL-TA-07',
    title: 'Edits and approvals',
    text: 'No timecard edit is final until a manager approves it. Every edit and decision is recorded with who, when and why.',
  },
}
