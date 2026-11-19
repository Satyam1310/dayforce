// The real API client, with fetch faked: right URLs, right bodies, error shape handled.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { httpApi } from './http'
import { ApiError } from './types'

function fakeFetch(status: number, body: unknown) {
  const fn = vi.fn(async () => new Response(JSON.stringify(body), { status }))
  vi.stubGlobal('fetch', fn)
  return fn
}

const call = (fn: ReturnType<typeof fakeFetch>, i = 0) => {
  const [url, init] = fn.mock.calls[i] as unknown as [string, RequestInit | undefined]
  return { url, init }
}

afterEach(() => vi.unstubAllGlobals())

describe('httpApi', () => {
  it('lists cases with filters', async () => {
    const items = [{ exception: { id: 'c1', type: 'MISSED_PUNCH' }, proposal: null }]
    const fetch = fakeFetch(200, items)
    const result = await httpApi.listCases({ status: 'OPEN', type: 'MISSED_PUNCH' })
    expect(call(fetch).url).toBe('/api/cases?status=OPEN&type=MISSED_PUNCH')
    expect(result).toEqual(items)
  })

  it('gets one case and proposes', async () => {
    const fetch = fakeFetch(200, {})
    await httpApi.getExceptionDetail('c 1')
    await httpApi.proposeFix('c1')
    expect(call(fetch, 0).url).toBe('/api/cases/c%201')
    expect(call(fetch, 1).url).toBe('/api/cases/c1/propose')
    expect(call(fetch, 1).init?.method).toBe('POST')
  })

  it('approves and rejects through their own endpoints', async () => {
    const fetch = fakeFetch(200, {})
    await httpApi.decide('c1', { decision: 'APPROVE', manager_id: 'mgr-0001', comment: null })
    await httpApi.decide('c1', { decision: 'REJECT', manager_id: 'mgr-0001', comment: 'No.' })
    expect(call(fetch, 0).url).toBe('/api/cases/c1/approve')
    expect(JSON.parse(call(fetch, 0).init?.body as string)).toEqual({
      manager_id: 'mgr-0001',
      comment: null,
    })
    expect(call(fetch, 1).url).toBe('/api/cases/c1/reject')
    expect(JSON.parse(call(fetch, 1).init?.body as string)).toEqual({
      manager_id: 'mgr-0001',
      comment: 'No.',
    })
  })

  it('turns the backend error shape into an ApiError', async () => {
    fakeFetch(409, { error: { code: 'CASE_ESCALATED', message: 'Escalated.', request_id: 'r1' } })
    const err = await httpApi
      .decide('c1', { decision: 'APPROVE', manager_id: 'm', comment: null })
      .catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 409, code: 'CASE_ESCALATED', requestId: 'r1' })
  })

  it('reports a network failure clearly', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Promise.reject(new TypeError('offline'))),
    )
    await expect(httpApi.listAudit()).rejects.toMatchObject({ code: 'NETWORK_ERROR' })
  })
})
