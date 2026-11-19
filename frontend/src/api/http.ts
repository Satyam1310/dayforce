// Real API client: plain fetch against the FastAPI backend (endpoints: backend/API.md).
import { API_BASE_URL } from '../config'
import type { ApiErrorBody, CaseListItem } from '../types/api'
import { ApiError, type Api } from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    })
  } catch {
    throw new ApiError(0, 'NETWORK_ERROR', 'Could not reach the server. Is the backend running?')
  }

  if (!res.ok) {
    // Backend error shape: { "error": { "code", "message", "request_id" } }
    let body: Partial<ApiErrorBody> = {}
    try {
      body = (await res.json()) as Partial<ApiErrorBody>
    } catch {
      // not JSON, keep the defaults below
    }
    throw new ApiError(
      res.status,
      body.error?.code ?? `HTTP_${res.status}`,
      body.error?.message ?? res.statusText ?? 'Request failed',
      body.error?.request_id ?? null,
    )
  }
  return (await res.json()) as T
}

function query(params: Record<string, string | undefined>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, v)
  const s = q.toString()
  return s ? `?${s}` : ''
}

const casePath = (id: string) => `/cases/${encodeURIComponent(id)}`

export const httpApi: Api = {
  listCases: (f) => request<CaseListItem[]>(`/cases${query({ status: f.status, type: f.type })}`),
  getExceptionDetail: (id) => request(casePath(id)),
  proposeFix: (caseId) => request(`${casePath(caseId)}/propose`, { method: 'POST' }),
  // Two endpoints: /approve and /reject. The body carries who decided and why.
  decide: (caseId, { decision, manager_id, comment }) =>
    request(`${casePath(caseId)}/${decision === 'APPROVE' ? 'approve' : 'reject'}`, {
      method: 'POST',
      body: JSON.stringify({ manager_id, comment }),
    }),
  listAudit: (entityId) => request(`/audit${query({ entity_id: entityId })}`),
}
