import { useEffect, useState } from 'react'
import type { Objective } from '../types'
import { createEmptyObjective, getObjectiveIssues } from '../objectiveUtils'

interface Props {
  open: boolean
  initialObjective: Objective | null
  existingObjectives: Objective[]
  onCancel: () => void
  onSave: (objective: Objective) => void
}

const DIRECTIONS: Objective['direction'][] = ['Maximize', 'Minimize']

export default function ObjectiveDialog({
  open,
  initialObjective,
  existingObjectives,
  onCancel,
  onSave,
}: Props) {
  const [draft, setDraft] = useState<Objective>(
    () => initialObjective ?? createEmptyObjective(),
  )
  const [touched, setTouched] = useState(false)

  useEffect(() => {
    if (!open) return
    setDraft(initialObjective ?? createEmptyObjective())
    setTouched(false)
  }, [open, initialObjective])

  if (!open) return null

  const errors = getObjectiveIssues(draft, existingObjectives)
  const isEdit = initialObjective !== null

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
        aria-labelledby="objective-dialog-title"
        className="w-[460px] rounded border border-slate-200 bg-white shadow-lg"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="border-b border-slate-200 px-5 py-3.5">
          <h3 id="objective-dialog-title" className="text-sm font-semibold text-slate-900">
            {isEdit ? 'Edit Objective' : 'Add Objective'}
          </h3>
        </div>

        <div className="flex flex-col gap-3 px-5 py-4">
          <div>
            <label htmlFor="objective-name" className="mb-1 block text-xs font-medium text-slate-500">
              Name
            </label>
            <input
              id="objective-name"
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
              <label
                htmlFor="objective-direction"
                className="mb-1 block text-xs font-medium text-slate-500"
              >
                Direction
              </label>
              <select
                id="objective-direction"
                value={draft.direction}
                onChange={(event) =>
                  setDraft({ ...draft, direction: event.target.value as Objective['direction'] })
                }
                className="w-full rounded border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-800 focus:border-indigo-400 focus:outline-none focus:ring-1 focus:ring-indigo-400"
              >
                {DIRECTIONS.map((direction) => (
                  <option key={direction} value={direction}>
                    {direction}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex-1">
              <label
                htmlFor="objective-unit"
                className="mb-1 block text-xs font-medium text-slate-500"
              >
                Unit
              </label>
              <input
                id="objective-unit"
                type="text"
                value={draft.unit}
                onChange={(event) => setDraft({ ...draft, unit: event.target.value })}
                className="w-full rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-800 focus:border-indigo-400 focus:outline-none focus:ring-1 focus:ring-indigo-400"
              />
            </div>
          </div>

          <div>
            <label
              htmlFor="objective-description"
              className="mb-1 block text-xs font-medium text-slate-500"
            >
              Description
            </label>
            <input
              id="objective-description"
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
