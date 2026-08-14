import { forwardRef } from 'react'
import { cn } from '@/lib/utils'

// ── input ───────────────────────────────────────────────────────────────────

export const Input = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        'flex h-9 w-full rounded-md border border-input bg-card px-3 text-sm shadow-xs',
        'transition-shadow placeholder:text-muted-foreground',
        'focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20',
        'disabled:cursor-not-allowed disabled:opacity-50',
        // Set by the field wrappers when the server rejects this one, so the
        // bad field is findable in a long form without reading the message.
        'aria-[invalid=true]:border-danger aria-[invalid=true]:ring-2 aria-[invalid=true]:ring-danger/20',
        className,
      )}
      {...props}
    />
  ),
)
Input.displayName = 'Input'

// ── select ──────────────────────────────────────────────────────────────────

export const Select = forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...props }, ref) => (
    <select
      ref={ref}
      className={cn(
        'flex h-9 w-full rounded-md border border-input bg-card px-2.5 text-sm shadow-xs',
        'transition-shadow',
        'focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20',
        'disabled:cursor-not-allowed disabled:opacity-50',
        'aria-[invalid=true]:border-danger aria-[invalid=true]:ring-2 aria-[invalid=true]:ring-danger/20',
        className,
      )}
      {...props}
    >
      {children}
    </select>
  ),
)
Select.displayName = 'Select'

// ── label ───────────────────────────────────────────────────────────────────

export function Label({ className, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label
      className={cn('block text-xs font-medium text-muted-foreground', className)}
      {...props}
    />
  )
}

// ── card ────────────────────────────────────────────────────────────────────

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'rounded-lg border border-border bg-card text-card-foreground shadow-sm',
        className,
      )}
      {...props}
    />
  )
}

export function CardHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('flex items-center justify-between border-b border-border px-4 py-3', className)}
      {...props}
    />
  )
}

export function CardTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={cn('text-sm font-semibold tracking-tight', className)} {...props} />
}

export function CardBody({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('p-4', className)} {...props} />
}

// ── badge ───────────────────────────────────────────────────────────────────

type Tone = 'default' | 'muted' | 'success' | 'warning' | 'danger' | 'info'

const tones: Record<Tone, string> = {
  default: 'bg-secondary text-secondary-foreground ring-1 ring-inset ring-border',
  muted: 'bg-muted text-muted-foreground',
  success: 'bg-success/10 text-success ring-1 ring-inset ring-success/25',
  warning: 'bg-warning/10 text-warning ring-1 ring-inset ring-warning/25',
  danger: 'bg-danger/10 text-danger ring-1 ring-inset ring-danger/25',
  info: 'bg-primary/10 text-primary ring-1 ring-inset ring-primary/25',
}

export function Badge({
  tone = 'default',
  className,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: Tone }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-2xs font-medium',
        tones[tone],
        className,
      )}
      {...props}
    />
  )
}

// ── skeleton ────────────────────────────────────────────────────────────────

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-md bg-muted', className)} />
}

// ── empty state ─────────────────────────────────────────────────────────────

export function EmptyState({
  icon: Icon,
  title,
  hint,
  action,
}: {
  icon: React.ComponentType<{ className?: string }>
  title: string
  hint?: string
  action?: React.ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-20 text-center">
      <div className="rounded-full border border-border bg-muted/60 p-3.5">
        <Icon className="h-5 w-5 text-muted-foreground" />
      </div>
      <div>
        <p className="text-sm font-medium">{title}</p>
        {hint && <p className="mx-auto mt-1 max-w-sm text-xs text-muted-foreground">{hint}</p>}
      </div>
      {action}
    </div>
  )
}
