import { useEffect, useRef, useState } from 'react'
import CampaignHeader from './components/CampaignHeader'
import StageNav from './components/StageNav'
import MainWorkspace from './components/MainWorkspace'
import AgentPanel from './components/AgentPanel'
import RecommendationsView from './components/RecommendationsView'
import ParameterDialog from './components/ParameterDialog'
import ObjectiveDialog from './components/ObjectiveDialog'
import ToastStack from './components/ToastStack'
import { campaignData, initialObjectives, initialParameters, stages } from './mockData'
import { useToasts } from './useToasts'
import { canGenerateInitialDesign, displayStatus, isLifecycleLocked } from './runState'
import { newFixedSumConstraintId } from './constraintUtils'
import {
  enabledStageKeys,
  resolveInitialStage,
  selectRecommendationsData,
  type RecommendationsData,
} from './recommendationsState'
import type { ConstraintChoice, ConstraintState, Objective, Parameter, StageKey } from './types'
import { ApiError } from '../../../api/client'
import {
  createCampaignRun,
  generateInitialDesign,
  getCampaignRun,
  saveDesignSpace,
  validateDesignSpace,
} from '../../../api/campaignRuns'
import {
  DEFAULT_POLICY_BASE,
  MappingError,
  hydrateFromView,
  toCreateBody,
  toDesignSpaceBody,
  type DesignSpaceInputs,
  type PolicyBase,
  type UnsupportedReason,
} from '../../../api/mapper'
import {
  approveProposal,
  getAgentThread,
  postAgentMessage,
  rejectProposal,
} from '../../../api/agent'
import { canSendMessage, isProposalStale } from './agentState'
import type {
  AgentMessageDto,
  AgentProposalDto,
  RunStatus,
  RunViewDto,
  ValidationIssueDto,
} from '../../../api/types'

interface HeaderMeta {
  title: string
  goal: string
  round: number
  budgetUsed: number
  budgetTotal: number
  batchSize: number
}

const DEFAULT_META: HeaderMeta = {
  title: campaignData.title,
  goal: campaignData.goal,
  round: campaignData.round,
  budgetUsed: campaignData.budgetUsed,
  budgetTotal: campaignData.budgetTotal,
  batchSize: campaignData.batchSize,
}

function readRunIdFromUrl(): string | null {
  return new URLSearchParams(window.location.search).get('runId')
}

function writeRunIdToUrl(runId: string): void {
  const url = new URL(window.location.href)
  url.searchParams.set('runId', runId)
  window.history.replaceState(null, '', url)
}

function clearRunIdFromUrl(): void {
  const url = new URL(window.location.href)
  url.searchParams.delete('runId')
  url.searchParams.delete('stage')
  window.history.replaceState(null, '', url)
}

function readStageFromUrl(): string | null {
  return new URLSearchParams(window.location.search).get('stage')
}

function writeStageToUrl(stage: StageKey): void {
  const url = new URL(window.location.href)
  url.searchParams.set('stage', stage)
  window.history.replaceState(null, '', url)
}

