import type { Parameter, ParameterFieldErrors, ParameterType } from './types'

export function createEmptyParameter(type: ParameterType): Parameter {
  const id = `param-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  const base = { id, name: '', unit: '', description: '' }

  if (type === 'Continuous') {
    return { ...base, type, lowerBound: '', upperBound: '' }
  }
  return { ...base, type, values: [] }
}

export function getParameterIssues(
  param: Parameter,
  allParameters: Parameter[],
): ParameterFieldErrors {
  const errors: ParameterFieldErrors = {}

  const trimmedName = param.name.trim()
  if (!trimmedName) {
    errors.name = 'Name is required.'
  } else {
    const isDuplicate = allParameters.some(
      (other) =>
        other.id !== param.id &&
        other.name.trim().toLowerCase() === trimmedName.toLowerCase(),
    )
    if (isDuplicate) errors.name = 'Name must be unique.'
  }

  if (param.type === 'Continuous') {
    const lower = Number(param.lowerBound)
    const upper = Number(param.upperBound)
    if (param.lowerBound.trim() === '' || param.upperBound.trim() === '') {
      errors.bounds = 'Lower and upper bound are required.'
    } else if (Number.isNaN(lower) || Number.isNaN(upper)) {
      errors.bounds = 'Bounds must be numbers.'
    } else if (lower >= upper) {
      errors.bounds = 'Lower bound must be less than upper bound.'
    }
  } else {
    const nonEmptyValues = param.values.filter((value) => value.trim() !== '')
    if (nonEmptyValues.length === 0) {
      errors.values = 'At least one value is required.'
    }
  }

  return errors
}

export function isParameterValid(param: Parameter, allParameters: Parameter[]): boolean {
  const errors = getParameterIssues(param, allParameters)
  return Object.keys(errors).length === 0
}

export function areParametersValid(parameters: Parameter[]): boolean {
  if (parameters.length === 0) return false
  return parameters.every((param) => isParameterValid(param, parameters))
}

export function formatParameterRange(param: Parameter): string {
  if (param.type === 'Continuous') {
    if (param.lowerBound.trim() === '' || param.upperBound.trim() === '') return '—'
    return `${param.lowerBound} – ${param.upperBound}`
  }
  const nonEmptyValues = param.values.filter((value) => value.trim() !== '')
  return nonEmptyValues.length > 0 ? nonEmptyValues.join(', ') : '—'
}
