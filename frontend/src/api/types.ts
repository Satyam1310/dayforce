import type {
  AuditEvent,
  CaseListItem,
  DecisionRequest,
  ExceptionDetail,
  ExceptionStatus,
  ExceptionType,
  Proposal,
} from '../types/api'

export interface ExceptionFilters {
  status?: ExceptionStatus
  type?: ExceptionType
}

/** Everything the UI needs from the backend. Implemented twice:
 *  src/api/http.ts (real FastAPI, backend/API.md) and src/mocks/mockApi.ts (in-browser).
 *  A case id is the exception id. */
export interface Api {
  /** Cases with their latest proposal (null when none yet). */
  listCases(filters: ExceptionFilters): Promise<CaseListItem[]>
  getExceptionDetail(id: string): Promise<ExceptionDetail>
  proposeFix(exceptionId: string): Promise<Proposal>
  /** Approve or reject the case's pending proposal. Returns the updated case. */
  decide(caseId: string, body: DecisionRequest): Promise<ExceptionDetail>
  listAudit(entityId?: string): Promise<AuditEvent[]>
}

/** Error thrown by both implementations, mirrors the backend error shape. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId: string | null

  constructor(status: number, code: string, message: string, requestId: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.requestId = requestId
  }
}
