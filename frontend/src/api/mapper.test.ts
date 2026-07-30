import { describe, expect, it } from 'vitest'
import {
  DEFAULT_POLICY_BASE,
  MappingError,
  assessSupport,
  hydrateFromView,
  toDesignSpaceBody,
  type DesignSpaceInputs,
} from './mapper'
import type { Objective, Parameter, ConstraintState } from '../pages/campaigns/demo-v2/types'
import type {
  ConstraintSpecDto,
  ObjectivePolicyDto,
  RunViewDto,
  StrategyConfigDto,
} from './types'

function continuous(id: string, name: string, lower: string, upper: string): Parameter {
  return { id, name, type: 'Continuous', unit: '', description: '', lowerBound: lower, upperBound: upper }
}

function discrete(id: string, name: string, values: string[]): Parameter {
  return { id, name, type: 'Discrete', unit: '', description: '', values }
}

function objective(id: string, name: string, direction: 'Maximize' | 'Minimize'): Objective {
  return { id, outputId: id, targetId: id, name, direction, unit: '', description: '' }
}

const RESIN = continuous('p-resin', 'Resin Ratio', '60', '85')
const HARDENER = continuous('p-hardener', 'Hardener Ratio', '15', '40')

function inputs(overrides: Partial<DesignSpaceInputs> = {}): DesignSpaceInputs {
  return {
    parameters: [RESIN, HARDENER],
    objectives: [objective('o-hardness', 'Hardness', 'Maximize')],
    constraint: { choice: 'no-constraint' } satisfies ConstraintState,
    policyBase: DEFAULT_POLICY_BASE,
    ...overrides,
  }
}

describe('toDesignSpaceBody', () => {
  it('converts continuous bounds to numbers', () => {
    const body = toDesignSpaceBody(inputs())
    expect(body.parameters[0]).toMatchObject({
      type: 'Continuous',
      id: 'p-resin',
      bounds: { lower: 60, upper: 85 },
    })
  })

  it('maps a single objective to Single + qLogEI', () => {
    const body = toDesignSpaceBody(inputs())
    expect(body.objectivePolicy).toEqual({ kind: 'Single', targetId: 'o-hardness' })
    expect(body.outputs).toEqual([
      expect.objectContaining({ id: 'o-hardness', name: 'Hardness' }),
    ])
    expect(body.targets).toEqual([
      { id: 'o-hardness', outputId: 'o-hardness', direction: 'Maximize' },
    ])
    expect(body.optimizationPolicy.strategyConfig).toMatchObject({
      kind: 'TwoPhaseMeta',
      acquisitionFunction: 'qLogEI',
    })
  })

  it('maps multiple objectives to Pareto + qLogNEHVI', () => {
    const body = toDesignSpaceBody(
      inputs({
        objectives: [
          objective('o-hardness', 'Hardness', 'Maximize'),
          objective('o-cost', 'Cost', 'Minimize'),
        ],
      }),
    )
    expect(body.objectivePolicy).toEqual({
      kind: 'Pareto',
      targetIds: ['o-hardness', 'o-cost'],
    })
    expect(body.optimizationPolicy.strategyConfig.acquisitionFunction).toBe('qLogNEHVI')
  })

  it('maps a fixed-sum constraint to a LinearEquality over resin + hardener', () => {
    const body = toDesignSpaceBody(
      inputs({ constraint: { choice: 'fixed-sum' } }),
    )
    expect(body.constraintsConfirmed).toBe(true)
    expect(body.constraints).toEqual([
      {
        kind: 'LinearEquality',
        id: 'constraint-fixed-sum',
        resolvedAt: null,
        parameterIds: ['p-resin', 'p-hardener'],
        coefficients: [1, 1],
        rhs: 100,
      },
    ])
  })

  it('confirms an explicit no-constraint choice with an empty set', () => {
    const body = toDesignSpaceBody(inputs({ constraint: { choice: 'no-constraint' } }))
    expect(body.constraints).toEqual([])
    expect(body.constraintsConfirmed).toBe(true)
  })

  it('leaves an unresolved constraint unconfirmed (savable, not validatable)', () => {
    const body = toDesignSpaceBody(inputs({ constraint: { choice: null } }))
    expect(body.constraints).toEqual([])
    expect(body.constraintsConfirmed).toBe(false)
  })

  it('parses finite discrete values', () => {
    const body = toDesignSpaceBody(
      inputs({ parameters: [discrete('p-lvl', 'Levels', ['1', '2.5', '3'])] }),
    )
    expect(body.parameters[0]).toMatchObject({ type: 'Discrete', values: [1, 2.5, 3] })
  })

  it('blocks non-numeric discrete values', () => {
    expect(() =>
      toDesignSpaceBody(inputs({ parameters: [discrete('p-lvl', 'Levels', ['1', 'abc'])] })),
    ).toThrow(MappingError)
  })

  it('blocks non-finite continuous bounds', () => {
    expect(() =>
      toDesignSpaceBody(inputs({ parameters: [continuous('p-x', 'X', '', '10'), HARDENER] })),
    ).toThrow(MappingError)
  })

  it('requires at least one objective', () => {
    expect(() => toDesignSpaceBody(inputs({ objectives: [] }))).toThrow(MappingError)
  })

  it('omits empty unit and description', () => {
    const body = toDesignSpaceBody(inputs())
    expect(body.parameters[0]).not.toHaveProperty('unit')
    expect(body.parameters[0]).not.toHaveProperty('description')
  })
})

