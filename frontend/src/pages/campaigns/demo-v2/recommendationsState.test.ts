import { describe, expect, it } from 'vitest'
import {
  buildRecommendationRows,
  resolveInitialStage,
  selectLatestBatch,
  selectRecommendationsData,
} from './recommendationsState'
import type { Parameter } from './types'
import type {
  ExperimentRoundDto,
  ExperimentRunDto,
  RecommendationBatchDto,
  RecommendationCandidateDto,
  RunViewDto,
} from '../../../api/types'

const PARAMETERS: Parameter[] = [
  { id: 'p-resin', name: 'Resin Ratio', type: 'Continuous', unit: '%', description: '', lowerBound: '60', upperBound: '85' },
  { id: 'p-hardener', name: 'Hardener Ratio', type: 'Continuous', unit: '%', description: '', lowerBound: '15', upperBound: '40' },
]

function candidate(id: string, values: Record<string, number>): RecommendationCandidateDto {
  return { id, parameterValues: values, predictedMean: null, predictedSd: null, desirability: null }
}

function batch(overrides: Partial<RecommendationBatchDto> = {}): RecommendationBatchDto {
  return {
    id: 'batch-1',
    campaignRunId: 'run-1',
    roundNumber: 1,
    generatedAt: '2026-02-01T10:00:00Z',
    inputSnapshot: {},
    algorithmConfig: {
      backendName: 'baybe',
      backendVersion: '0.9.0',
      backendCommit: 'abc123',
      strategyKind: 'TwoPhaseMeta',
      hyperparameters: {},
      acquisitionFunction: 'qLogEI',
      seed: 42,
      environment: {
        pythonVersion: '3.11',
        torchVersion: '2.2',
        botorchVersion: '0.10',
        dependencyLockHash: 'sha256:xyz',
      },
    },
    candidates: [
      candidate('cand-1', { 'p-resin': 70, 'p-hardener': 30 }),
      candidate('cand-2', { 'p-resin': 65, 'p-hardener': 35 }),
    ],
    status: 'Proposed',
    ...overrides,
  }
}

function round(overrides: Partial<ExperimentRoundDto> = {}): ExperimentRoundDto {
  return {
    id: 'round-1',
    campaignRunId: 'run-1',
    roundNumber: 1,
    recommendationBatchId: 'batch-1',
    openedAt: '2026-02-01T10:00:00Z',
    closedAt: null,
    status: 'Open',
    ...overrides,
  }
}

function run(overrides: Partial<ExperimentRunDto> = {}): ExperimentRunDto {
  return {
    id: 'exp-1',
    campaignRunId: 'run-1',
    experimentRoundId: 'round-1',
    recommendationCandidateId: 'cand-1',
    parameterValues: { 'p-resin': 70, 'p-hardener': 30 },
    status: 'Pending',
    executedAt: null,
    executedBy: null,
    notes: null,
    ...overrides,
  }
}

function view(overrides: Partial<RunViewDto> = {}): RunViewDto {
  return {
    campaignDefinition: {} as RunViewDto['campaignDefinition'],
    pinnedRevision: {} as RunViewDto['pinnedRevision'],
    campaignRun: {} as RunViewDto['campaignRun'],
    recommendationBatches: [],
    experimentRounds: [],
    experimentRuns: [],
    measurements: [],
    decisionLogs: [],
    ...overrides,
  }
}

describe('selectLatestBatch', () => {
  it('returns null when there are no batches', () => {
    expect(selectLatestBatch([])).toBeNull()
  })

  it('returns the highest-round batch', () => {
    const older = batch({ id: 'batch-1', roundNumber: 1 })
    const newer = batch({ id: 'batch-2', roundNumber: 2 })
    expect(selectLatestBatch([older, newer])?.id).toBe('batch-2')
    expect(selectLatestBatch([newer, older])?.id).toBe('batch-2')
  })
})

describe('selectRecommendationsData (restore from aggregate)', () => {
  it('assembles the latest batch with its round and experiments', () => {
    const data = selectRecommendationsData(
      view({
        recommendationBatches: [batch()],
        experimentRounds: [round()],
        experimentRuns: [
          run({ id: 'exp-1', recommendationCandidateId: 'cand-1' }),
          run({ id: 'exp-2', recommendationCandidateId: 'cand-2' }),
        ],
      }),
    )
    expect(data).not.toBeNull()
    expect(data?.batch.id).toBe('batch-1')
    expect(data?.round?.id).toBe('round-1')
    expect(data?.experimentRuns.map((r) => r.id)).toEqual(['exp-1', 'exp-2'])
  })

  it('returns null when the run has no batch', () => {
    expect(selectRecommendationsData(view())).toBeNull()
  })

  it('ignores experiments belonging to another round', () => {
    const data = selectRecommendationsData(
      view({
        recommendationBatches: [batch()],
        experimentRounds: [round()],
        experimentRuns: [
          run({ id: 'exp-1', experimentRoundId: 'round-1' }),
          run({ id: 'exp-other', experimentRoundId: 'round-2' }),
        ],
      }),
    )
    expect(data?.experimentRuns.map((r) => r.id)).toEqual(['exp-1'])
  })
})

describe('buildRecommendationRows', () => {
  it('pairs each candidate with its experiment run by recommendationCandidateId', () => {
    const rows = buildRecommendationRows(
      batch(),
      [
        run({ id: 'exp-2', recommendationCandidateId: 'cand-2', status: 'Completed' }),
        run({ id: 'exp-1', recommendationCandidateId: 'cand-1', status: 'Pending' }),
      ],
      PARAMETERS,
    )
    expect(rows).toHaveLength(2)
    // Row order follows candidate order, not experiment order.
    expect(rows[0]).toMatchObject({ candidateId: 'cand-1', position: 1, experimentId: 'exp-1', experimentStatus: 'Pending' })
    expect(rows[1]).toMatchObject({ candidateId: 'cand-2', position: 2, experimentId: 'exp-2', experimentStatus: 'Completed' })
  })

  it('exposes parameter columns by name in parameter order', () => {
    const rows = buildRecommendationRows(batch(), [run()], PARAMETERS)
    expect(rows[0].cells).toEqual([
      { paramId: 'p-resin', name: 'Resin Ratio', value: '70' },
      { paramId: 'p-hardener', name: 'Hardener Ratio', value: '30' },
    ])
  })

  it('shows an em dash for missing predictions instead of fabricating values', () => {
    const rows = buildRecommendationRows(batch(), [run()], PARAMETERS)
    expect(rows[0].predictedMean).toBe('—')
    expect(rows[0].predictedSd).toBe('—')
    expect(rows[0].desirability).toBe('—')
  })

  it('leaves experiment id and status null when no run matches a candidate', () => {
    const rows = buildRecommendationRows(batch(), [], PARAMETERS)
    expect(rows[0].experimentId).toBeNull()
    expect(rows[0].experimentStatus).toBeNull()
  })
})

describe('resolveInitialStage', () => {
  it('honours an explicit, enabled ?stage', () => {
    expect(resolveInitialStage(null, 'recommendations')).toBe('recommendations')
    expect(resolveInitialStage(null, 'design-space')).toBe('design-space')
  })

  it('ignores a disabled ?stage and falls back', () => {
    expect(resolveInitialStage(null, 'execution')).toBe('design-space')
  })

  it('defaults to recommendations when a batch already exists', () => {
    expect(resolveInitialStage(view({ recommendationBatches: [batch()] }), null)).toBe(
      'recommendations',
    )
  })

  it('defaults to design-space with no batch and no stage param', () => {
    expect(resolveInitialStage(view(), null)).toBe('design-space')
  })
})
