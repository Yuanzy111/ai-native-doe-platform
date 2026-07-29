import { describe, expect, it } from 'vitest'
import {
  DEFAULT_POLICY_BASE,
  MappingError,
  hydrateFromView,
  toDesignSpaceBody,
  type DesignSpaceInputs,
} from './mapper'
import type { Objective, Parameter, ConstraintState } from '../pages/campaigns/demo-v2/types'
import type { RunViewDto } from './types'

function continuous(id: string, name: string, lower: string, upper: string): Parameter {
  return { id, name, type: 'Continuous', unit: '', description: '', lowerBound: lower, upperBound: upper }
}

function discrete(id: string, name: string, values: string[]): Parameter {
  return { id, name, type: 'Discrete', unit: '', description: '', values }
}

function objective(id: string, name: string, direction: 'Maximize' | 'Minimize'): Objective {
  return { id, name, direction, unit: '', description: '' }
}

const RESIN = continuous('p-resin', 'Resin Ratio', '60', '85')
const HARDENER = continuous('p-hardener', 'Hardener Ratio', '15', '40')

function inputs(overrides: Partial<DesignSpaceInputs> = {}): DesignSpaceInputs {
  return {
    parameters: [RESIN, HARDENER],
    objectives: [objective('o-hardness', 'Hardness', 'Maximize')],
    constraint: { choice: 'no-constraint', customExpression: '' } satisfies ConstraintState,
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
      inputs({ constraint: { choice: 'fixed-sum', customExpression: '' } }),
    )
    expect(body.constraintsConfirmed).toBe(true)
    expect(body.constraints).toEqual([
      {
        kind: 'LinearEquality',
        id: 'constraint-fixed-sum',
        parameterIds: ['p-resin', 'p-hardener'],
        coefficients: [1, 1],
        rhs: 100,
      },
    ])
  })

  it('confirms an explicit no-constraint choice with an empty set', () => {
    const body = toDesignSpaceBody(inputs({ constraint: { choice: 'no-constraint', customExpression: '' } }))
    expect(body.constraints).toEqual([])
    expect(body.constraintsConfirmed).toBe(true)
  })

  it('leaves an unresolved constraint unconfirmed (savable, not validatable)', () => {
    const body = toDesignSpaceBody(inputs({ constraint: { choice: null, customExpression: '' } }))
    expect(body.constraints).toEqual([])
    expect(body.constraintsConfirmed).toBe(false)
  })

  it('refuses to submit a custom constraint expression', () => {
    expect(() =>
      toDesignSpaceBody(inputs({ constraint: { choice: 'custom', customExpression: 'a + b <= 1' } })),
    ).toThrow(MappingError)
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
      name: 'Hardness',
      direction: 'Maximize',
      unit: '',
      description: '',
    })
    expect(hydrated.constraint).toEqual({ choice: 'no-constraint', customExpression: '' })
    expect(hydrated.status).toBe('DesignSpaceValidated')
    expect(hydrated.batchSize).toBe(4)
    expect(hydrated.budgetTotal).toBe(12)
  })

  it('round-trips a design space through the server view', () => {
    const original = inputs({ constraint: { choice: 'fixed-sum', customExpression: '' } })
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
