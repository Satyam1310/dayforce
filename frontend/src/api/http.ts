// Real API client: plain fetch against the FastAPI backend (CLAUDE.md section 9).
import { API_BASE_URL } from '../config'
import type { ApiErrorBody } from '../types/api'
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

export const httpApi: Api = {
  listExceptions: (f) => request(`/exceptions${query({ status: f.status, type: f.type })}`),
  getExceptionDetail: (id) => request(`/exceptions/${encodeURIComponent(id)}`),
  proposeFix: (exceptionId) =>
    request(`/exceptions/${encodeURIComponent(exceptionId)}/propose`, { method: 'POST' }),
  decide: (proposalId, body) =>
    request(`/proposals/${encodeURIComponent(proposalId)}/decision`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  listAudit: (entityId) => request(`/audit${query({ entity_id: entityId })}`),
}
