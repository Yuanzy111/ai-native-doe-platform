import { useEffect, useState } from 'react'

interface Props {
  open: boolean
  onCancel: () => void
  onConfirm: (expression: string) => void
}

export default function ConstraintDialog({ open, onCancel, onConfirm }: Props) {
  const [expression, setExpression] = useState('')

  useEffect(() => {
    if (open) setExpression('')
  }, [open])

  useEffect(() => {
    if (!open) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [open, onCancel])

  if (!open) return null

  const canConfirm = expression.trim().length > 0

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40"
      onClick={onCancel}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="constraint-dialog-title"
        className="w-[420px] rounded border border-slate-200 bg-white shadow-lg"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="border-b border-slate-200 px-5 py-3.5">
          <h3 id="constraint-dialog-title" className="text-sm font-semibold text-slate-900">
            Specify Another Constraint
          </h3>
        </div>

        <div className="px-5 py-4">
          <label
            htmlFor="constraint-expression"
            className="mb-1.5 block text-xs font-medium text-slate-500"
          >
            Expression
          </label>
          <input
            id="constraint-expression"
            type="text"
            autoFocus
            value={expression}
            onChange={(event) => setExpression(event.target.value)}
            placeholder="e.g. Resin Ratio + Hardener Ratio <= 95"
            className="w-full rounded border border-slate-300 px-3 py-2 text-sm text-slate-800 placeholder:text-slate-400 focus:border-indigo-400 focus:outline-none focus:ring-1 focus:ring-indigo-400"
          />
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
            disabled={!canConfirm}
            onClick={() => onConfirm(expression.trim())}
            className="rounded bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400"
          >
            Confirm
          </button>
        </div>
      </div>
    </div>
  )
}
