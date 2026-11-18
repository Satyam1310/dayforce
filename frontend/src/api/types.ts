import type {
  AuditEvent,
  Decision,
  DecisionRequest,
  DetectedException,
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
 *  src/api/http.ts (real FastAPI) and src/mocks/mockApi.ts (in-browser). */
export interface Api {
  listExceptions(filters: ExceptionFilters): Promise<DetectedException[]>
  getExceptionDetail(id: string): Promise<ExceptionDetail>
  proposeFix(exceptionId: string): Promise<Proposal>
  decide(proposalId: string, body: DecisionRequest): Promise<Decision>
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
