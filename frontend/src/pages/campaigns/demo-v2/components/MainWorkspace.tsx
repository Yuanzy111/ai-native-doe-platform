import type {
  CampaignData,
  ConstraintChoice,
  ConstraintState,
  Objective,
  Parameter,
} from '../types'
import type { ValidationIssueDto } from '../../../../api/types'
import type { UnsupportedReason } from '../../../../api/mapper'
import { getConstraintDisplayText, isConstraintResolved } from '../constraintUtils'
import { areParametersValid } from '../parameterUtils'
import { areObjectivesValid } from '../objectiveUtils'
import Section from './Section'
import ParametersTable from './ParametersTable'
import ObjectivesTable from './ObjectivesTable'

interface Props {
  data: CampaignData
  parameters: Parameter[]
  objectives: Objective[]
  constraint: ConstraintState
  blockingIssues: ValidationIssueDto[]
  unsupported: UnsupportedReason[]
  locked: boolean
  onAddParameter: () => void
  onEditParameter: (parameter: Parameter) => void
  onDeleteParameter: (id: string) => void
  onAddObjective: () => void
  onEditObjective: (objective: Objective) => void
  onDeleteObjective: (id: string) => void
  onChooseConstraint: (choice: ConstraintChoice) => void
}

export default function MainWorkspace({
  data,
  parameters,
  objectives,
  constraint,
  blockingIssues,
  unsupported,
  locked,
  onAddParameter,
  onEditParameter,
  onDeleteParameter,
  onAddObjective,
  onEditObjective,
  onDeleteObjective,
  onChooseConstraint,
}: Props) {
  const constraintResolved = isConstraintResolved(constraint)
  const constraintText = getConstraintDisplayText(constraint)
  const parametersValid = areParametersValid(parameters)
  const objectivesValid = areObjectivesValid(objectives)
  const unresolvedCount = constraintResolved ? 0 : 1

  return (
    <main className="flex-1 overflow-y-auto bg-slate-50">
      <div className="mx-auto max-w-4xl bg-white">
        {unsupported.length > 0 && (
          <div className="border-b border-amber-200 bg-amber-50 px-6 py-4">
            <p className="text-sm font-semibold text-amber-900">Unsupported configuration</p>
            <p className="mt-1 text-xs text-amber-800">
              This run uses configuration this stage cannot edit. It is shown read-only; editing,
              saving, validating, and generating are disabled to avoid overwriting the server’s
              configuration.
            </p>
            <ul className="mt-2 flex list-disc flex-col gap-1 pl-5 text-xs text-amber-800">
              {unsupported.map((reason) => (
                <li key={`${reason.area}-${reason.detail}`}>
                  <span className="font-medium uppercase tracking-wide">{reason.area}</span>:{' '}
                  {reason.detail}
                </li>
              ))}
            </ul>
          </div>
        )}

        <Section title="Campaign Goal">
          <p className="text-sm leading-relaxed text-slate-700">{data.goal}</p>
        </Section>

        <Section
          title="Parameters"
          action={
            <button
              type="button"
              onClick={onAddParameter}
              disabled={locked}
              className="rounded border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              + Add Parameter
            </button>
          }
        >
          <ParametersTable
            parameters={parameters}
            locked={locked}
            onEdit={onEditParameter}
            onDelete={onDeleteParameter}
          />
        </Section>

        <Section
          title="Objectives"
          action={
            <button
              type="button"
              onClick={onAddObjective}
              disabled={locked}
              className="rounded border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              + Add Objective
            </button>
          }
        >
          <ObjectivesTable
            objectives={objectives}
            locked={locked}
            onEdit={onEditObjective}
            onDelete={onDeleteObjective}
          />
        </Section>

        <Section title="Constraints">
          {constraintResolved && constraintText ? (
            <div className="flex items-start gap-2 rounded border border-emerald-200 bg-emerald-50 px-3 py-2.5">
              <span className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" />
              <div>
                <p className="font-mono text-sm text-emerald-900">{constraintText}</p>
                <span className="mt-1 inline-block text-[11px] font-medium uppercase tracking-wide text-emerald-600">
                  Confirmed
                </span>
              </div>
            </div>
          ) : (
            <>
              <div className="flex items-start gap-2 rounded border border-amber-200 bg-amber-50 px-3 py-2.5">
                <span className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" />
                <p className="text-sm text-amber-800">{data.openConstraintQuestion}</p>
              </div>
              {!locked && (
                <div className="mt-3 flex flex-col gap-1.5">
                  <button
                    type="button"
                    onClick={() => onChooseConstraint('fixed-sum')}
                    className="rounded border border-slate-300 bg-white px-3 py-1.5 text-left text-xs font-medium text-slate-700 hover:bg-slate-50"
                  >
                    Yes, sum equals 100%
                  </button>
                  <button
                    type="button"
                    onClick={() => onChooseConstraint('no-constraint')}
                    className="rounded border border-slate-300 bg-white px-3 py-1.5 text-left text-xs font-medium text-slate-700 hover:bg-slate-50"
                  >
                    No fixed-sum constraint
                  </button>
                </div>
              )}
            </>
          )}
        </Section>

        <Section title="Validation">
          <ul className="flex flex-col gap-1.5 text-sm text-slate-700">
            <li className="flex items-center gap-2">
              <span
                className={`h-1.5 w-1.5 rounded-full ${parametersValid ? 'bg-emerald-500' : 'bg-red-500'}`}
              />
              {parameters.length} parameters configured
              {!parametersValid && (
                <span className="text-xs text-red-600">— one or more are invalid</span>
              )}
            </li>
            <li className="flex items-center gap-2">
              <span
                className={`h-1.5 w-1.5 rounded-full ${objectivesValid ? 'bg-emerald-500' : 'bg-red-500'}`}
              />
              {objectives.length} objectives configured
              {!objectivesValid && (
                <span className="text-xs text-red-600">— one or more are invalid</span>
              )}
            </li>
            <li className="flex items-center gap-2">
              <span
                className={`h-1.5 w-1.5 rounded-full ${unresolvedCount === 0 ? 'bg-emerald-500' : 'bg-amber-500'}`}
              />
              {unresolvedCount} unresolved question{unresolvedCount === 1 ? '' : 's'}
            </li>
          </ul>

          {blockingIssues.length > 0 && (
            <div className="mt-3 flex flex-col gap-1.5 rounded border border-red-200 bg-red-50 px-3 py-2.5">
              <p className="text-[11px] font-medium uppercase tracking-wide text-red-600">
                Server validation — {blockingIssues.length} blocking issue
                {blockingIssues.length === 1 ? '' : 's'}
              </p>
              <ul className="flex flex-col gap-1 text-sm text-red-800">
                {blockingIssues.map((issue) => (
                  <li key={`${issue.code}-${issue.relatedEntityId ?? ''}-${issue.message}`}>
                    <span className="font-mono text-xs text-red-500">{issue.code}</span>{' '}
                    {issue.message}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Section>
      </div>
    </main>
  )
}