describe('hydrateFromView', () => {
  const view: RunViewDto = {
    campaignDefinition: {
      id: 'def-1',
      name: 'Epoxy Coating Optimization',
      goal: 'make it hard',
      headRevisionId: 'rev-1',
      createdAt: '2026-01-01T00:00:00Z',
      createdBy: 'web-user',
      updatedAt: '2026-01-01T00:00:00Z',
    },
    pinnedRevision: {
      id: 'rev-1',
      campaignDefinitionId: 'def-1',
      revisionNumber: 1,
      parentRevisionId: null,
      parameters: [
        {
          type: 'Continuous',
          id: 'p-resin',
          name: 'Resin Ratio',
          unit: '%',
          description: null,
          bounds: { lower: 60, upper: 85, stepsize: null },
        },
      ],
      outputs: [{ id: 'o-hardness', name: 'Hardness', unit: null, description: null }],
      targets: [
        { id: 'o-hardness', outputId: 'o-hardness', direction: 'Maximize', targetValue: null },
      ],
      objectivePolicy: { kind: 'Single', targetId: 'o-hardness' },
      constraints: [],
      constraintsConfirmed: true,
      constraintsConfirmedAt: '2026-01-01T00:00:00Z',
      createdAt: '2026-01-01T00:00:00Z',
      createdBy: 'web-user',
    },
    campaignRun: {
      id: 'run-1',
      campaignDefinitionId: 'def-1',
      definitionRevisionId: 'rev-1',
      status: 'DesignSpaceValidated',
      optimizationPolicy: {
        id: 'pol-1',
        backendName: 'baybe',
        batchSize: 4,
        seedPolicy: 'Fixed',
        seedValue: 42,
        strategyConfig: {
          kind: 'TwoPhaseMeta',
          initialRecommender: 'RandomRecommender',
          switchAfter: 4,
          remainSwitched: true,
          acquisitionFunction: 'qLogEI',
        },
      },
      round: 0,
      budgetTotal: 12,
      budgetUsed: 0,
      createdAt: '2026-01-01T00:00:00Z',
      updatedAt: '2026-01-01T00:00:00Z',
      createdBy: 'web-user',
    },
    recommendationBatches: [],
    experimentRounds: [],
    experimentRuns: [],
    measurements: [],
    decisionLogs: [],
  }

  it('rebuilds UI parameters, objectives, and header meta', () => {
    const hydrated = hydrateFromView(view)
    expect(hydrated.parameters[0]).toMatchObject({
      id: 'p-resin',
      type: 'Continuous',
      lowerBound: '60',
      upperBound: '85',
      unit: '%',
    })
    expect(hydrated.objectives[0]).toEqual({
      id: 'o-hardness',
      outputId: 'o-hardness',
      targetId: 'o-hardness',
      name: 'Hardness',
      direction: 'Maximize',
      unit: '',
      description: '',
    })
    expect(hydrated.constraint).toEqual({ choice: 'no-constraint' })
    expect(hydrated.status).toBe('DesignSpaceValidated')
    expect(hydrated.batchSize).toBe(4)
    expect(hydrated.budgetTotal).toBe(12)
  })

  it('round-trips a design space through the server view', () => {
    const original = inputs({ constraint: { choice: 'fixed-sum' } })
    const body = toDesignSpaceBody(original)
    // Echo the request back into a view as the server would after a save.
    const echoed: RunViewDto = {
      ...view,
      pinnedRevision: {
        ...view.pinnedRevision,
        parameters: body.parameters.map((p) =>
          p.type === 'Continuous'
            ? { ...p, unit: p.unit ?? null, description: p.description ?? null, bounds: { ...p.bounds, stepsize: null } }
            : { ...p, unit: p.unit ?? null, description: p.description ?? null },
        ),
        outputs: body.outputs.map((o) => ({ ...o, unit: o.unit ?? null, description: o.description ?? null })),
        targets: body.targets.map((t) => ({ ...t, targetValue: null })),
        objectivePolicy: body.objectivePolicy,
        constraints: body.constraints.map((c) => ({ ...c, resolvedAt: null })),
        constraintsConfirmed: body.constraintsConfirmed,
      },
    }
    const hydrated = hydrateFromView(echoed)
    expect(hydrated.parameters.map((p) => p.id)).toEqual(['p-resin', 'p-hardener'])
    expect(hydrated.objectives.map((o) => o.id)).toEqual(['o-hardness'])
    expect(hydrated.constraint.choice).toBe('fixed-sum')
  })
})

