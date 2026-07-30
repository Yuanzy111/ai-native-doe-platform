import { describe, expect, it } from 'vitest'
import {
  canApproveProposal,
  canSendMessage,
  describeProposal,
  isModificationProposal,
  isProposalStale,
  runToken,
} from './agentState'
import type { AgentProposalDto, CampaignRunDto } from '../../../api/types'

function proposal(overrides: Partial<AgentProposalDto> = {}): AgentProposalDto {
  return {
    id: 'prop-1',
    threadId: 'thread-1',
    campaignRunId: 'run-1',
    kind: 'designSpacePatch',
    payload: {},
    status: 'Pending',
    baseRevisionId: 'rev-1',
    baseRunUpdatedAt: 'ts-1',
    createdAt: '2026-02-01T10:00:00Z',
    resolvedAt: null,
    error: null,
    ...overrides,
  }
}

function run(overrides: Partial<CampaignRunDto> = {}): CampaignRunDto {
  return {
    id: 'run-1',
    campaignDefinitionId: 'cd-1',
    definitionRevisionId: 'rev-1',
    status: 'Draft',
    optimizationPolicy: {
      id: 'op-1',
      backendName: 'baybe',
      batchSize: 4,
      seedPolicy: 'Fixed',
      seedValue: 42,
      strategyConfig: { kind: 'Botorch', acquisitionFunction: 'qLogEI' },
    } as CampaignRunDto['optimizationPolicy'],
    round: 0,
    budgetTotal: 10,
    budgetUsed: 0,
    createdAt: '2026-02-01T09:00:00Z',
    updatedAt: 'ts-1',
    createdBy: 'user-1',
    ...overrides,
  }
}

describe('canSendMessage', () => {
  it('rejects an empty or whitespace-only draft', () => {
    expect(canSendMessage('', false)).toBe(false)
    expect(canSendMessage('   ', false)).toBe(false)
  })

  it('accepts a non-empty draft when idle', () => {
    expect(canSendMessage('add a parameter', false)).toBe(true)
  })

  it('blocks a duplicate send while a request is in flight', () => {
    expect(canSendMessage('add a parameter', true)).toBe(false)
  })
})

describe('isModificationProposal', () => {
  it('is true only for a design-space patch', () => {
    expect(isModificationProposal(proposal({ kind: 'designSpacePatch' }))).toBe(true)
    expect(isModificationProposal(proposal({ kind: 'validateDesignSpace' }))).toBe(false)
    expect(isModificationProposal(proposal({ kind: 'generateInitialDesign' }))).toBe(false)
  })
})

describe('isProposalStale', () => {
  it('is fresh when both the revision and run token match', () => {
    expect(
      isProposalStale(
        proposal({ baseRevisionId: 'rev-1', baseRunUpdatedAt: 'ts-1' }),
        'rev-1',
        'ts-1',
      ),
    ).toBe(false)
  })

  it('is stale when the current revision has moved on', () => {
    expect(
      isProposalStale(
        proposal({ baseRevisionId: 'rev-1', baseRunUpdatedAt: 'ts-1' }),
        'rev-2',
        'ts-1',
      ),
    ).toBe(true)
  })

  it('is stale when the run token moved even though the revision matches', () => {
    // A status transition or policy swap bumps updatedAt without changing the
    // pinned revision id; the run token catches it.
    expect(
      isProposalStale(
        proposal({ baseRevisionId: 'rev-1', baseRunUpdatedAt: 'ts-1' }),
        'rev-1',
        'ts-2',
      ),
    ).toBe(true)
  })

  it('is stale (undecidable) before the run is persisted', () => {
    expect(isProposalStale(proposal({ baseRevisionId: 'rev-1' }), null, 'ts-1')).toBe(true)
    expect(isProposalStale(proposal({ baseRevisionId: 'rev-1' }), 'rev-1', null)).toBe(true)
  })
})

