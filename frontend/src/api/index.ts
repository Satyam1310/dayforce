// The one place the app gets its API from. Pages import `api` from here and
// never care whether it's the mock or the real backend.
import { USE_MOCK } from '../config'
import { mockApi } from '../mocks/mockApi'
import { httpApi } from './http'
import type { Api } from './types'

export const api: Api = USE_MOCK ? mockApi : httpApi
export { ApiError } from './types'
export type { ExceptionFilters } from './types'
