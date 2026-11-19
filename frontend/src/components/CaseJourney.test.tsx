import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { createSeed } from '../mocks/data'
import { buildJourney } from '../lib/journey'
import { CaseJourney } from './CaseJourney'

const seed = createSeed()
const exc = (n: number) => seed.exceptions.find((e) => e.id.endsWith(`00${n}`))!
const prop = (n: number) => seed.proposals.find((p) => p.exception_id === exc(n).id) ?? null
const states = (n: number) =>
  buildJourney(
    exc(n),
    prop(n),
    seed.decisions.find((d) => d.proposal_id === prop(n)?.id),
  ).map((s) => s.state)

describe('CaseJourney', () => {
  it('AI fix waiting for the manager', () => {
    expect(states(1)).toEqual(['done', 'done', 'done', 'done', 'current'])
  })

  it('rule-based fallback is marked as a caveat', () => {
    expect(states(2)[2]).toBe('warn')
  })

  it('escalated case stops at the proposal step', () => {
    expect(states(5)).toEqual(['done', 'warn', 'blocked', 'idle', 'current'])
  })

  it('no proposal yet', () => {
    expect(states(6)).toEqual(['done', 'idle', 'current', 'idle', 'idle'])
  })

  it('says who rejected the fix', () => {
    render(<CaseJourney exception={exc(8)} proposal={prop(8)} decision={seed.decisions[1]} />)
    expect(screen.getByText(/rejected by mgr-0001/i)).toBeInTheDocument()
  })
})
