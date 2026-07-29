import type { CampaignData, ConstraintState, Objective, Parameter } from '../types'
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
  onAddParameter: () => void
  onEditParameter: (parameter: Parameter) => void
  onDeleteParameter: (id: string) => void
  onAddObjective: () => void
  onEditObjective: (objective: Objective) => void
  onDeleteObjective: (id: string) => void
}

export default function MainWorkspace({
  data,
  parameters,
  objectives,
  constraint,
  onAddParameter,
  onEditParameter,
  onDeleteParameter,
  onAddObjective,
  onEditObjective,
  onDeleteObjective,
}: Props) {
  const constraintResolved = isConstraintResolved(constraint)
  const constraintText = getConstraintDisplayText(constraint)
  const parametersValid = areParametersValid(parameters)
  const objectivesValid = areObjectivesValid(objectives)
  const unresolvedCount = constraintResolved ? 0 : 1

  return (
    <main className="flex-1 overflow-y-auto bg-slate-50">
      <div className="mx-auto max-w-4xl bg-white">
        <Section title="Campaign Goal">
          <p className="text-sm leading-relaxed text-slate-700">{data.goal}</p>
        </Section>

        <Section
          title="Parameters"
          action={
            <button
              type="button"
              onClick={onAddParameter}
              className="rounded border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              + Add Parameter
            </button>
          }
        >
          <ParametersTable
            parameters={parameters}
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
              className="rounded border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              + Add Objective
            </button>
          }
        >
          <ObjectivesTable
            objectives={objectives}
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
            <div className="flex items-start gap-2 rounded border border-amber-200 bg-amber-50 px-3 py-2.5">
              <span className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" />
              <p className="text-sm text-amber-800">{data.openConstraintQuestion}</p>
            </div>
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
        </Section>
      </div>
    </main>
  )
}
