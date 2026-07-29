interface Props {
  breadcrumb: string[]
  title: string
  status: string
  round: number
  budgetUsed: number
  budgetTotal: number
  batchSize: number
  canGenerate: boolean
  saving: boolean
  validating: boolean
  onSave: () => void
  onValidate: () => void
  onGenerateDesign: () => void
}

const statusStyles: Record<string, string> = {
  Draft: 'bg-amber-50 text-amber-700 border-amber-200',
  DesignSpaceValidated: 'bg-emerald-50 text-emerald-700 border-emerald-200',
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
  breadcrumb,
  title,
  status,
  round,
  budgetUsed,
  budgetTotal,
  batchSize,
  canGenerate,
  saving,
  validating,
  onSave,
  onValidate,
  onGenerateDesign,
}: Props) {
  const statusClass = statusStyles[status] ?? 'bg-slate-100 text-slate-600 border-slate-200'
  const busy = saving || validating

  return (
    <header className="flex h-16 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-5">
      <div className="flex min-w-0 flex-col gap-1">
        <nav className="flex items-center gap-1.5 text-xs text-slate-400">
          {breadcrumb.map((crumb, i) => (
            <span key={crumb} className="flex items-center gap-1.5">
              {i > 0 && <span className="text-slate-300">/</span>}
              <span>{crumb}</span>
            </span>
          ))}
        </nav>
        <div className="flex items-center gap-3">
          <h1 className="truncate text-base font-semibold text-slate-900">{title}</h1>
          <span
            className={`rounded border px-1.5 py-0.5 text-[11px] font-medium leading-none ${statusClass}`}
          >
            {status}
          </span>
        </div>
      </div>

      <div className="flex items-center gap-5 text-xs">
        <MetaItem label="Round" value={String(round)} />
        <MetaItem label="Budget" value={`${budgetUsed} / ${budgetTotal}`} />
        <MetaItem label="Batch Size" value={String(batchSize)} />

        <div className="ml-2 flex items-center gap-2">
          <button
            type="button"
            onClick={onSave}
            disabled={busy}
            className="rounded border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
          <button
            type="button"
            onClick={onValidate}
            disabled={busy}
            className="rounded border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {validating ? 'Validating…' : 'Validate'}
          </button>
          <span
            title={
              canGenerate
                ? undefined
                : 'Validate the design space (with no unsaved edits) first'
            }
          >
            <button
              type="button"
              disabled={!canGenerate || busy}
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