// --- view builder for round-trip and support tests -------------------------

interface ViewParts {
  parameters?: RunViewDto['pinnedRevision']['parameters']
  outputs?: RunViewDto['pinnedRevision']['outputs']
  targets?: RunViewDto['pinnedRevision']['targets']
  objectivePolicy?: ObjectivePolicyDto
  constraints?: ConstraintSpecDto[]
  constraintsConfirmed?: boolean
  strategyConfig?: StrategyConfigDto
}

const TWO_PHASE_QLOGEI: StrategyConfigDto = {
  kind: 'TwoPhaseMeta',
  initialRecommender: 'RandomRecommender',
  switchAfter: 4,
  remainSwitched: true,
  acquisitionFunction: 'qLogEI',
}

function buildView(parts: ViewParts = {}): RunViewDto {
  const strategyConfig = parts.strategyConfig ?? TWO_PHASE_QLOGEI
  return {
    campaignDefinition: {
      id: 'def-1',
      name: 'Round-trip Campaign',
      goal: null,
      headRevisionId: 'rev-1',
      createdAt: '2026-01-01T00:00:00Z',
      createdBy: 'web-user',
      updatedAt: '2026-01-01T00:00:00Z',
    },
    pinnedRevision: {
      id: 'rev-1',
      campaignDefinitionId: 'def-1',
      revisionNumber: 1,
      parentRevisionId: null,
      parameters: parts.parameters ?? [
        {
          type: 'Continuous',
          id: 'p-resin',
          name: 'Resin Ratio',
          unit: '%',
          description: null,
          bounds: { lower: 60, upper: 85, stepsize: null },
        },
        {
          type: 'Continuous',
          id: 'p-hardener',
          name: 'Hardener Ratio',
          unit: '%',
          description: null,
          bounds: { lower: 15, upper: 40, stepsize: null },
        },
      ],
      outputs: parts.outputs ?? [
        { id: 'out-h', name: 'Hardness', unit: null, description: null },
      ],
      targets: parts.targets ?? [
        { id: 'tgt-h', outputId: 'out-h', direction: 'Maximize', targetValue: null },
      ],
      objectivePolicy: parts.objectivePolicy ?? { kind: 'Single', targetId: 'tgt-h' },
      constraints: parts.constraints ?? [],
      constraintsConfirmed: parts.constraintsConfirmed ?? true,
      constraintsConfirmedAt: '2026-01-01T00:00:00Z',
      createdAt: '2026-01-01T00:00:00Z',
      createdBy: 'web-user',
    },
    campaignRun: {
      id: 'run-1',
      campaignDefinitionId: 'def-1',
      definitionRevisionId: 'rev-1',
      status: 'DesignSpaceValidated',
      optimizationPolicy: {
        id: 'pol-1',
        backendName: 'baybe',
        batchSize: 4,
        seedPolicy: 'Fixed',
        seedValue: 42,
        strategyConfig,
      },
      round: 0,
      budgetTotal: 12,
      budgetUsed: 0,
      createdAt: '2026-01-01T00:00:00Z',
      updatedAt: '2026-01-01T00:00:00Z',
      createdBy: 'web-user',
    },
    recommendationBatches: [],
    experimentRounds: [],
    experimentRuns: [],
    measurements: [],
    decisionLogs: [],
  }
}

const FIXED_SUM: ConstraintSpecDto = {
  kind: 'LinearEquality',
  id: 'constraint-fixed-sum',
  resolvedAt: null,
  parameterIds: ['p-resin', 'p-hardener'],
  coefficients: [1, 1],
  rhs: 100,
}

