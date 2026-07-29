import { useEffect, useState } from 'react'
import type { Parameter, ParameterType } from '../types'
import { createEmptyParameter, getParameterIssues } from '../parameterUtils'

interface Props {
  open: boolean
  initialParameter: Parameter | null
  existingParameters: Parameter[]
  onCancel: () => void
  onSave: (parameter: Parameter) => void
}

const PARAMETER_TYPES: ParameterType[] = ['Continuous', 'Discrete', 'Categorical']

function switchType(param: Parameter, type: ParameterType): Parameter {
  const base = { id: param.id, name: param.name, unit: param.unit, description: param.description }
  if (type === 'Continuous') {
    return { ...base, type, lowerBound: '', upperBound: '' }
  }
  return { ...base, type, values: [] }
}

export default function ParameterDialog({
  open,
  initialParameter,
  existingParameters,
  onCancel,
  onSave,
}: Props) {
  const [draft, setDraft] = useState<Parameter>(
    () => initialParameter ?? createEmptyParameter('Continuous'),
  )
  const [valuesText, setValuesText] = useState('')
  const [touched, setTouched] = useState(false)

  useEffect(() => {
    if (!open) return
    const next = initialParameter ?? createEmptyParameter('Continuous')
    setDraft(next)
    setValuesText(next.type !== 'Continuous' ? next.values.join(', ') : '')
    setTouched(false)
  }, [open, initialParameter])

  if (!open) return null

  const errors = getParameterIssues(draft, existingParameters)
  const isEdit = initialParameter !== null

  const handleTypeChange = (type: ParameterType) => {
    setDraft((current) => switchType(current, type))
    setValuesText('')
  }

  const handleValuesTextChange = (text: string) => {
    setValuesText(text)
    if (draft.type === 'Continuous') return
    const values = text
      .split(',')
      .map((value) => value.trim())
      .filter((value) => value !== '')
    setDraft({ ...draft, values })
  }

  const handleSave = () => {
    setTouched(true)
    if (Object.keys(errors).length > 0) return
    onSave(draft)
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40"
      onClick={onCancel}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="parameter-dialog-title"
        className="w-[460px] rounded border border-slate-200 bg-white shadow-lg"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="border-b border-slate-200 px-5 py-3.5">
          <h3 id="parameter-dialog-title" className="text-sm font-semibold text-slate-900">
            {isEdit ? 'Edit Parameter' : 'Add Parameter'}
          </h3>
        </div>

        <div className="flex flex-col gap-3 px-5 py-4">
          <div>
            <label htmlFor="param-name" className="mb-1 block text-xs font-medium text-slate-500">
              Name
            </label>
            <input
              id="param-name"
              type="text"
              autoFocus
              value={draft.name}
              onChange={(event) => setDraft({ ...draft, name: event.target.value })}
              className="w-full rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-800 focus:border-indigo-400 focus:outline-none focus:ring-1 focus:ring-indigo-400"
            />
            {touched && errors.name && (
              <p className="mt-1 text-xs text-red-600">{errors.name}</p>
            )}
          </div>

          <div className="flex gap-3">
            <div className="flex-1">
              <label htmlFor="param-type" className="mb-1 block text-xs font-medium text-slate-500">
                Type
              </label>
              <select
                id="param-type"
                value={draft.type}
                onChange={(event) => handleTypeChange(event.target.value as ParameterType)}
                className="w-full rounded border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-800 focus:border-indigo-400 focus:outline-none focus:ring-1 focus:ring-indigo-400"
              >
                {PARAMETER_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex-1">
              <label htmlFor="param-unit" className="mb-1 block text-xs font-medium text-slate-500">
                Unit
              </label>
              <input
                id="param-unit"
                type="text"
                value={draft.unit}
                onChange={(event) => setDraft({ ...draft, unit: event.target.value })}
                className="w-full rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-800 focus:border-indigo-400 focus:outline-none focus:ring-1 focus:ring-indigo-400"
              />
            </div>
          </div>

          {draft.type === 'Continuous' ? (
            <div className="flex gap-3">
              <div className="flex-1">
                <label
                  htmlFor="param-lower"
                  className="mb-1 block text-xs font-medium text-slate-500"
                >
                  Lower Bound
                </label>
                <input
                  id="param-lower"
                  type="text"
                  value={draft.lowerBound}
                  onChange={(event) => setDraft({ ...draft, lowerBound: event.target.value })}
                  className="w-full rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-800 focus:border-indigo-400 focus:outline-none focus:ring-1 focus:ring-indigo-400"
                />
              </div>
              <div className="flex-1">
                <label
                  htmlFor="param-upper"
                  className="mb-1 block text-xs font-medium text-slate-500"
                >
                  Upper Bound
                </label>
                <input
                  id="param-upper"
                  type="text"
                  value={draft.upperBound}
                  onChange={(event) => setDraft({ ...draft, upperBound: event.target.value })}
                  className="w-full rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-800 focus:border-indigo-400 focus:outline-none focus:ring-1 focus:ring-indigo-400"
                />
              </div>
              {touched && errors.bounds && (
                <p className="mt-1 basis-full text-xs text-red-600">{errors.bounds}</p>
              )}
            </div>
          ) : (
            <div>
              <label htmlFor="param-values" className="mb-1 block text-xs font-medium text-slate-500">
                Values (comma-separated)
              </label>
              <input
                id="param-values"
                type="text"
                value={valuesText}
                onChange={(event) => handleValuesTextChange(event.target.value)}
                placeholder="e.g. Red, Green, Blue"
                className="w-full rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-800 placeholder:text-slate-400 focus:border-indigo-400 focus:outline-none focus:ring-1 focus:ring-indigo-400"
              />
              {touched && errors.values && (
                <p className="mt-1 text-xs text-red-600">{errors.values}</p>
              )}
            </div>
          )}

          <div>
            <label
              htmlFor="param-description"
              className="mb-1 block text-xs font-medium text-slate-500"
            >
              Description
            </label>
            <input
              id="param-description"
              type="text"
              value={draft.description}
              onChange={(event) => setDraft({ ...draft, description: event.target.value })}
              className="w-full rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-800 focus:border-indigo-400 focus:outline-none focus:ring-1 focus:ring-indigo-400"
            />
          </div>
        </div>

        <div className="flex justify-end gap-2 border-t border-slate-200 px-5 py-3.5">
          <button
            type="button"
            onClick={onCancel}
            className="rounded border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSave}
            className="rounded bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  )
}
