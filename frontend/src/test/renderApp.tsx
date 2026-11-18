import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { AppRoutes } from '../App'
import { resetMockDb } from '../mocks/mockApi'

/** Renders the whole app starting at `path`, like opening that URL.
 *  Resets the mock API first, so every test starts from the seed data. */
export function renderApp(path: string) {
  resetMockDb()
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>,
  )
}
