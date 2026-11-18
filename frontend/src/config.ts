// App settings, read from Vite env vars (see frontend/.env.example).

// true  -> use the in-browser mock API (src/mocks), no backend needed
// false -> call the real FastAPI backend
export const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false'

// Base URL for the real API. In dev, vite.config.ts proxies /api to :8000.
export const API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? '/api/v1'

// There is no login in v1, so decisions are made as this demo manager.
export const CURRENT_MANAGER_ID = 'mgr-0001'

// The fictional company's timezone (PolicyConfig.company_tz).
export const COMPANY_TZ = 'Asia/Kolkata'
