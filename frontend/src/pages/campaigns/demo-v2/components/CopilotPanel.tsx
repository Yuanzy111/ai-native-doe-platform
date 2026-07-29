import type { ReactNode } from 'react'
import type { CampaignData, ConstraintChoice, ConstraintState } from '../types'
import { getConstraintDisplayText, isConstraintResolved } from '../constraintUtils'

interface Props {
  copilot: CampaignData['copilot']
  experimentSummary: string
  constraint: ConstraintState
  onChoose: (choice: ConstraintChoice) => void
}

function CopilotBlock({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <div className="border-b border-slate-100 px-4 py-3.5 last:border-b-0">
      <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
        {label}
      </h3>
      {children}
    </div>
  )
}

export default function CopilotPanel({ copilot, experimentSummary, constraint, onChoose }: Props) {
  const resolved = isConstraintResolved(constraint)
  const constraintText = getConstraintDisplayText(constraint)
  const missingInformation = resolved ? [] : [copilot.constraintMissingInfo]
  const suggestedNextStep = resolved
    ? copilot.suggestedNextStepResolved
    : copilot.suggestedNextStepPending

  return (
    <aside className="flex w-[360px] shrink-0 flex-col border-l border-slate-200 bg-white">
      <div className="flex h-12 shrink-0 items-center border-b border-slate-200 px-4">
        <span className="h-1.5 w-1.5 rounded-full bg-indigo-500" />
        <h2 className="ml-2 text-sm font-semibold text-slate-800">AI Copilot</h2>
      </div>

      <div className="flex-1 overflow-y-auto">
        <CopilotBlock label="Experiment Summary">
          <p className="text-sm leading-relaxed text-slate-700">{experimentSummary}</p>
        </CopilotBlock>

        <CopilotBlock label="Missing Information">
          {missingInformation.length === 0 ? (
            <p className="text-sm text-slate-400">Nothing outstanding.</p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {missingInformation.map((item) => (
                <li key={item} className="flex items-start gap-2 text-sm text-slate-700">
                  <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-slate-400" />
                  {item}
                </li>
              ))}
            </ul>
          )}
        </CopilotBlock>

        <CopilotBlock label="Optional Preferences">
          <ul className="flex flex-col gap-1.5">
            {copilot.optionalPreferences.map((item) => (
              <li key={item} className="flex items-start gap-2 text-sm text-slate-700">
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-slate-400" />
                {item}
              </li>
            ))}
          </ul>
        </CopilotBlock>

        <CopilotBlock label="Suggested Next Step">
          <p className="text-sm leading-relaxed text-slate-700">{suggestedNextStep}</p>
        </CopilotBlock>

        <CopilotBlock label={resolved ? 'Resolved Constraint' : 'Pending Confirmation'}>
          {resolved ? (
            <div className="rounded border border-emerald-200 bg-emerald-50 px-3 py-2.5">
              <p className="font-mono text-sm text-emerald-900">{constraintText}</p>
              <span className="mt-1 inline-block text-[11px] font-medium uppercase tracking-wide text-emerald-600">
                Confirmed
              </span>
            </div>
          ) : (
            <>
              <div className="rounded border border-indigo-200 bg-indigo-50 px-3 py-2.5">
                <p className="text-sm text-indigo-900">{copilot.pendingConstraint}</p>
              </div>
              <div className="mt-3 flex flex-col gap-1.5">
                <button
                  type="button"
                  onClick={() => onChoose('fixed-sum')}
                  className="rounded border border-slate-300 bg-white px-3 py-1.5 text-left text-xs font-medium text-slate-700 hover:bg-slate-50"
                >
                  Yes, sum equals 100%
                </button>
                <button
                  type="button"
                  onClick={() => onChoose('no-constraint')}
                  className="rounded border border-slate-300 bg-white px-3 py-1.5 text-left text-xs font-medium text-slate-700 hover:bg-slate-50"
                >
                  No fixed-sum constraint
                </button>
                <button
                  type="button"
                  onClick={() => onChoose('custom')}
                  className="rounded border border-slate-300 bg-white px-3 py-1.5 text-left text-xs font-medium text-slate-700 hover:bg-slate-50"
                >
                  Specify another constraint
                </button>
              </div>
            </>
          )}
        </CopilotBlock>
      </div>
    </aside>
  )
}