describe('output/target id preservation', () => {
  it('keeps distinct outputId and targetId through a save', () => {
    const obj: Objective = {
      id: 'row-1',
      outputId: 'out-xyz',
      targetId: 'tgt-abc',
      name: 'Hardness',
      direction: 'Maximize',
      unit: '',
      description: '',
    }
    const body = toDesignSpaceBody(inputs({ objectives: [obj] }))
    expect(body.outputs).toEqual([{ id: 'out-xyz', name: 'Hardness' }])
    expect(body.targets).toEqual([
      { id: 'tgt-abc', outputId: 'out-xyz', direction: 'Maximize' },
    ])
    expect(body.objectivePolicy).toEqual({ kind: 'Single', targetId: 'tgt-abc' })
  })

  it('hydrates outputId and targetId separately when the server differs', () => {
    const view = buildView({
      outputs: [{ id: 'out-xyz', name: 'Hardness', unit: null, description: null }],
      targets: [{ id: 'tgt-abc', outputId: 'out-xyz', direction: 'Maximize', targetValue: null }],
      objectivePolicy: { kind: 'Single', targetId: 'tgt-abc' },
    })
    const hydrated = hydrateFromView(view)
    expect(hydrated.objectives[0]).toMatchObject({
      outputId: 'out-xyz',
      targetId: 'tgt-abc',
    })
    expect(hydrated.unsupported).toEqual([])
  })
})

describe('lossless GET -> (no edit) -> PUT round-trip', () => {
  it('reproduces parameters, outputs, targets, objectivePolicy and optimizationPolicy', () => {
    const view = buildView()
    const body = toDesignSpaceBody(hydrateFromView(view))

    expect(body.parameters).toEqual([
      { type: 'Continuous', id: 'p-resin', name: 'Resin Ratio', unit: '%', bounds: { lower: 60, upper: 85 } },
      { type: 'Continuous', id: 'p-hardener', name: 'Hardener Ratio', unit: '%', bounds: { lower: 15, upper: 40 } },
    ])
    expect(body.outputs).toEqual([{ id: 'out-h', name: 'Hardness' }])
    expect(body.targets).toEqual([{ id: 'tgt-h', outputId: 'out-h', direction: 'Maximize' }])
    expect(body.objectivePolicy).toEqual({ kind: 'Single', targetId: 'tgt-h' })
    expect(body.optimizationPolicy).toEqual({
      backendName: 'baybe',
      batchSize: 4,
      seedPolicy: 'Fixed',
      seedValue: 42,
      strategyConfig: {
        kind: 'TwoPhaseMeta',
        initialRecommender: 'RandomRecommender',
        switchAfter: 4,
        remainSwitched: true,
        acquisitionFunction: 'qLogEI',
      },
    })
  })

  it('round-trips a confirmed fixed-sum constraint unchanged', () => {
    const view = buildView({ constraints: [FIXED_SUM], constraintsConfirmed: true })
    const hydrated = hydrateFromView(view)
    expect(hydrated.unsupported).toEqual([])
    expect(hydrated.constraint.choice).toBe('fixed-sum')
    const body = toDesignSpaceBody(hydrated)
    expect(body.constraints).toEqual([
      {
        kind: 'LinearEquality',
        id: 'constraint-fixed-sum',
        resolvedAt: null,
        parameterIds: ['p-resin', 'p-hardener'],
        coefficients: [1, 1],
        rhs: 100,
      },
    ])
    expect(body.constraintsConfirmed).toBe(true)
  })

  it('preserves an arbitrary fixed-sum id and non-null resolvedAt through a save', () => {
    const view = buildView({
      constraints: [
        {
          ...FIXED_SUM,
          id: 'srv-generated-99',
          resolvedAt: '2026-02-03T10:00:00Z',
        },
      ],
      constraintsConfirmed: true,
    })
    const hydrated = hydrateFromView(view)
    expect(hydrated.unsupported).toEqual([])
    expect(hydrated.constraint).toMatchObject({
      choice: 'fixed-sum',
      constraintId: 'srv-generated-99',
      resolvedAt: '2026-02-03T10:00:00Z',
    })
    const body = toDesignSpaceBody(hydrated)
    expect(body.constraints).toEqual([
      {
        kind: 'LinearEquality',
        id: 'srv-generated-99',
        resolvedAt: '2026-02-03T10:00:00Z',
        parameterIds: ['p-resin', 'p-hardener'],
        coefficients: [1, 1],
        rhs: 100,
      },
    ])
  })
})

