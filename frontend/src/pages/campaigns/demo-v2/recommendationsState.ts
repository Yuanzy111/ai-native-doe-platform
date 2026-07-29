// Pure selectors and view-model builders for the Recommendations stage. Kept
// free of React so the gating, candidate<->experiment pairing, and stage
// resolution can be unit-tested directly.

import type {
  ExperimentRoundDto,
  ExperimentRunDto,
  ExperimentRunStatus,
  RecommendationBatchDto,
  RunViewDto,
} from '../../../api/types'
import type { Parameter, StageKey } from './types'

// Which stages a user can actually open. Recommendations only unlocks once a
// batch has been generated; before that only the Design Space is navigable.
export function enabledStageKeys(hasBatch: boolean): StageKey[] {
  return hasBatch ? ['design-space', 'recommendations'] : ['design-space']
}

export function isStageEnabled(key: StageKey, hasBatch: boolean): boolean {
  return enabledStageKeys(hasBatch).includes(key)
}

// The batch a fresh Recommendations view should show: the highest round number
// (latest generation). Returns null when no batch has been generated.
export function selectLatestBatch(
  batches: RecommendationBatchDto[],
): RecommendationBatchDto | null {
  if (batches.length === 0) return null
  return batches.reduce((latest, batch) =>
    batch.roundNumber > latest.roundNumber ? batch : latest,
  )
}

export function selectRoundForBatch(
  rounds: ExperimentRoundDto[],
  batchId: string,
): ExperimentRoundDto | null {
  return rounds.find((round) => round.recommendationBatchId === batchId) ?? null
}

export function selectExperimentRunsForRound(
  runs: ExperimentRunDto[],
  roundId: string,
): ExperimentRunDto[] {
  return runs.filter((run) => run.experimentRoundId === roundId)
}

// --- row view-model --------------------------------------------------------

export interface RecommendationParamCell {
  paramId: string
  name: string
  value: string
}

export interface RecommendationRow {
  candidateId: string
  position: number
  cells: RecommendationParamCell[]
  experimentId: string | null
  experimentStatus: ExperimentRunStatus | null
  predictedMean: string
  predictedSd: string
  desirability: string
}

const EM_DASH = '—'

function formatValue(value: string | number | undefined): string {
  if (value === undefined) return EM_DASH
  return typeof value === 'number' ? String(value) : value
}

// Predicted fields are absent for a model-free initial design; show an em dash
// rather than fabricating a number. When present (future rounds) render the
// per-output values compactly.
function formatRecord(record: Record<string, number> | null): string {
  if (record === null) return EM_DASH
  const entries = Object.entries(record)
  if (entries.length === 0) return EM_DASH
  return entries.map(([key, val]) => `${key}: ${val}`).join(', ')
}

function formatNumber(value: number | null): string {
  return value === null ? EM_DASH : String(value)
}

// Build one row per candidate, in candidate order. Each row is joined to its
// experiment run by recommendationCandidateId, and parameter columns follow the
// supplied parameter order using human names (never internal parameter ids).
export function buildRecommendationRows(
  batch: RecommendationBatchDto,
  experimentRuns: ExperimentRunDto[],
  parameters: Parameter[],
): RecommendationRow[] {
  const runByCandidate = new Map<string, ExperimentRunDto>()
  for (const run of experimentRuns) {
    if (run.recommendationCandidateId !== null) {
      runByCandidate.set(run.recommendationCandidateId, run)
    }
  }

  return batch.candidates.map((candidate, index) => {
    const run = runByCandidate.get(candidate.id) ?? null
    const cells: RecommendationParamCell[] = parameters.map((param) => ({
      paramId: param.id,
      name: param.name,
      value: formatValue(candidate.parameterValues[param.id]),
    }))
    return {
      candidateId: candidate.id,
      position: index + 1,
      cells,
      experimentId: run?.id ?? null,
      experimentStatus: run?.status ?? null,
      predictedMean: formatRecord(candidate.predictedMean),
      predictedSd: formatRecord(candidate.predictedSd),
      desirability: formatNumber(candidate.desirability),
    }
  })
}

// --- assembled view --------------------------------------------------------

export interface RecommendationsData {
  batch: RecommendationBatchDto
  round: ExperimentRoundDto | null
  experimentRuns: ExperimentRunDto[]
}

// Resolve the latest batch and its round/experiments out of a full aggregate.
// Returns null when the run has no recommendation batch yet.
export function selectRecommendationsData(view: RunViewDto): RecommendationsData | null {
  const batch = selectLatestBatch(view.recommendationBatches)
  if (batch === null) return null
  const round = selectRoundForBatch(view.experimentRounds, batch.id)
  const experimentRuns =
    round === null ? [] : selectExperimentRunsForRound(view.experimentRuns, round.id)
  return { batch, round, experimentRuns }
}

// Decide which stage to open. An explicit, enabled `?stage` wins; otherwise a
// run that already has a batch defaults to Recommendations, and everything else
// opens on the Design Space.
export function resolveInitialStage(
  view: RunViewDto | null,
  urlStage: string | null,
): StageKey {
  const hasBatch = view !== null && view.recommendationBatches.length > 0
  if (urlStage !== null && isStageEnabled(urlStage as StageKey, hasBatch)) {
    return urlStage as StageKey
  }
  return hasBatch ? 'recommendations' : 'design-space'
}
