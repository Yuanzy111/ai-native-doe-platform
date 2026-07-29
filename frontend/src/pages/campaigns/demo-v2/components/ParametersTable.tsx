import type { Parameter } from '../types'
import { formatParameterRange, isParameterValid } from '../parameterUtils'

interface Props {
  parameters: Parameter[]
  locked: boolean
  onEdit: (parameter: Parameter) => void
  onDelete: (id: string) => void
}

export default function ParametersTable({ parameters, locked, onEdit, onDelete }: Props) {
  if (parameters.length === 0) {
    return <p className="text-sm text-slate-400">No parameters configured yet.</p>
  }

  return (
    <table className="w-full border-collapse text-left text-sm">
      <thead>
        <tr className="border-b border-slate-200 text-[12px] uppercase tracking-wide text-slate-400">
          <th className="py-1.5 pr-3 font-medium">Name</th>
          <th className="py-1.5 pr-3 font-medium">Type</th>
          <th className="py-1.5 pr-3 font-medium">Range / Values</th>
          <th className="py-1.5 pr-3 font-medium">Unit</th>
          <th className="py-1.5 font-medium" />
        </tr>
      </thead>
      <tbody>
        {parameters.map((param) => {
          const valid = isParameterValid(param, parameters)
          return (
            <tr key={param.id} className="border-b border-slate-100 last:border-b-0">
              <td className="py-1.5 pr-3 font-medium text-slate-800">
                <span className="flex items-center gap-1.5">
                  {!valid && (
                    <span
                      title="This parameter has a validation issue."
                      className="h-1.5 w-1.5 shrink-0 rounded-full bg-red-500"
                    />
                  )}
                  {param.name || (
                    <span className="italic text-slate-400">Unnamed</span>
                  )}
                </span>
              </td>
              <td className="py-1.5 pr-3 text-slate-600">{param.type}</td>
              <td className="py-1.5 pr-3 font-mono text-[13px] text-slate-600">
                {formatParameterRange(param)}
              </td>
              <td className="py-1.5 pr-3 text-slate-600">{param.unit || '—'}</td>
              <td className="py-1.5 text-right">
                <button
                  type="button"
                  disabled={locked}
                  onClick={() => onEdit(param)}
                  className="mr-2 text-xs font-medium text-indigo-600 hover:text-indigo-500 disabled:cursor-not-allowed disabled:text-slate-300 disabled:hover:text-slate-300"
                >
                  Edit
                </button>
                <button
                  type="button"
                  disabled={locked}
                  onClick={() => onDelete(param.id)}
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
