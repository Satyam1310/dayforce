import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { renderApp } from '../test/renderApp'

describe('Audit log', () => {
  it('lists audit events newest first', async () => {
    renderApp('/audit')
    const table = await screen.findByRole('table')
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.length).toBeGreaterThan(5)
  })

  it('filters by entity id from the URL', async () => {
    renderApp('/audit?entity_id=66666666-6666-4666-8666-000000000007')
    const table = await screen.findByRole('table')
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2) // proposal created + decision approved
    expect(table).toHaveTextContent('Decision approved')
    expect(table).toHaveTextContent('mgr-0001')
  })
})
