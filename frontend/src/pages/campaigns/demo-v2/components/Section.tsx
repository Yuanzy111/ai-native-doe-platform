import type { ReactNode } from 'react'

interface Props {
  title: string
  action?: ReactNode
  children: ReactNode
}

export default function Section({ title, action, children }: Props) {
  return (
    <section className="border-b border-slate-200 px-6 py-5 last:border-b-0">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-[13px] font-semibold uppercase tracking-wide text-slate-500">
          {title}
        </h2>
        {action}
      </div>
      {children}
    </section>
  )
}
