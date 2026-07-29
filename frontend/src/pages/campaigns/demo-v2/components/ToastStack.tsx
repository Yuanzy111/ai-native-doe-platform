import type { Toast, ToastVariant } from '../types'

interface Props {
  toasts: Toast[]
  onDismiss: (id: string) => void
}

const variantStyles: Record<ToastVariant, string> = {
  success: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  warning: 'border-amber-200 bg-amber-50 text-amber-800',
  info: 'border-indigo-200 bg-indigo-50 text-indigo-800',
}

export default function ToastStack({ toasts, onDismiss }: Props) {
  if (toasts.length === 0) return null

  return (
    <div className="fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          role="status"
          className={`flex items-start justify-between gap-3 rounded border px-3 py-2.5 text-sm shadow-sm ${variantStyles[toast.variant]}`}
        >
          <p className="leading-relaxed">{toast.message}</p>
          <button
            type="button"
            aria-label="Dismiss"
            onClick={() => onDismiss(toast.id)}
            className="shrink-0 text-current opacity-60 hover:opacity-100"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  )
}
