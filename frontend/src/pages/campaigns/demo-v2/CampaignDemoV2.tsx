import { useEffect, useRef, useState } from 'react'
import CampaignHeader from './components/CampaignHeader'
import StageNav from './components/StageNav'
import MainWorkspace from './components/MainWorkspace'
import CopilotPanel from './components/CopilotPanel'
import ConstraintDialog from './components/ConstraintDialog'
import ParameterDialog from './components/ParameterDialog'
import ObjectiveDialog from './components/ObjectiveDialog'
import ToastStack from './components/ToastStack'
import { campaignData, initialObjectives, initialParameters, stages } from './mockData'
import { useToasts } from './useToasts'
import { canGenerateInitialDesign, displayStatus } from './runState'
import type { ConstraintChoice, ConstraintState, Objective, Parameter } from './types'
import { ApiError } from '../../../api/client'
import {
  createCampaignRun,
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
} from '../../../api/mapper'
import type { RunStatus, RunViewDto, ValidationIssueDto } from '../../../api/types'

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

export default function CampaignDemoV2() {
  const [parameters, setParameters] = useState<Parameter[]>(initialParameters)
  const [editingParameter, setEditingParameter] = useState<Parameter | null>(null)
  const [parameterDialogOpen, setParameterDialogOpen] = useState(false)

  const [objectives, setObjectives] = useState<Objective[]>(initialObjectives)
  const [editingObjective, setEditingObjective] = useState<Objective | null>(null)
  const [objectiveDialogOpen, setObjectiveDialogOpen] = useState(false)

  const [constraint, setConstraint] = useState<ConstraintState>({
    choice: null,
    customExpression: '',
  })
  const [constraintDialogOpen, setConstraintDialogOpen] = useState(false)

  const [runId, setRunId] = useState<string | null>(null)
  const [serverStatus, setServerStatus] = useState<RunStatus | null>(null)
  const [policyBase, setPolicyBase] = useState<PolicyBase>(DEFAULT_POLICY_BASE)
  const [meta, setMeta] = useState<HeaderMeta>(DEFAULT_META)
  const [dirty, setDirty] = useState(false)
  const [blockingIssues, setBlockingIssues] = useState<ValidationIssueDto[]>([])

  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [validating, setValidating] = useState(false)

  const { toasts, pushToast, dismissToast } = useToasts()

  const applyView = (view: RunViewDto) => {
    const hydrated = hydrateFromView(view)
    setParameters(hydrated.parameters)
    setObjectives(hydrated.objectives)
    setConstraint(hydrated.constraint)
    setPolicyBase(hydrated.policyBase)
    setServerStatus(hydrated.status)
    setMeta({
      title: hydrated.title,
      goal: hydrated.goal,
      round: hydrated.round,
      budgetUsed: hydrated.budgetUsed,
      budgetTotal: hydrated.budgetTotal,
      batchSize: hydrated.batchSize,
    })
    setDirty(false)
    setBlockingIssues([])
  }

  // Restore an existing run from `?runId` on first load.
  const restored = useRef(false)
  useEffect(() => {
    if (restored.current) return
    restored.current = true
    const existingId = readRunIdFromUrl()
    if (existingId === null) return

    setRunId(existingId)
    setLoading(true)
    getCampaignRun(existingId)
      .then(applyView)
      .catch((err: unknown) => {
        const message =
          err instanceof ApiError
            ? `Could not restore run: ${err.message}`
            : 'Could not restore the campaign run.'
        pushToast('warning', message)
      })
      .finally(() => setLoading(false))
  }, [pushToast])

  const markDirty = () => {
    setDirty(true)
    setBlockingIssues([])
  }

  const experimentSummary = `${parameters.length} 个参数、${objectives.length} 个优化目标已配置完成,尚未生成首轮实验推荐。`
  const status = displayStatus(serverStatus, dirty)
  const canGenerate = canGenerateInitialDesign(serverStatus, dirty)

  const handleChooseConstraint = (choice: ConstraintChoice) => {
    if (choice === 'custom') {
      setConstraintDialogOpen(true)
      return
    }
    setConstraint({ choice, customExpression: '' })
    markDirty()
  }

  const handleConstraintDialogConfirm = (expression: string) => {
    setConstraint({ choice: 'custom', customExpression: expression })
    setConstraintDialogOpen(false)
    markDirty()
  }

  const handleAddParameter = () => {
    setEditingParameter(null)
    setParameterDialogOpen(true)
  }

  const handleEditParameter = (parameter: Parameter) => {
    setEditingParameter(parameter)
    setParameterDialogOpen(true)
  }

  const handleDeleteParameter = (id: string) => {
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
    setEditingObjective(null)
    setObjectiveDialogOpen(true)
  }

  const handleEditObjective = (objective: Objective) => {
    setEditingObjective(objective)
    setObjectiveDialogOpen(true)
  }

  const handleDeleteObjective = (id: string) => {
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
    if (saving) return null
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
    if (validating || saving) return

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

  const handleGenerateDesign = () => {
    if (!canGenerate) return
    pushToast('info', 'Initial design generation is not wired up in this stage.')
  }

  const workspaceData = { ...campaignData, title: meta.title, goal: meta.goal }

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-slate-50 text-sm text-slate-500">
        Loading campaign run…
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
        onSave={handleSave}
        onValidate={() => void handleValidate()}
        onGenerateDesign={handleGenerateDesign}
      />
      <div className="flex min-h-0 flex-1">
        <StageNav stages={stages} activeKey="design-space" />
        <MainWorkspace
          data={workspaceData}
          parameters={parameters}
          objectives={objectives}
          constraint={constraint}
          blockingIssues={blockingIssues}
          onAddParameter={handleAddParameter}
          onEditParameter={handleEditParameter}
          onDeleteParameter={handleDeleteParameter}
          onAddObjective={handleAddObjective}
          onEditObjective={handleEditObjective}
          onDeleteObjective={handleDeleteObjective}
        />
        <CopilotPanel
          copilot={campaignData.copilot}
          experimentSummary={experimentSummary}
          constraint={constraint}
          onChoose={handleChooseConstraint}
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
      <ConstraintDialog
        open={constraintDialogOpen}
        onCancel={() => setConstraintDialogOpen(false)}
        onConfirm={handleConstraintDialogConfirm}
      />
      <ToastStack toasts={toasts} onDismiss={dismissToast} />
    </div>
  )
}
