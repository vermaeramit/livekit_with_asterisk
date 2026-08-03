import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react'
import { cn } from '@/lib/utils'

type ToastKind = 'success' | 'error' | 'warning' | 'info'

interface Toast {
  id: number
  kind: ToastKind
  title: string
  description?: string
}

interface ToastApi {
  show: (t: Omit<Toast, 'id'>) => void
  success: (title: string, description?: string) => void
  error: (title: string, description?: string) => void
  warning: (title: string, description?: string) => void
  info: (title: string, description?: string) => void
}

const ToastContext = createContext<ToastApi | null>(null)

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used inside <ToastProvider>')
  return ctx
}

const styles: Record<ToastKind, { ring: string; icon: React.ComponentType<{ className?: string }>; color: string }> = {
  success: { ring: 'ring-success/30', icon: CheckCircle2, color: 'text-success' },
  error: { ring: 'ring-danger/30', icon: XCircle, color: 'text-danger' },
  warning: { ring: 'ring-warning/30', icon: AlertTriangle, color: 'text-warning' },
  info: { ring: 'ring-primary/30', icon: Info, color: 'text-primary' },
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(1)

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const api = useMemo<ToastApi>(() => {
    const show = (t: Omit<Toast, 'id'>) => {
      const id = nextId.current++
      setToasts((prev) => [...prev, { ...t, id }])
      // errors stay long enough to read and copy; the rest get out of the way
      window.setTimeout(() => dismiss(id), t.kind === 'error' ? 8000 : 4000)
    }
    return {
      show,
      success: (title, description) => show({ kind: 'success', title, description }),
      error: (title, description) => show({ kind: 'error', title, description }),
      warning: (title, description) => show({ kind: 'warning', title, description }),
      info: (title, description) => show({ kind: 'info', title, description }),
    }
  }, [dismiss])

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-full max-w-sm flex-col gap-2"
        role="region"
        aria-label="Notifications"
      >
        {toasts.map((t) => {
          const s = styles[t.kind]
          const Icon = s.icon
          return (
            <div
              key={t.id}
              role="alert"
              className={cn(
                'pointer-events-auto flex animate-slide-in items-start gap-3 rounded-lg border border-border',
                'bg-card p-3 shadow-lg ring-1',
                s.ring,
              )}
            >
              <Icon className={cn('mt-0.5 h-4 w-4 shrink-0', s.color)} />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{t.title}</p>
                {t.description && (
                  <p className="mt-0.5 break-words text-xs text-muted-foreground">{t.description}</p>
                )}
              </div>
              <button
                onClick={() => dismiss(t.id)}
                className="rounded p-0.5 text-muted-foreground transition-colors hover:text-foreground"
                aria-label="Dismiss"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          )
        })}
      </div>
    </ToastContext.Provider>
  )
}