describe('canApproveProposal', () => {
  const base = {
    proposal: proposal({ baseRevisionId: 'rev-1', baseRunUpdatedAt: 'ts-1' }),
    currentRevisionId: 'rev-1',
    currentRunUpdatedAt: 'ts-1',
  }

  it('allows approval of a fresh proposal on a clean, unfrozen design space', () => {
    expect(canApproveProposal({ ...base, frozen: false, dirty: false })).toBe(true)
  })

  it('blocks approval while the design space is dirty', () => {
    expect(canApproveProposal({ ...base, frozen: false, dirty: true })).toBe(false)
  })

  it('blocks approval while the run is frozen', () => {
    expect(canApproveProposal({ ...base, frozen: true, dirty: false })).toBe(false)
  })

  it('blocks approval of a proposal stale by revision', () => {
    expect(
      canApproveProposal({ ...base, currentRevisionId: 'rev-2', frozen: false, dirty: false }),
    ).toBe(false)
  })

  it('blocks approval of a proposal stale by run token', () => {
    expect(
      canApproveProposal({ ...base, currentRunUpdatedAt: 'ts-2', frozen: false, dirty: false }),
    ).toBe(false)
  })
})

describe('describeProposal (renders the backend effectPreview verbatim)', () => {
  it('titles an add-parameter preview and lists its name plus each field', () => {
    const summary = describeProposal(
      proposal({
        kind: 'designSpacePatch',
        effectPreview: {
          entityType: 'parameter',
          entityId: 'p-temp',
          operation: 'add',
          entityName: 'Temperature',
          changedFields: [
            { field: 'type', before: null, after: 'Continuous' },
            { field: 'unit', before: null, after: '°C' },
            { field: 'lowerBound', before: null, after: '20' },
            { field: 'upperBound', before: null, after: '80' },
          ],
        },
      }),
    )
    expect(summary.title).toBe('Add parameter')
    expect(summary.lines).toContain('Name: Temperature')
    expect(summary.lines).toContain('type: Continuous')
    expect(summary.lines).toContain('unit: °C')
    expect(summary.lines).toContain('upperBound: 80')
  })

  it('renders an update as before → after only for the changed fields', () => {
    const summary = describeProposal(
      proposal({
        kind: 'designSpacePatch',
        effectPreview: {
          entityType: 'parameter',
          entityId: 'p-temp',
          operation: 'update',
          entityName: 'Temperature',
          changedFields: [{ field: 'upperBound', before: '80', after: '90' }],
        },
      }),
    )
    expect(summary.title).toBe('Update parameter')
    expect(summary.lines).toContain('Name: Temperature')
    expect(summary.lines).toContain('upperBound: 80 → 90')
  })

  it('renders a cleared optional field as "(empty)"', () => {
    const summary = describeProposal(
      proposal({
        kind: 'designSpacePatch',
        effectPreview: {
          entityType: 'parameter',
          entityId: 'p-temp',
          operation: 'update',
          entityName: 'Temperature',
          changedFields: [{ field: 'unit', before: '°C', after: '' }],
        },
      }),
    )
    expect(summary.lines).toContain('unit: °C → (empty)')
  })

  it('renders an objective direction change', () => {
    const summary = describeProposal(
      proposal({
        kind: 'designSpacePatch',
        effectPreview: {
          entityType: 'objective',
          entityId: 't-1',
          operation: 'update',
          entityName: 'Yield',
          changedFields: [{ field: 'direction', before: 'Maximize', after: 'Minimize' }],
        },
      }),
    )
    expect(summary.title).toBe('Update objective')
    expect(summary.lines).toContain('Name: Yield')
    expect(summary.lines).toContain('direction: Maximize → Minimize')
  })

  it('renders a delete preview with the entity name and its fields, not just an id', () => {
    const summary = describeProposal(
      proposal({
        kind: 'designSpacePatch',
        effectPreview: {
          entityType: 'parameter',
          entityId: 'p-42',
          operation: 'delete',
          entityName: 'Pressure',
          changedFields: [
            { field: 'type', before: 'Continuous', after: null },
            { field: 'lowerBound', before: '1', after: null },
            { field: 'upperBound', before: '5', after: null },
          ],
        },
      }),
    )
    expect(summary.title).toBe('Delete parameter')
    expect(summary.lines).toContain('Name: Pressure')
    expect(summary.lines).toContain('lowerBound: 1')
    expect(summary.lines.some((line) => line.includes('→'))).toBe(false)
  })

  it('titles a fixed-sum constraint set operation', () => {
    const summary = describeProposal(
      proposal({
        kind: 'designSpacePatch',
        effectPreview: {
          entityType: 'constraint',
          entityId: null,
          operation: 'set',
          entityName: null,
          changedFields: [{ field: 'fixedSum', before: null, after: 'p-resin + p-hardener = 100' }],
        },
      }),
    )
    expect(summary.title).toBe('Set constraint')
    expect(summary.lines).toContain('fixedSum: p-resin + p-hardener = 100')
  })

  it('degrades to a bare title when a design-space patch carries no effectPreview', () => {
    const summary = describeProposal(proposal({ kind: 'designSpacePatch', effectPreview: undefined }))
    expect(summary.title).toBe('Design-space change')
    expect(summary.lines).toEqual([])
  })

  it('describes the validate and generate actions', () => {
    expect(
      describeProposal(proposal({ kind: 'validateDesignSpace', payload: { kind: 'validateDesignSpace' } })).title,
    ).toBe('Validate design space')
    expect(
      describeProposal(proposal({ kind: 'generateInitialDesign', payload: { kind: 'generateInitialDesign' } })).title,
    ).toBe('Generate initial design')
  })
})

