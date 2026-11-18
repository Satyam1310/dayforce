import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ValidationPanel } from './ValidationPanel'

describe('ValidationPanel', () => {
  it('shows a pass message', () => {
    render(<ValidationPanel validation={{ passed: true, violations: [] }} />)
    expect(screen.getByText(/passed all policy checks/i)).toBeInTheDocument()
  })

  it('lists every violation when validation fails', () => {
    // same data as backend/app/contracts/fixtures/validation_result_failed.json
    render(
      <ValidationPanel
        validation={{
          passed: false,
          violations: [
            {
              code: 'SHIFT_OVER_MAX_HOURS',
              message: 'Proposed clock-out makes the shift 14.5 hours, above the 12 hour limit.',
              clause_id: 'POL-TA-01',
            },
            {
              code: 'UNRELATED_CHANGE',
              message: 'Fix modifies a field that is not part of the detected exception.',
              clause_id: null,
            },
          ],
        }}
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Failed validation (2)')
    expect(screen.getByText('SHIFT_OVER_MAX_HOURS')).toBeInTheDocument()
    expect(screen.getByText('UNRELATED_CHANGE')).toBeInTheDocument()
  })

  it('says when there was nothing to validate', () => {
    render(<ValidationPanel validation={null} />)
    expect(screen.getByText(/not validated/i)).toBeInTheDocument()
  })
})
