import { Input, Label, Select } from '@/components/ui/primitives'
import { cn } from '@/lib/utils'

export function Field({
  label,
  hint,
  htmlFor,
  children,
  className,
}: {
  label: string
  hint?: React.ReactNode
  htmlFor?: string
  children: React.ReactNode
  className?: string
}) {
  return (
    <div className={cn('space-y-1.5', className)}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {hint && <p className="text-2xs leading-relaxed text-muted-foreground">{hint}</p>}
    </div>
  )
}

export function TextField({
  label,
  hint,
  value,
  onChange,
  ...props
}: {
  label: string
  hint?: React.ReactNode
  value: string
  onChange: (v: string) => void
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange'>) {
  const id = props.id ?? `f-${label.replace(/\W+/g, '-').toLowerCase()}`
  return (
    <Field label={label} hint={hint} htmlFor={id}>
      <Input id={id} value={value} onChange={(e) => onChange(e.target.value)} {...props} />
    </Field>
  )
}

export function NumberField({
  label,
  hint,
  value,
  onChange,
  suffix,
  ...props
}: {
  label: string
  hint?: React.ReactNode
  value: number
  onChange: (v: number) => void
  suffix?: string
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange'>) {
  const id = props.id ?? `f-${label.replace(/\W+/g, '-').toLowerCase()}`
  return (
    <Field label={label} hint={hint} htmlFor={id}>
      <div className="relative">
        <Input
          id={id}
          type="number"
          value={Number.isFinite(value) ? value : ''}
          // An empty box parses as NaN; keep the last good value rather than
          // sending NaN to the API and getting an opaque 422.
          onChange={(e) => {
            const n = e.target.valueAsNumber
            if (Number.isFinite(n)) onChange(n)
          }}
          className={cn('tnum', suffix && 'pr-14')}
          {...props}
        />
        {suffix && (
          <span className="pointer-events-none absolute right-3 top-2 text-xs text-muted-foreground">
            {suffix}
          </span>
        )}
      </div>
    </Field>
  )
}

export function SelectField({
  label,
  hint,
  value,
  onChange,
  options,
}: {
  label: string
  hint?: React.ReactNode
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[]
}) {
  const id = `f-${label.replace(/\W+/g, '-').toLowerCase()}`
  return (
    <Field label={label} hint={hint} htmlFor={id}>
      <Select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </Select>
    </Field>
  )
}

export function TextArea({
  label,
  hint,
  value,
  onChange,
  rows = 6,
  mono,
  ...props
}: {
  label: string
  hint?: React.ReactNode
  value: string
  onChange: (v: string) => void
  rows?: number
  mono?: boolean
} & Omit<React.TextareaHTMLAttributes<HTMLTextAreaElement>, 'value' | 'onChange' | 'rows'>) {
  const id = props.id ?? `f-${label.replace(/\W+/g, '-').toLowerCase()}`
  return (
    <Field label={label} hint={hint} htmlFor={id}>
      <textarea
        id={id}
        rows={rows}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={cn(
          'scrollbar-thin w-full rounded-md border border-input bg-card px-3 py-2 text-sm shadow-xs',
          'placeholder:text-muted-foreground',
          'focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20',
          mono && 'font-mono text-xs leading-relaxed',
        )}
        {...props}
      />
    </Field>
  )
}

export function Toggle({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string
  hint?: React.ReactNode
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3 rounded-md border border-border bg-card p-3 transition-colors hover:bg-accent/40">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={cn(
          'relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30',
          checked ? 'bg-primary' : 'bg-muted-foreground/30',
        )}
      >
        <span
          className={cn(
            'absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform',
            checked ? 'translate-x-[1.125rem]' : 'translate-x-0.5',
          )}
        />
      </button>
      <span className="min-w-0">
        <span className="block text-sm font-medium">{label}</span>
        {hint && <span className="mt-0.5 block text-2xs text-muted-foreground">{hint}</span>}
      </span>
    </label>
  )
}