describe('assessSupport flags configurations this stage cannot express', () => {
  it('accepts a plain supported run', () => {
    expect(assessSupport(buildView())).toEqual([])
  })

  it('flags a Desirability objective policy', () => {
    const reasons = assessSupport(
      buildView({ objectivePolicy: { kind: 'Desirability' } as ObjectivePolicyDto }),
    )
    expect(reasons.some((r) => r.area === 'objective')).toBe(true)
  })

  it('flags a Botorch strategy', () => {
    const reasons = assessSupport(
      buildView({ strategyConfig: { kind: 'Botorch', acquisitionFunction: 'qLogEI' } }),
    )
    expect(reasons.some((r) => r.area === 'strategy')).toBe(true)
  })

  it('flags a LinearInequality constraint', () => {
    const reasons = assessSupport(
      buildView({
        constraints: [{ kind: 'LinearInequality' } as ConstraintSpecDto],
      }),
    )
    expect(reasons.some((r) => r.area === 'constraint')).toBe(true)
  })

  it('flags a Cardinality constraint', () => {
    const reasons = assessSupport(
      buildView({ constraints: [{ kind: 'Cardinality' } as ConstraintSpecDto] }),
    )
    expect(reasons.some((r) => r.area === 'constraint')).toBe(true)
  })

  it('flags a fixed-sum over the wrong parameters', () => {
    const reasons = assessSupport(
      buildView({
        constraints: [{ ...FIXED_SUM, parameterIds: ['p-resin', 'p-other'] }],
      }),
    )
    expect(reasons.some((r) => r.area === 'constraint')).toBe(true)
  })

  it('flags a fixed-sum with a non-unit rhs', () => {
    const reasons = assessSupport(
      buildView({ constraints: [{ ...FIXED_SUM, rhs: 90 }] }),
    )
    expect(reasons.some((r) => r.area === 'constraint')).toBe(true)
  })

  it('flags a continuous bound stepsize', () => {
    const reasons = assessSupport(
      buildView({
        parameters: [
          {
            type: 'Continuous',
            id: 'p-resin',
            name: 'Resin Ratio',
            unit: '%',
            description: null,
            bounds: { lower: 60, upper: 85, stepsize: 5 },
          },
        ],
      }),
    )
    expect(reasons.some((r) => r.area === 'parameter')).toBe(true)
  })

  it('flags an acquisition function that does not match the objective count', () => {
    const reasons = assessSupport(
      buildView({ strategyConfig: { ...TWO_PHASE_QLOGEI, acquisitionFunction: 'qLogNEHVI' } }),
    )
    expect(reasons.some((r) => r.area === 'strategy')).toBe(true)
  })

  it('leaves an unsupported constraint unresolved rather than calling it no-constraint', () => {
    const hydrated = hydrateFromView(
      buildView({ constraints: [{ kind: 'LinearInequality' } as ConstraintSpecDto] }),
    )
    expect(hydrated.constraint.choice).toBeNull()
    expect(hydrated.unsupported.some((r) => r.area === 'constraint')).toBe(true)
  })

  it('flags outputs and targets stored in a different order', () => {
    const reasons = assessSupport(
      buildView({
        outputs: [
          { id: 'out-a', name: 'A', unit: null, description: null },
          { id: 'out-b', name: 'B', unit: null, description: null },
        ],
        targets: [
          { id: 'tgt-b', outputId: 'out-b', direction: 'Maximize', targetValue: null },
          { id: 'tgt-a', outputId: 'out-a', direction: 'Minimize', targetValue: null },
        ],
        objectivePolicy: { kind: 'Pareto', targetIds: ['tgt-b', 'tgt-a'] },
        strategyConfig: { ...TWO_PHASE_QLOGEI, acquisitionFunction: 'qLogNEHVI' },
      }),
    )
    expect(reasons.some((r) => r.area === 'objective')).toBe(true)
  })

  it('flags an output that no target references', () => {
    const reasons = assessSupport(
      buildView({
        outputs: [
          { id: 'out-h', name: 'Hardness', unit: null, description: null },
          { id: 'out-orphan', name: 'Orphan', unit: null, description: null },
        ],
      }),
    )
    expect(
      reasons.some((r) => r.area === 'objective' && r.detail.includes('out-orphan')),
    ).toBe(true)
  })

  it('flags a target that references a missing output', () => {
    const reasons = assessSupport(
      buildView({
        targets: [{ id: 'tgt-h', outputId: 'out-missing', direction: 'Maximize', targetValue: null }],
        objectivePolicy: { kind: 'Single', targetId: 'tgt-h' },
      }),
    )
    expect(
      reasons.some((r) => r.area === 'objective' && r.detail.includes('out-missing')),
    ).toBe(true)
  })
})
