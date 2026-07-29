import type { Objective, ObjectiveFieldErrors } from './types'

export function createEmptyObjective(): Objective {
  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  return {
    id: `objective-${suffix}`,
    outputId: `output-${suffix}`,
    targetId: `target-${suffix}`,
    name: '',
    direction: 'Maximize',
    unit: '',
    description: '',
  }
}

export function getObjectiveIssues(
  objective: Objective,
  allObjectives: Objective[],
): ObjectiveFieldErrors {
  const errors: ObjectiveFieldErrors = {}

  const trimmedName = objective.name.trim()
  if (!trimmedName) {
    errors.name = 'Name is required.'
  } else {
    const isDuplicate = allObjectives.some(
      (other) =>
        other.id !== objective.id &&
        other.name.trim().toLowerCase() === trimmedName.toLowerCase(),
    )
    if (isDuplicate) errors.name = 'Name must be unique.'
  }

  return errors
}

export function isObjectiveValid(objective: Objective, allObjectives: Objective[]): boolean {
  const errors = getObjectiveIssues(objective, allObjectives)
  return Object.keys(errors).length === 0
}

export function areObjectivesValid(objectives: Objective[]): boolean {
  if (objectives.length === 0) return false
  return objectives.every((objective) => isObjectiveValid(objective, objectives))
}
