import type { ConstraintState } from './types'

export function newFixedSumConstraintId(): string {
  return `constraint-fixed-sum-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

export function isConstraintResolved(constraint: ConstraintState): boolean {
  if (constraint.choice === null) return false
  if (constraint.choice === 'custom') return constraint.customExpression.trim().length > 0
  return true
}

export function getConstraintDisplayText(constraint: ConstraintState): string | null {
  switch (constraint.choice) {
    case 'fixed-sum':
      return 'Resin Ratio + Hardener Ratio = 100%'
    case 'no-constraint':
      return 'No fixed-sum composition constraint'
    case 'custom':
      return constraint.customExpression.trim() || null
    default:
      return null
  }
}
