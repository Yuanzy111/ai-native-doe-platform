// Pure helpers for the conversational agent panel. Kept free of React so the
// send guards and proposal rendering can be unit-tested directly.
//
// `describeProposal` reads only the stored payload to render the approval card;
// it never executes anything. The payload mirrors the backend action contract
// (see backend/agent/contract.py): an action keyed on `kind`, and for a design
// space patch a single op keyed on `op`.

import type {
  AgentProposalDto,
  CampaignRunDto,
  ChangedFieldDto,
  EffectPreviewDto,
} from '../../../api/types'

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

const _OPERATION_TITLES: Record<
  EffectPreviewDto['operation'],
  Record<EffectPreviewDto['entityType'], string>
> = {
  add: { parameter: 'Add parameter', objective: 'Add objective', constraint: 'Set constraint' },
  update: { parameter: 'Update parameter', objective: 'Update objective', constraint: 'Set constraint' },
  delete: { parameter: 'Delete parameter', objective: 'Delete objective', constraint: 'Remove constraint' },
  set: { parameter: 'Set parameter', objective: 'Set objective', constraint: 'Set constraint' },
}

// Render one backend-computed changed field as a line. An empty string means
// the field was cleared (or, on a delete/add, is absent on one side) and is
// shown as "(empty)". A genuine before→after change renders as "old → new"; a
// one-sided change (add/delete) renders the single present value.
function changedFieldLine(change: ChangedFieldDto): string {
  const show = (value: string | null): string =>
    value === null ? '' : value === '' ? '(empty)' : value
  const { field, before, after } = change
  if (before === null) return `${field}: ${show(after)}`
  if (after === null) return `${field}: ${show(before)}`
  if (before === after) return `${field}: ${show(after)}`
  return `${field}: ${show(before)} → ${show(after)}`
}

// Turn the backend EffectPreview into a title + lines. The frontend renders the
// preview verbatim and never recomputes a diff from on-screen state (§7).
function previewSummary(preview: EffectPreviewDto): ProposalSummary {
  const title = _OPERATION_TITLES[preview.operation][preview.entityType]
  const lines: string[] = []
  if (preview.entityName) lines.push(`Name: ${preview.entityName}`)
  for (const change of preview.changedFields) lines.push(changedFieldLine(change))
  return { title, lines }
}

export function describeProposal(proposal: AgentProposalDto): ProposalSummary {
  if (proposal.kind === 'designSpacePatch') {
    // A pending patch always carries a backend effectPreview; render it. Its
    // absence (a resolved proposal, or a vanished base revision) degrades to a
    // bare title rather than a client-side re-derivation.
    if (proposal.effectPreview) return previewSummary(proposal.effectPreview)
    return { title: 'Design-space change', lines: [] }
  }
  if (proposal.kind === 'validateDesignSpace') {
    return { title: 'Validate design space', lines: ['Run the deterministic design-space validation.'] }
  }
  if (proposal.kind === 'generateInitialDesign') {
    return {
      title: 'Generate initial design',
      lines: ['Generate the first-round design with the optimizer.'],
    }
  }
  return { title: 'Proposal', lines: [] }
}
