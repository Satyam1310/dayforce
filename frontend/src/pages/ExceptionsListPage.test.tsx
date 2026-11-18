import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { renderApp } from '../test/renderApp'

describe('Exceptions list', () => {
  it('lists all exceptions from the mock API', async () => {
    renderApp('/exceptions')
    const table = await screen.findByRole('table')
    // 7 seed exceptions + 1 header row
    expect(within(table).getAllByRole('row')).toHaveLength(8)
    expect(screen.getByText(/awaiting review/i)).toBeInTheDocument()
  })

  it('filters by exception type', async () => {
    const user = userEvent.setup()
    renderApp('/exceptions')
    await screen.findByRole('table')

    await user.selectOptions(screen.getByLabelText('Type'), 'SCHEDULE_CONFLICT')

    const table = await screen.findByRole('table')
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(1)
    expect(rows[0]).toHaveTextContent('Schedule conflict')
  })

  it('marks escalated exceptions', async () => {
    renderApp('/exceptions?status=ESCALATED')
    expect(await screen.findByText('Needs manager')).toBeInTheDocument()
  })

  it('shows an empty state when nothing matches', async () => {
    renderApp('/exceptions?type=OVERTIME_RISK&status=RESOLVED')
    expect(await screen.findByText(/no exceptions match/i)).toBeInTheDocument()
  })
})
