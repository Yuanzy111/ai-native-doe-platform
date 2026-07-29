import type { CampaignData } from '../types'

interface Props {
  data: CampaignData
  readyToGenerate: boolean
  onValidate: () => void
  onGenerateDesign: () => void
}

const statusStyles: Record<CampaignData['status'], string> = {
  Draft: 'bg-amber-50 text-amber-700 border-amber-200',
  Active: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  Completed: 'bg-slate-100 text-slate-600 border-slate-200',
}

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-1.5 whitespace-nowrap">
      <span className="text-slate-400">{label}</span>
      <span className="font-medium text-slate-700">{value}</span>
    </div>
  )
}

export default function CampaignHeader({
  data,
  readyToGenerate,
  onValidate,
  onGenerateDesign,
}: Props) {
  return (
    <header className="flex h-16 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-5">
      <div className="flex min-w-0 flex-col gap-1">
        <nav className="flex items-center gap-1.5 text-xs text-slate-400">
          {data.breadcrumb.map((crumb, i) => (
            <span key={crumb} className="flex items-center gap-1.5">
              {i > 0 && <span className="text-slate-300">/</span>}
              <span>{crumb}</span>
            </span>
          ))}
        </nav>
        <div className="flex items-center gap-3">
          <h1 className="truncate text-base font-semibold text-slate-900">
            {data.title}
          </h1>
          <span
            className={`rounded border px-1.5 py-0.5 text-[11px] font-medium leading-none ${statusStyles[data.status]}`}
          >
            {data.status}
          </span>
        </div>
      </div>

      <div className="flex items-center gap-5 text-xs">
        <MetaItem label="Round" value={String(data.round)} />
        <MetaItem
          label="Budget"
          value={`${data.budgetUsed} / ${data.budgetTotal}`}
        />
        <MetaItem label="Batch Size" value={String(data.batchSize)} />

        <div className="ml-2 flex items-center gap-2">
          <button
            type="button"
            className="rounded border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
          >
            Save
          </button>
          <button
            type="button"
            onClick={onValidate}
            className="rounded border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
          >
            Validate
          </button>
          <span
            title={
              readyToGenerate ? undefined : 'Resolve pending design-space questions first'
            }
          >
            <button
              type="button"
              disabled={!readyToGenerate}
              onClick={onGenerateDesign}
              className="rounded bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400"
            >
              Generate Initial Design
            </button>
          </span>
        </div>
      </div>
    </header>
  )
}
