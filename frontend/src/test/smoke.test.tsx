// Tiny smoke test: proves Vitest + React Testing Library + jest-dom are wired up.
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'

function Counter() {
  const [count, setCount] = useState(0)
  return <button onClick={() => setCount((c) => c + 1)}>Count is {count}</button>
}

describe('test setup', () => {
  it('renders a component and handles a click', async () => {
    render(<Counter />)
    await userEvent.click(screen.getByRole('button'))
    expect(screen.getByRole('button')).toHaveTextContent('Count is 1')
  })
})
