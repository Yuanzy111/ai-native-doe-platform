import type { Stage, StageKey } from '../types'

interface Props {
  stages: Stage[]
  activeKey: StageKey
  enabledKeys: StageKey[]
  onSelect: (key: StageKey) => void
}

export default function StageNav({ stages, activeKey, enabledKeys, onSelect }: Props) {
  return (
    <nav className="w-52 shrink-0 border-r border-slate-200 bg-white py-3">
      <ul className="flex flex-col gap-0.5">
        {stages.map((stage) => {
          const isActive = stage.key === activeKey
          const isEnabled = enabledKeys.includes(stage.key)
          return (
            <li key={stage.key}>
              <button
                type="button"
                disabled={!isEnabled}
                aria-current={isActive ? 'page' : undefined}
                onClick={() => onSelect(stage.key)}
                className={`flex w-full items-center border-l-2 px-4 py-2 text-left text-sm transition-colors ${
                  isActive
                    ? 'border-indigo-600 bg-indigo-50 font-medium text-indigo-700'
                    : isEnabled
                      ? 'border-transparent text-slate-600 hover:bg-slate-50 hover:text-slate-900'
                      : 'border-transparent text-slate-300 cursor-not-allowed'
                }`}
              >
                {stage.label}
              </button>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}