export default function CampaignDemoV2() {
  const [parameters, setParameters] = useState<Parameter[]>(initialParameters)
  const [editingParameter, setEditingParameter] = useState<Parameter | null>(null)
  const [parameterDialogOpen, setParameterDialogOpen] = useState(false)

  const [objectives, setObjectives] = useState<Objective[]>(initialObjectives)
  const [editingObjective, setEditingObjective] = useState<Objective | null>(null)
  const [objectiveDialogOpen, setObjectiveDialogOpen] = useState(false)

  const [constraint, setConstraint] = useState<ConstraintState>({
    choice: null,
  })

  const [runId, setRunId] = useState<string | null>(null)
  const [currentRevisionId, setCurrentRevisionId] = useState<string | null>(null)
  const [serverStatus, setServerStatus] = useState<RunStatus | null>(null)
  const [policyBase, setPolicyBase] = useState<PolicyBase>(DEFAULT_POLICY_BASE)
  const [meta, setMeta] = useState<HeaderMeta>(DEFAULT_META)
  const [dirty, setDirty] = useState(false)
  const [blockingIssues, setBlockingIssues] = useState<ValidationIssueDto[]>([])
  const [unsupported, setUnsupported] = useState<UnsupportedReason[]>([])
  const [restoreError, setRestoreError] = useState<string | null>(null)

  const [activeStage, setActiveStage] = useState<StageKey>('design-space')
  const [recommendations, setRecommendations] = useState<RecommendationsData | null>(null)

  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [validating, setValidating] = useState(false)
  const [generating, setGenerating] = useState(false)

  const [agentMessages, setAgentMessages] = useState<AgentMessageDto[]>([])
  const [agentPendingProposals, setAgentPendingProposals] = useState<AgentProposalDto[]>([])
  const [agentDraft, setAgentDraft] = useState('')
  const [agentSending, setAgentSending] = useState(false)
  const [agentActioningId, setAgentActioningId] = useState<string | null>(null)
  const [agentError, setAgentError] = useState<string | null>(null)

  const { toasts, pushToast, dismissToast } = useToasts()

  const applyView = (view: RunViewDto) => {
    const hydrated = hydrateFromView(view)
    setParameters(hydrated.parameters)
    setObjectives(hydrated.objectives)
    setConstraint(hydrated.constraint)
    setPolicyBase(hydrated.policyBase)
    setServerStatus(hydrated.status)
    setCurrentRevisionId(view.pinnedRevision.id)
    setMeta({
      title: hydrated.title,
      goal: hydrated.goal,
      round: hydrated.round,
      budgetUsed: hydrated.budgetUsed,
      budgetTotal: hydrated.budgetTotal,
      batchSize: hydrated.batchSize,
    })
    setUnsupported(hydrated.unsupported)
    setRecommendations(selectRecommendationsData(view))
    setDirty(false)
    setBlockingIssues([])
  }

  const applyThread = (messages: AgentMessageDto[], pending: AgentProposalDto[]) => {
    setAgentMessages(messages)
    setAgentPendingProposals(pending)
  }

  // Load a run by id, surfacing failures as an explicit error screen (with
  // Retry / Start New Draft) rather than leaving an editable default page bound
  // to a runId the server rejected. The agent thread is restored alongside the
  // run so a refresh brings back the conversation and any pending proposals.
  const loadRun = (id: string) => {
    setRunId(id)
    setRestoreError(null)
    setAgentError(null)
    setLoading(true)
    Promise.all([getCampaignRun(id), getAgentThread(id)])
      .then(([view, thread]) => {
        applyView(view)
        applyThread(thread.messages, thread.pendingProposals)
        setActiveStage(resolveInitialStage(view, readStageFromUrl()))
      })
      .catch((err: unknown) => {
        const message =
          err instanceof ApiError
            ? `Could not restore run: ${err.code}: ${err.message}`
            : 'Could not restore the campaign run.'
        setRestoreError(message)
      })
      .finally(() => setLoading(false))
  }

  // Restore an existing run from `?runId` on first load.
  const restored = useRef(false)
  useEffect(() => {
    if (restored.current) return
    restored.current = true
    const existingId = readRunIdFromUrl()
    if (existingId !== null) loadRun(existingId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const startNewDraft = () => {
    clearRunIdFromUrl()
    setRunId(null)
    setCurrentRevisionId(null)
    setServerStatus(null)
    setPolicyBase(DEFAULT_POLICY_BASE)
    setMeta(DEFAULT_META)
    setParameters(initialParameters)
    setObjectives(initialObjectives)
    setConstraint({ choice: null })
    setUnsupported([])
    setBlockingIssues([])
    setDirty(false)
    setRestoreError(null)
    setRecommendations(null)
    setActiveStage('design-space')
    setAgentMessages([])
    setAgentPendingProposals([])
    setAgentDraft('')
    setAgentError(null)
  }

  const handleSelectStage = (key: StageKey) => {
    if (!enabledStageKeys(recommendations !== null).includes(key)) return
    setActiveStage(key)
    writeStageToUrl(key)
  }

  const markDirty = () => {
    setDirty(true)
    setBlockingIssues([])
  }

  // Frozen once the run's lifecycle has advanced past validation (an initial
  // design exists); derived from the authoritative server status so the gate
  // matches the backend after a reload, not just a local flag.
  const lifecycleLocked = isLifecycleLocked(serverStatus)
  const locked = unsupported.length > 0 || lifecycleLocked
  const hasBatch = recommendations !== null
  const enabledKeys = enabledStageKeys(hasBatch)
  // The nav must never highlight Recommendations while the Design Space is on
  // screen: fall back to design-space whenever no batch exists.
  const effectiveStage: StageKey = hasBatch ? activeStage : 'design-space'
  const showRecommendations = effectiveStage === 'recommendations' && recommendations !== null

  const experimentSummary =
    recommendations !== null
      ? `第 ${meta.round} 轮:已生成 ${recommendations.batch.candidates.length} 个候选实验推荐,请在 Recommendations 页面查看。`
      : `${parameters.length} 个参数、${objectives.length} 个优化目标已配置完成,尚未生成首轮实验推荐。`
  const status = displayStatus(serverStatus, dirty)
  const canGenerate = canGenerateInitialDesign(serverStatus, dirty, hasBatch) && !locked

  const handleChooseConstraint = (choice: ConstraintChoice) => {
    if (locked) return
    if (choice === 'fixed-sum') {
      // A freshly authored fixed-sum gets a new stable id and a null resolvedAt;
      // a hydrated one keeps the server's values via applyView.
      setConstraint({
        choice,
        constraintId: newFixedSumConstraintId(),
        resolvedAt: null,
      })
    } else {
      setConstraint({ choice })
    }
    markDirty()
  }

  const handleAddParameter = () => {
    if (locked) return
    setEditingParameter(null)
    setParameterDialogOpen(true)
  }

  const handleEditParameter = (parameter: Parameter) => {
    if (locked) return
    setEditingParameter(parameter)
    setParameterDialogOpen(true)
  }

  const handleDeleteParameter = (id: string) => {
    if (locked) return
    setParameters((current) => current.filter((param) => param.id !== id))
    markDirty()
  }

  const handleSaveParameter = (parameter: Parameter) => {
    setParameters((current) => {
      const exists = current.some((param) => param.id === parameter.id)
      return exists
        ? current.map((param) => (param.id === parameter.id ? parameter : param))
        : [...current, parameter]
    })
    setParameterDialogOpen(false)
    markDirty()
  }

  const handleAddObjective = () => {
    if (locked) return
    setEditingObjective(null)
    setObjectiveDialogOpen(true)
  }

  const handleEditObjective = (objective: Objective) => {
    if (locked) return
    setEditingObjective(objective)
    setObjectiveDialogOpen(true)
  }

  const handleDeleteObjective = (id: string) => {
    if (locked) return
    setObjectives((current) => current.filter((objective) => objective.id !== id))
    markDirty()
  }

  const handleSaveObjective = (objective: Objective) => {
    setObjectives((current) => {
      const exists = current.some((item) => item.id === objective.id)
      return exists
        ? current.map((item) => (item.id === objective.id ? objective : item))
        : [...current, objective]
    })
    setObjectiveDialogOpen(false)
    markDirty()
  }

  const buildInputs = (): DesignSpaceInputs => ({
    parameters,
    objectives,
    constraint,
    policyBase,
  })

  // Persist the current design space. Returns the fresh view on success, or
  // null when a mapping/API error was surfaced to the user.
  const persist = async (): Promise<RunViewDto | null> => {
    if (saving || locked) return null
    const inputs = buildInputs()

    let payload:
      | { kind: 'create'; body: ReturnType<typeof toCreateBody> }
      | { kind: 'save'; body: ReturnType<typeof toDesignSpaceBody> }
    try {
      payload =
        runId === null
          ? {
              kind: 'create',
              body: toCreateBody(inputs, {
                name: meta.title,
                goal: meta.goal.trim() === '' ? null : meta.goal,
                budgetTotal: meta.budgetTotal,
              }),
            }
          : { kind: 'save', body: toDesignSpaceBody(inputs) }
    } catch (err) {
      if (err instanceof MappingError) {
        pushToast('warning', err.message)
        return null
      }
      throw err
    }

    setSaving(true)
    try {
      if (payload.kind === 'create') {
        const view = await createCampaignRun(payload.body)
        const newId = view.campaignRun.id
        writeRunIdToUrl(newId)
        setRunId(newId)
        applyView(view)
        pushToast('success', 'Campaign run created and saved.')
        return view
      }
      const result = await saveDesignSpace(runId!, payload.body)
      applyView(result.view)
      pushToast(
        result.changed ? 'success' : 'info',
        result.changed ? 'Design space saved.' : 'No changes to save.',
      )
      return result.view
    } catch (err) {
      pushToast(
        'warning',
        err instanceof ApiError ? `${err.code}: ${err.message}` : 'Failed to save.',
      )
      return null
    } finally {
      setSaving(false)
    }
  }

  const handleSave = () => {
    void persist()
  }

  const handleValidate = async () => {
    if (validating || saving || locked) return

    let targetRunId = runId
    if (dirty || runId === null) {
      const view = await persist()
      if (view === null) return
      targetRunId = view.campaignRun.id
    }
    if (targetRunId === null) return

    setValidating(true)
    try {
      const result = await validateDesignSpace(targetRunId)
      setServerStatus(result.campaignRun.status)
      setMeta((current) => ({
        ...current,
        round: result.campaignRun.round,
        budgetUsed: result.campaignRun.budgetUsed,
        budgetTotal: result.campaignRun.budgetTotal,
        batchSize: result.campaignRun.optimizationPolicy.batchSize,
      }))
      const blocking = result.validationResult.issues.filter(
        (issue) => issue.severity === 'blocking',
      )
      setBlockingIssues(blocking)
      if (result.validationResult.ok) {
        pushToast('success', 'Design space validated.')
      } else {
        pushToast(
          'warning',
          `Validation found ${blocking.length} blocking issue${blocking.length === 1 ? '' : 's'}.`,
        )
      }
    } catch (err) {
      pushToast(
        'warning',
        err instanceof ApiError ? `${err.code}: ${err.message}` : 'Validation failed.',
      )
    } finally {
      setValidating(false)
    }
  }

  const handleGenerateDesign = async () => {
    if (!canGenerate || locked || generating || runId === null) return
    setGenerating(true)
    try {
      const response = await generateInitialDesign(runId)
      setServerStatus(response.campaignRun.status)
      setMeta((current) => ({
        ...current,
        round: response.campaignRun.round,
        budgetUsed: response.campaignRun.budgetUsed,
        budgetTotal: response.campaignRun.budgetTotal,
        batchSize: response.campaignRun.optimizationPolicy.batchSize,
      }))
      setRecommendations({
        batch: response.recommendationBatch,
        round: response.experimentRound,
        experimentRuns: response.experimentRuns,
      })
      setActiveStage('recommendations')
      writeStageToUrl('recommendations')
      pushToast('success', 'Initial design generated.')
    } catch (err) {
      // Stay on the Design Space with its state intact; only surface the error.
      pushToast(
        'warning',
        err instanceof ApiError
          ? `${err.code}: ${err.message}`
          : 'Failed to generate the initial design.',
      )
    } finally {
      setGenerating(false)
    }
  }

  // Send a message to the agent. Sending never mutates the campaign; a proposed
  // action is staged as Pending for explicit approval. When no run exists yet
  // the draft is persisted first so the agent has a run to work against.
  const refreshThread = async (id: string) => {
    const thread = await getAgentThread(id)
    applyThread(thread.messages, thread.pendingProposals)
  }

  const handleSendMessage = async () => {
    if (!canSendMessage(agentDraft, agentSending)) return
    const message = agentDraft.trim()
    setAgentError(null)
    setAgentSending(true)
    try {
      let targetRunId = runId
      // Persist any unsaved local edits before the agent reads the design space,
      // so it never reasons over stale server state. A failed save aborts the
      // send rather than silently talking to the agent about the wrong campaign.
      if (targetRunId === null || dirty) {
        const view = await persist()
        if (view === null) return
        targetRunId = view.campaignRun.id
      }
      const thread = await postAgentMessage(targetRunId, message)
      applyThread(thread.messages, thread.pendingProposals)
      setAgentDraft('')
    } catch (err) {
      setAgentError(
        err instanceof ApiError ? `${err.code}: ${err.message}` : 'Failed to reach the agent.',
      )
    } finally {
      setAgentSending(false)
    }
  }

  const handleApproveProposal = async (proposalId: string) => {
    if (runId === null || agentActioningId !== null) return
    // Never let an approval silently overwrite unsaved local edits or apply a
    // patch minted against a superseded revision. The panel already disables the
    // button in these cases; this guards the programmatic path too.
    if (dirty) {
      setAgentError('Save or discard your design-space changes before approving.')
      return
    }
    const proposal = agentPendingProposals.find((p) => p.id === proposalId)
    if (proposal && isProposalStale(proposal, currentRevisionId)) {
      setAgentError('This proposal is stale; reject it and ask the agent again.')
      return
    }
    setAgentActioningId(proposalId)
    setAgentError(null)
    try {
      const response = await approveProposal(runId, proposalId)
      applyView(response.view)
      // Approving a validate proposal returns the real validation outcome:
      // "approved" is not "validation passed", so surface blocking issues.
      if (response.validationResult !== null) {
        setBlockingIssues(
          response.validationResult.issues.filter((issue) => issue.severity === 'blocking'),
        )
        pushToast(
          response.validationResult.ok ? 'success' : 'warning',
          response.validationResult.ok
            ? 'Validation passed.'
            : 'Proposal approved, but validation found blocking issues.',
        )
      }
      await refreshThread(runId)
      // A generate proposal returns a fresh batch; jump to the Recommendations
      // stage which hydrates from the real backend values (never fabricated).
      if (response.initialDesign !== null) {
        setActiveStage('recommendations')
        writeStageToUrl('recommendations')
      }
      if (response.validationResult === null) {
        pushToast('success', 'Proposal approved.')
      }
    } catch (err) {
      setAgentError(
        err instanceof ApiError ? `${err.code}: ${err.message}` : 'Failed to approve the proposal.',
      )
      // Reflect a now-resolved (Failed/stale) proposal in the thread view.
      try {
        await refreshThread(runId)
      } catch {
        // Leave the thread as-is if the refresh also fails.
      }
    } finally {
      setAgentActioningId(null)
    }
  }

  const handleRejectProposal = async (proposalId: string) => {
    if (runId === null || agentActioningId !== null) return
    setAgentActioningId(proposalId)
    setAgentError(null)
    try {
      const thread = await rejectProposal(runId, proposalId)
      applyThread(thread.messages, thread.pendingProposals)
    } catch (err) {
      setAgentError(
        err instanceof ApiError ? `${err.code}: ${err.message}` : 'Failed to reject the proposal.',
      )
    } finally {
      setAgentActioningId(null)
    }
  }

  const workspaceData = { ...campaignData, title: meta.title, goal: meta.goal }

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-slate-50 text-sm text-slate-500">
        Loading campaign run…
      </div>
    )
  }

  if (restoreError !== null) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-4 bg-slate-50 px-6 text-center">
        <div className="max-w-md">
          <h1 className="text-base font-semibold text-slate-900">Couldn’t load this campaign run</h1>
          <p className="mt-1 text-sm text-slate-600">{restoreError}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              const id = runId
              if (id !== null) loadRun(id)
            }}
            className="rounded border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
          >
            Retry
          </button>
          <button
            type="button"
            onClick={startNewDraft}
            className="rounded bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500"
          >
            Start New Draft
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-screen min-w-[1280px] flex-col bg-slate-50 text-slate-900">
      <CampaignHeader
        breadcrumb={campaignData.breadcrumb}
        title={meta.title}
        status={status}
        round={meta.round}
        budgetUsed={meta.budgetUsed}
        budgetTotal={meta.budgetTotal}
        batchSize={meta.batchSize}
        canGenerate={canGenerate}
        saving={saving}
        validating={validating}
        generating={generating}
        locked={locked}
        onSave={handleSave}
        onValidate={() => void handleValidate()}
        onGenerateDesign={() => void handleGenerateDesign()}
      />
      <div className="flex min-h-0 flex-1">
        <StageNav
          stages={stages}
          activeKey={effectiveStage}
          enabledKeys={enabledKeys}
          onSelect={handleSelectStage}
        />
        {showRecommendations ? (
          <RecommendationsView data={recommendations} parameters={parameters} />
        ) : (
          <MainWorkspace
            data={workspaceData}
            parameters={parameters}
            objectives={objectives}
            constraint={constraint}
            blockingIssues={blockingIssues}
            unsupported={unsupported}
            locked={locked}
            onAddParameter={handleAddParameter}
            onEditParameter={handleEditParameter}
            onDeleteParameter={handleDeleteParameter}
            onAddObjective={handleAddObjective}
            onEditObjective={handleEditObjective}
            onDeleteObjective={handleDeleteObjective}
            onChooseConstraint={handleChooseConstraint}
          />
        )}
        <AgentPanel
          experimentSummary={experimentSummary}
          messages={agentMessages}
          pendingProposals={agentPendingProposals}
          draft={agentDraft}
          sending={agentSending}
          actioningProposalId={agentActioningId}
          frozen={lifecycleLocked}
          dirty={dirty}
          currentRevisionId={currentRevisionId}
          errorMessage={agentError}
          onDraftChange={setAgentDraft}
          onSend={() => void handleSendMessage()}
          onApprove={(id) => void handleApproveProposal(id)}
          onReject={(id) => void handleRejectProposal(id)}
        />
      </div>

      <ParameterDialog
        open={parameterDialogOpen}
        initialParameter={editingParameter}
        existingParameters={parameters}
        onCancel={() => setParameterDialogOpen(false)}
        onSave={handleSaveParameter}
      />
      <ObjectiveDialog
        open={objectiveDialogOpen}
        initialObjective={editingObjective}
        existingObjectives={objectives}
        onCancel={() => setObjectiveDialogOpen(false)}
        onSave={handleSaveObjective}
      />
      <ToastStack toasts={toasts} onDismiss={dismissToast} />
    </div>
  )
}
