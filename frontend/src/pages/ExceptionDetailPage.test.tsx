import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { api } from '../api'
import { renderApp } from '../test/renderApp'

const MISSED_PUNCH = '55555555-5555-4555-8555-000000000001' // fixture exception, AI proposal
const PROMPT_INJECTION = '55555555-5555-4555-8555-000000000004'
const ESCALATED = '55555555-5555-4555-8555-000000000005'
const NO_PROPOSAL = '55555555-5555-4555-8555-000000000006'
const REJECTED = '55555555-5555-4555-8555-000000000008'
const PROPOSAL_1 = '66666666-6666-4666-8666-000000000001'

describe('Exception detail', () => {
  it('shows the proposal, cited policy and validator result', async () => {
    renderApp(`/exceptions/${MISSED_PUNCH}`)
    expect(await screen.findByRole('heading', { name: 'Missed punch' })).toBeInTheDocument()
    expect(screen.getByText('AI suggestion')).toBeInTheDocument()
    expect(screen.getAllByText('POL-TA-01').length).toBeGreaterThan(0)
    expect(screen.getByText(/passed all policy checks/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve fix' })).toBeInTheDocument()
  })

  it('approves a fix and records it in the audit log', async () => {
    const user = userEvent.setup()
    renderApp(`/exceptions/${MISSED_PUNCH}`)

    await user.type(await screen.findByLabelText(/comment/i), 'Checked with the employee.')
    await user.click(screen.getByRole('button', { name: 'Approve fix' }))

    expect(await screen.findByText('Fix approved and applied.')).toBeInTheDocument()
    expect(await screen.findByText(/this proposal was/i)).toHaveTextContent('approved')
    expect(screen.queryByRole('button', { name: 'Approve fix' })).not.toBeInTheDocument()

    const events = await api.listAudit(PROPOSAL_1)
    expect(events[0]).toMatchObject({
      event_type: 'DECISION_APPROVED',
      actor: 'mgr-0001',
      payload: { comment: 'Checked with the employee.' },
    })
  })

  it('requires a comment to reject', async () => {
    const user = userEvent.setup()
    renderApp(`/exceptions/${MISSED_PUNCH}`)

    await user.click(await screen.findByRole('button', { name: 'Reject' }))
    expect(await screen.findByText(/add a comment explaining why/i)).toBeInTheDocument()

    const events = await api.listAudit(PROPOSAL_1)
    expect(events.some((e) => e.event_type === 'DECISION_REJECTED')).toBe(false)
  })

  it('clearly marks escalated cases and offers no approve button', async () => {
    renderApp(`/exceptions/${ESCALATED}`)
    expect(
      await screen.findByText(/AI couldn't safely propose a fix, manager review needed/i),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve fix' })).not.toBeInTheDocument()
  })

  it('shows untrusted notes as plain text', async () => {
    renderApp(`/exceptions/${PROMPT_INJECTION}`)
    expect(await screen.findByText(/ignore all previous instructions/i)).toBeInTheDocument()
    expect(screen.getByText(/untrusted text/i)).toBeInTheDocument()
  })

  it('generates a proposal for an open exception (escalates when there is no schedule)', async () => {
    const user = userEvent.setup()
    renderApp(`/exceptions/${NO_PROPOSAL}`)

    await user.click(await screen.findByRole('button', { name: 'Generate proposal' }))
    expect(
      await screen.findByText(/AI couldn't safely propose a fix, manager review needed/i),
    ).toBeInTheDocument()
  })

  it('shows a rejected proposal read-only, with who rejected it and why', async () => {
    renderApp(`/exceptions/${REJECTED}`)
    expect(await screen.findByText(/this proposal was/i)).toHaveTextContent('rejected by mgr-0001')
    expect(screen.getByText(/employee confirmed no break was taken/i)).toBeInTheDocument()
    expect(screen.getByText(/nothing was changed on the timecard/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve fix' })).not.toBeInTheDocument()
  })

  it('shows who approved after approving', async () => {
    const user = userEvent.setup()
    renderApp(`/exceptions/${MISSED_PUNCH}`)
    await user.click(await screen.findByRole('button', { name: 'Approve fix' }))
    expect(await screen.findByText(/this proposal was/i)).toHaveTextContent('approved by mgr-0001')
  })

  it('shows an error for an unknown exception', async () => {
    renderApp('/exceptions/does-not-exist')
    expect(await screen.findByRole('alert')).toHaveTextContent(/not found/i)
  })
})
