import type { Objective } from '../types'
import { isObjectiveValid } from '../objectiveUtils'

interface Props {
  objectives: Objective[]
  locked: boolean
  onEdit: (objective: Objective) => void
  onDelete: (id: string) => void
}

const directionStyles: Record<Objective['direction'], string> = {
  Maximize: 'bg-emerald-50 text-emerald-700',
  Minimize: 'bg-sky-50 text-sky-700',
}

export default function ObjectivesTable({ objectives, locked, onEdit, onDelete }: Props) {
  if (objectives.length === 0) {
    return <p className="text-sm text-slate-400">No objectives configured yet.</p>
  }

  const canDelete = objectives.length > 1 && !locked

  return (
    <table className="w-full border-collapse text-left text-sm">
      <thead>
        <tr className="border-b border-slate-200 text-[12px] uppercase tracking-wide text-slate-400">
          <th className="py-1.5 pr-3 font-medium">Name</th>
          <th className="py-1.5 pr-3 font-medium">Direction</th>
          <th className="py-1.5 pr-3 font-medium">Unit</th>
          <th className="py-1.5 font-medium" />
        </tr>
      </thead>
      <tbody>
        {objectives.map((obj) => {
          const valid = isObjectiveValid(obj, objectives)
          return (
            <tr key={obj.id} className="border-b border-slate-100 last:border-b-0">
              <td className="py-1.5 pr-3 font-medium text-slate-800">
                <span className="flex items-center gap-1.5">
                  {!valid && (
                    <span
                      title="This objective has a validation issue."
                      className="h-1.5 w-1.5 shrink-0 rounded-full bg-red-500"
                    />
                  )}
                  {obj.name || <span className="italic text-slate-400">Unnamed</span>}
                </span>
              </td>
              <td className="py-1.5 pr-3">
                <span
                  className={`rounded px-1.5 py-0.5 text-[12px] font-medium ${directionStyles[obj.direction]}`}
                >
                  {obj.direction}
                </span>
              </td>
              <td className="py-1.5 pr-3 text-slate-600">{obj.unit || '—'}</td>
              <td className="py-1.5 text-right">
                <button
                  type="button"
                  disabled={locked}
                  onClick={() => onEdit(obj)}
                  className="mr-2 text-xs font-medium text-indigo-600 hover:text-indigo-500 disabled:cursor-not-allowed disabled:text-slate-300 disabled:hover:text-slate-300"
                >
                  Edit
                </button>
                <button
                  type="button"
                  disabled={!canDelete}
                  title={canDelete ? undefined : 'At least one objective must remain.'}
                  onClick={() => onDelete(obj.id)}
                  className="text-xs font-medium text-slate-500 hover:text-red-600 disabled:cursor-not-allowed disabled:text-slate-300 disabled:hover:text-slate-300"
                >
                  Delete
                </button>
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
