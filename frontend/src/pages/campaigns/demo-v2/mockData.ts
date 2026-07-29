import type { CampaignData, Objective, Parameter, Stage } from './types'

export const stages: Stage[] = [
  { key: 'objective', label: 'Objective' },
  { key: 'design-space', label: 'Design Space' },
  { key: 'historical-data', label: 'Historical Data' },
  { key: 'recommendations', label: 'Recommendations' },
  { key: 'execution', label: 'Execution' },
  { key: 'analysis', label: 'Analysis' },
  { key: 'decision-log', label: 'Decision Log' },
]

export const initialParameters: Parameter[] = [
  {
    id: 'param-resin-ratio',
    name: 'Resin Ratio',
    type: 'Continuous',
    lowerBound: '60',
    upperBound: '85',
    unit: '%',
    description: '',
  },
  {
    id: 'param-hardener-ratio',
    name: 'Hardener Ratio',
    type: 'Continuous',
    lowerBound: '15',
    upperBound: '40',
    unit: '%',
    description: '',
  },
  {
    id: 'param-curing-temperature',
    name: 'Curing Temperature',
    type: 'Continuous',
    lowerBound: '80',
    upperBound: '160',
    unit: '°C',
    description: '',
  },
  {
    id: 'param-curing-time',
    name: 'Curing Time',
    type: 'Continuous',
    lowerBound: '20',
    upperBound: '120',
    unit: 'min',
    description: '',
  },
]

export const initialObjectives: Objective[] = [
  {
    id: 'objective-hardness',
    name: 'Hardness',
    direction: 'Maximize',
    unit: '',
    description: '',
  },
  {
    id: 'objective-brittleness',
    name: 'Brittleness',
    direction: 'Minimize',
    unit: '',
    description: '',
  },
  {
    id: 'objective-cost',
    name: 'Cost',
    direction: 'Minimize',
    unit: '',
    description: '',
  },
  {
    id: 'objective-actual-curing-time',
    name: 'Actual Curing Time',
    direction: 'Minimize',
    unit: '',
    description: '',
  },
]

export const campaignData: CampaignData = {
  breadcrumb: ['Projects', 'Coating Optimization'],
  title: 'Epoxy Coating Optimization',
  status: 'Draft',
  round: 0,
  budgetUsed: 0,
  budgetTotal: 12,
  batchSize: 4,
  goal: '优化环氧涂层配方,希望硬度高、不易脆、固化快、成本低。',
  openConstraintQuestion:
    'Should resin and hardener ratios be normalized or satisfy a fixed-sum constraint?',
  copilot: {
    experimentSummary:
      '4 个连续参数、4 个优化目标已配置完成,尚未生成首轮实验推荐。',
    constraintMissingInfo: 'Resin / Hardener 是否存在配比约束尚未确认',
    optionalPreferences: ['未指定各目标的相对权重或偏好排序'],
    suggestedNextStepPending: '请先确认 Resin / Hardener 配比约束,再继续下一步。',
    suggestedNextStepResolved:
      'Design space validation passed. Generate the initial experiment design when ready.',
    pendingConstraint: 'Resin Ratio + Hardener Ratio 是否应固定为 100%?',
  },
}
