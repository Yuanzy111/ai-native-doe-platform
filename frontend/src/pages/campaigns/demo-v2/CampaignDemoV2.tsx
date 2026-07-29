import { useState } from 'react'
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
import { isConstraintResolved } from './constraintUtils'
import { areParametersValid } from './parameterUtils'
import { areObjectivesValid } from './objectiveUtils'
import type { ConstraintChoice, ConstraintState, Objective, Parameter } from './types'

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
  const { toasts, pushToast, dismissToast } = useToasts()

  const constraintResolved = isConstraintResolved(constraint)
  const parametersValid = areParametersValid(parameters)
  const objectivesValid = areObjectivesValid(objectives)
  const readyToGenerate = constraintResolved && parametersValid && objectivesValid
  const experimentSummary = `${parameters.length} 个参数、${objectives.length} 个优化目标已配置完成,尚未生成首轮实验推荐。`

  const handleChooseConstraint = (choice: ConstraintChoice) => {
    if (choice === 'custom') {
      setConstraintDialogOpen(true)
      return
    }
    setConstraint({ choice, customExpression: '' })
  }

  const handleConstraintDialogConfirm = (expression: string) => {
    setConstraint({ choice: 'custom', customExpression: expression })
    setConstraintDialogOpen(false)
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
  }

  const handleSaveParameter = (parameter: Parameter) => {
    setParameters((current) => {
      const exists = current.some((param) => param.id === parameter.id)
      return exists
        ? current.map((param) => (param.id === parameter.id ? parameter : param))
        : [...current, parameter]
    })
    setParameterDialogOpen(false)
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
  }

  const handleSaveObjective = (objective: Objective) => {
    setObjectives((current) => {
      const exists = current.some((item) => item.id === objective.id)
      return exists
        ? current.map((item) => (item.id === objective.id ? objective : item))
        : [...current, objective]
    })
    setObjectiveDialogOpen(false)
  }

  const handleValidate = () => {
    if (readyToGenerate) {
      pushToast('success', 'Validation passed. Design space is fully specified.')
      return
    }
    if (!parametersValid) {
      pushToast('warning', 'One or more parameters are invalid. Fix them before validating.')
      return
    }
    if (!objectivesValid) {
      pushToast('warning', 'One or more objectives are invalid. Fix them before validating.')
      return
    }
    pushToast('warning', 'Unresolved question: confirm the resin/hardener constraint first.')
  }

  const handleGenerateDesign = () => {
    if (!readyToGenerate) return
    pushToast('info', 'Design space is ready. Optimization backend is not connected yet.')
  }

  return (
    <div className="flex h-screen min-w-[1280px] flex-col bg-slate-50 text-slate-900">
      <CampaignHeader
        data={campaignData}
        readyToGenerate={readyToGenerate}
        onValidate={handleValidate}
        onGenerateDesign={handleGenerateDesign}
      />
      <div className="flex min-h-0 flex-1">
        <StageNav stages={stages} activeKey="design-space" />
        <MainWorkspace
          data={campaignData}
          parameters={parameters}
          objectives={objectives}
          constraint={constraint}
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
