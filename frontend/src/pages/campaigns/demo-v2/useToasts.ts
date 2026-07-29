import { useCallback, useRef, useState } from 'react'
import type { Toast, ToastVariant } from './types'

const TOAST_DURATION_MS = 4000

export function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(0)

  const dismissToast = useCallback((id: string) => {
    setToasts((current) => current.filter((toast) => toast.id !== id))
  }, [])

  const pushToast = useCallback(
    (variant: ToastVariant, message: string) => {
      const id = `toast-${nextId.current++}`
      setToasts((current) => [...current, { id, variant, message }])
      setTimeout(() => dismissToast(id), TOAST_DURATION_MS)
    },
    [dismissToast],
  )

  return { toasts, pushToast, dismissToast }
}
