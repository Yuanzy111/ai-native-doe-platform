// Pure helpers for the conversational agent panel. Kept free of React so the
// send guards and proposal rendering can be unit-tested directly.
//
// `describeProposal` reads only the stored payload to render the approval card;
// it never executes anything. The payload mirrors the backend action contract
// (see backend/agent/contract.py): an action keyed on `kind`, and for a design
// space patch a single op keyed on `op`.

import type { AgentProposalDto, CampaignRunDto } from '../../../api/types'

// The pair the stale check compares a proposal's pin against, read from a single
// fresh run DTO. Every response that mutates a run (create/save/validate/
// generate/approve) carries a full CampaignRunDto, and each bumps updatedAt — a
// failing validation records its outcome and still bumps it. Extracting both
// halves from one place keeps the frontend's token in lockstep with the backend
// and stops a handler from syncing one field while forgetting the other.
export interface RunToken {
  currentRevisionId: string
  currentRunUpdatedAt: string
}

export function runToken(run: CampaignRunDto): RunToken {
  return {
    currentRevisionId: run.definitionRevisionId,
    currentRunUpdatedAt: run.updatedAt,
  }
}

// A message is sendable only when it has non-whitespace content and no request
// is already in flight. This blocks empty sends and duplicate concurrent sends,
// which would waste model calls.
export function canSendMessage(draft: string, sending: boolean): boolean {
  return !sending && draft.trim().length > 0
}

// A design-space patch is the only proposal kind that would modify the campaign;
// validate/generate advance the lifecycle instead. Used to disable approval of a
// modification once the design space is frozen.
export function isModificationProposal(proposal: AgentProposalDto): boolean {
  return proposal.kind === 'designSpacePatch'
}

// A proposal is stale when the run moved since it was minted — either the
// pinned revision changed, or the run's version token (`updatedAt`, which every
// status/policy/revision change bumps) changed. Approving a stale proposal would
// apply it on top of a design space, policy, or lifecycle state the user has
// since changed. Both `currentRevisionId` and `currentRunUpdatedAt` are null
// before a run is persisted, in which case staleness is undecidable and we treat
// it as stale to force a save first. This mirrors the backend token exactly.
export function isProposalStale(
  proposal: AgentProposalDto,
  currentRevisionId: string | null,
  currentRunUpdatedAt: string | null,
): boolean {
  if (currentRevisionId === null || currentRunUpdatedAt === null) return true
  return (
    proposal.baseRevisionId !== currentRevisionId ||
    proposal.baseRunUpdatedAt !== currentRunUpdatedAt
  )
}

// Approval is allowed only when nothing would silently clobber the user's work:
// the run must be neither frozen (all proposals blocked, not just modifications)
// nor dirty (unsaved local edits), and the proposal must be pinned to the run's
// current revision *and* version token.
export function canApproveProposal(params: {
  proposal: AgentProposalDto
  frozen: boolean
  dirty: boolean
  currentRevisionId: string | null
  currentRunUpdatedAt: string | null
}): boolean {
  const { proposal, frozen, dirty, currentRevisionId, currentRunUpdatedAt } = params
  if (frozen || dirty) return false
  return !isProposalStale(proposal, currentRevisionId, currentRunUpdatedAt)
}

export interface ProposalSummary {
  title: string
  lines: string[]
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object'
    ? (value as Record<string, unknown>)
    : {}
}

function asText(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null
}

function parameterLines(parameter: Record<string, unknown>): string[] {
  const lines: string[] = []
  const type = asText(parameter.type)
  const name = asText(parameter.name)
  lines.push(`Name: ${name ?? '(unnamed)'}${type ? ` (${type})` : ''}`)
  const unit = asText(parameter.unit)
  if (unit) lines.push(`Unit: ${unit}`)
  if (type === 'Continuous') {
    const lower = parameter.lowerBound
    const upper = parameter.upperBound
    if (typeof lower === 'number' && typeof upper === 'number') {
      lines.push(`Range: ${lower} – ${upper}`)
    }
  } else if (Array.isArray(parameter.values)) {
    lines.push(`Values: ${parameter.values.map(String).join(', ')}`)
  }
  return lines
}

function objectiveLines(objective: Record<string, unknown>): string[] {
  const lines: string[] = []
  const name = asText(objective.name)
  const direction = asText(objective.direction)
  lines.push(`Objective: ${name ?? '(unnamed)'}`)
  if (direction) lines.push(`Direction: ${direction}`)
  const unit = asText(objective.unit)
  if (unit) lines.push(`Unit: ${unit}`)
  return lines
}

function patchSummary(patch: Record<string, unknown>): ProposalSummary {
  const op = asText(patch.op)
  switch (op) {
    case 'addParameter':
      return { title: 'Add parameter', lines: parameterLines(asRecord(patch.parameter)) }
    case 'updateParameter':
      return {
        title: 'Update parameter',
        lines: [`Id: ${asText(patch.id) ?? '(missing)'}`, ...parameterLines(asRecord(patch.parameter))],
      }
    case 'deleteParameter':
      return { title: 'Delete parameter', lines: [`Id: ${asText(patch.id) ?? '(missing)'}`] }
    case 'addObjective':
      return { title: 'Add objective', lines: objectiveLines(asRecord(patch.objective)) }
    case 'updateObjective':
      return {
        title: 'Update objective',
        lines: [`Id: ${asText(patch.id) ?? '(missing)'}`, ...objectiveLines(asRecord(patch.objective))],
      }
    case 'deleteObjective':
      return { title: 'Delete objective', lines: [`Id: ${asText(patch.id) ?? '(missing)'}`] }
    case 'setNoConstraint':
      return { title: 'Set constraint', lines: ['No fixed-sum composition constraint'] }
    case 'setFixedSumConstraint': {
      const rhs = typeof patch.rhs === 'number' ? patch.rhs : 100
      const ids = Array.isArray(patch.parameterIds)
        ? patch.parameterIds.map(String).join(' + ')
        : 'the composition parameters'
      return { title: 'Set constraint', lines: [`Fixed sum: ${ids} = ${rhs}`] }
    }
    default:
      return { title: 'Design-space change', lines: [`Unrecognized op: ${op ?? '(none)'}`] }
  }
}

export function describeProposal(proposal: AgentProposalDto): ProposalSummary {
  switch (proposal.kind) {
    case 'designSpacePatch':
      return patchSummary(asRecord(proposal.payload).patch as Record<string, unknown>)
    case 'validateDesignSpace':
      return { title: 'Validate design space', lines: ['Run the deterministic design-space validation.'] }
    case 'generateInitialDesign':
      return {
        title: 'Generate initial design',
        lines: ['Generate the first-round design with the optimizer.'],
      }
    default:
      return { title: 'Proposal', lines: [] }
  }
}
