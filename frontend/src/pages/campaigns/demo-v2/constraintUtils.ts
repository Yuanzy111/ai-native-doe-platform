import type { ConstraintState } from './types'

export function newFixedSumConstraintId(): string {
  return `constraint-fixed-sum-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

export function isConstraintResolved(constraint: ConstraintState): boolean {
  return constraint.choice !== null
}

export function getConstraintDisplayText(constraint: ConstraintState): string | null {
  switch (constraint.choice) {
    case 'fixed-sum':
      return 'Resin Ratio + Hardener Ratio = 100%'
    case 'no-constraint':
      return 'No fixed-sum composition constraint'
    default:
      return null
  }
}
