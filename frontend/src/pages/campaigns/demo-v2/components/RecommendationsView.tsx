import type { Parameter } from '../types'
import type { ExperimentRunStatus } from '../../../../api/types'
import { buildRecommendationRows, type RecommendationsData } from '../recommendationsState'
import Section from './Section'

interface Props {
  data: RecommendationsData
  parameters: Parameter[]
}

const statusStyles: Record<ExperimentRunStatus, string> = {
  Pending: 'bg-amber-50 text-amber-700 border-amber-200',
  Completed: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  Failed: 'bg-red-50 text-red-700 border-red-200',
  Cancelled: 'bg-slate-100 text-slate-500 border-slate-200',
}

function formatTimestamp(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] font-medium uppercase tracking-wide text-slate-400">{label}</span>
      <span className="text-sm font-medium text-slate-700">{value}</span>
    </div>
  )
}

export default function RecommendationsView({ data, parameters }: Props) {
  const { batch } = data
  const config = batch.algorithmConfig
  const rows = buildRecommendationRows(batch, data.experimentRuns, parameters)

  return (
    <main className="flex-1 overflow-y-auto bg-slate-50">
      <div className="mx-auto max-w-4xl bg-white">
        <Section title="Recommendation Batch">
          <div className="grid grid-cols-3 gap-4">
            <MetaItem label="Round" value={String(batch.roundNumber)} />
            <MetaItem label="Generated" value={formatTimestamp(batch.generatedAt)} />
            <MetaItem label="Batch Status" value={batch.status} />
            <MetaItem
              label="Backend"
              value={`${config.backendName} ${config.backendVersion}`}
            />
            <MetaItem label="Seed" value={String(config.seed)} />
            <MetaItem label="Acquisition" value={config.acquisitionFunction} />
          </div>
        </Section>

        <Section title="Recommended Experiments">
          {rows.length === 0 ? (
            <p className="text-sm text-slate-400">This batch has no candidates.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-[12px] uppercase tracking-wide text-slate-400">
                    <th className="py-1.5 pr-3 font-medium">#</th>
                    {parameters.map((param) => (
                      <th key={param.id} className="py-1.5 pr-3 font-medium">
                        {param.name}
                        {param.unit && (
                          <span className="ml-1 font-normal normal-case text-slate-300">
                            ({param.unit})
                          </span>
                        )}
                      </th>
                    ))}
                    <th className="py-1.5 pr-3 font-medium">Pred. Mean</th>
                    <th className="py-1.5 pr-3 font-medium">Pred. SD</th>
                    <th className="py-1.5 pr-3 font-medium">Desirability</th>
                    <th className="py-1.5 pr-3 font-medium">Experiment</th>
                    <th className="py-1.5 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.candidateId} className="border-b border-slate-100 last:border-b-0">
                      <td className="py-1.5 pr-3 text-slate-400">{row.position}</td>
                      {row.cells.map((cell) => (
                        <td key={cell.paramId} className="py-1.5 pr-3 font-mono text-[13px] text-slate-700">
                          {cell.value}
                        </td>
                      ))}
                      <td className="py-1.5 pr-3 text-slate-500">{row.predictedMean}</td>
                      <td className="py-1.5 pr-3 text-slate-500">{row.predictedSd}</td>
                      <td className="py-1.5 pr-3 text-slate-500">{row.desirability}</td>
                      <td className="py-1.5 pr-3 font-mono text-[12px] text-slate-500">
                        {row.experimentId ?? '—'}
                      </td>
                      <td className="py-1.5">
                        {row.experimentStatus === null ? (
                          <span className="text-slate-400">—</span>
                        ) : (
                          <span
                            className={`rounded border px-1.5 py-0.5 text-[11px] font-medium leading-none ${statusStyles[row.experimentStatus]}`}
                          >
                            {row.experimentStatus}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      </div>
    </main>
  )
}
