export type StageKey =
  | 'objective'
  | 'design-space'
  | 'historical-data'
  | 'recommendations'
  | 'execution'
  | 'analysis'
  | 'decision-log'

export interface Stage {
  key: StageKey
  label: string
}

export type ParameterType = 'Continuous' | 'Discrete' | 'Categorical'

interface ParameterBase {
  id: string
  name: string
  unit: string
  description: string
}

export interface ContinuousParameter extends ParameterBase {
  type: 'Continuous'
  lowerBound: string
  upperBound: string
}

export interface ValuesParameter extends ParameterBase {
  type: 'Discrete' | 'Categorical'
  values: string[]
}

export type Parameter = ContinuousParameter | ValuesParameter

export interface ParameterFieldErrors {
  name?: string
  bounds?: string
  values?: string
}

export interface Objective {
  // Frontend row identity (React keys, dedup, edit/delete). Distinct from the
  // server-side outputId/targetId so a hydrate -> save round-trip never rewrites
  // the IDs the backend assigned.
  id: string
  outputId: string
  targetId: string
  name: string
  direction: 'Maximize' | 'Minimize'
  unit: string
  description: string
}

export interface ObjectiveFieldErrors {
  name?: string
}

export type ConstraintChoice = 'fixed-sum' | 'no-constraint'

export interface ConstraintState {
  choice: ConstraintChoice | null
  // Internal mapping bookkeeping, never shown in the UI: preserved from the
  // server so a hydrate -> save round-trip returns the original fixed-sum
  // constraint id and resolvedAt untouched instead of rewriting them.
  constraintId?: string
  resolvedAt?: string | null
}

export interface CampaignData {
  breadcrumb: string[]
  title: string
  status: 'Draft' | 'Active' | 'Completed'
  round: number
  budgetUsed: number
  budgetTotal: number
  batchSize: number
  goal: string
  openConstraintQuestion: string
  copilot: {
    experimentSummary: string
    constraintMissingInfo: string
    optionalPreferences: string[]
    suggestedNextStepPending: string
    suggestedNextStepResolved: string
    pendingConstraint: string
  }
}

export type ToastVariant = 'success' | 'warning' | 'info'

export interface Toast {
  id: string
  variant: ToastVariant
  message: string
}