describe('runToken (validate/generate sync)', () => {
  it('reads both halves of the token from a fresh run DTO', () => {
    const token = runToken(run({ definitionRevisionId: 'rev-9', updatedAt: 'ts-9' }))
    expect(token).toEqual({ currentRevisionId: 'rev-9', currentRunUpdatedAt: 'ts-9' })
  })

  it('a proposal minted after a manual validate is not judged stale', () => {
    // A manual validate bumps updatedAt (Draft -> DesignSpaceValidated) without
    // changing the pinned revision. Syncing from the validate response's run and
    // pinning a new proposal to that same token must leave it fresh.
    const validated = run({ status: 'DesignSpaceValidated', updatedAt: 'ts-2' })
    const token = runToken(validated)
    const fresh = proposal({ baseRevisionId: 'rev-1', baseRunUpdatedAt: 'ts-2' })
    expect(isProposalStale(fresh, token.currentRevisionId, token.currentRunUpdatedAt)).toBe(false)
    expect(
      canApproveProposal({
        proposal: fresh,
        frozen: false,
        dirty: false,
        currentRevisionId: token.currentRevisionId,
        currentRunUpdatedAt: token.currentRunUpdatedAt,
      }),
    ).toBe(true)
  })

  it('syncs the bumped token even when validation failed', () => {
    // A failing validation still records an outcome and bumps updatedAt. If the
    // frontend skipped the sync on failure, a proposal pinned to the new token
    // (ts-3) would be wrongly judged stale against the old one (ts-1).
    const failed = run({ status: 'Draft', updatedAt: 'ts-3' })
    const token = runToken(failed)
    expect(token.currentRunUpdatedAt).toBe('ts-3')
    const pinnedToBumped = proposal({ baseRevisionId: 'rev-1', baseRunUpdatedAt: 'ts-3' })
    expect(
      isProposalStale(pinnedToBumped, token.currentRevisionId, token.currentRunUpdatedAt),
    ).toBe(false)
    // The pre-validate token (ts-1) would have made it look stale — proving the
    // sync is load-bearing.
    expect(isProposalStale(pinnedToBumped, 'rev-1', 'ts-1')).toBe(true)
  })
})
