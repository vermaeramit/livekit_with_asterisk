import { useEffect, useRef, useState } from 'react'
import { Maximize2, Minimize2 } from 'lucide-react'
import { Input, Label, Select } from '@/components/ui/primitives'
import { cn } from '@/lib/utils'

export function Field({
  label,
  hint,
  error,
  htmlFor,
  children,
  className,
}: {
  label: string
  hint?: React.ReactNode
  /**
   * Server-side rejection of THIS field.
   *
   * It replaces the hint rather than sitting beside it: the messages the API
   * sends already say what to type, and stacking two paragraphs under one input
   * is how people stop reading either.
   *
   * Shown here, at the field, because the alternative was one banner at the
   * foot of a fourteen-field dialog naming a field scrolled off the top.
   */
  error?: string | null
  htmlFor?: string
  children: React.ReactNode
  className?: string
}) {
  return (
    <div className={cn('space-y-1.5', className)} data-field={htmlFor}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {error ? (
        <p className="text-2xs leading-relaxed text-danger">{error}</p>
      ) : (
        hint && <p className="text-2xs leading-relaxed text-muted-foreground">{hint}</p>
      )}
    </div>
  )
}

export function TextField({
  label,
  hint,
  error,
  value,
  onChange,
  ...props
}: {
  label: string
  hint?: React.ReactNode
  error?: string | null
  value: string
  onChange: (v: string) => void
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange'>) {
  const id = props.id ?? `f-${label.replace(/\W+/g, '-').toLowerCase()}`
  return (
    <Field label={label} hint={hint} error={error} htmlFor={id}>
      <Input id={id} value={value} onChange={(e) => onChange(e.target.value)}
             aria-invalid={error ? true : undefined} {...props} />
    </Field>
  )
}

export function NumberField({
  label,
  hint,
  error,
  value,
  onChange,
  suffix,
  ...props
}: {
  label: string
  hint?: React.ReactNode
  error?: string | null
  value: number
  onChange: (v: number) => void
  suffix?: string
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange'>) {
  const id = props.id ?? `f-${label.replace(/\W+/g, '-').toLowerCase()}`
  return (
    <Field label={label} hint={hint} error={error} htmlFor={id}>
      <div className="relative">
        <Input
          id={id}
          type="number"
          aria-invalid={error ? true : undefined}
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
  error,
  value,
  onChange,
  rows = 6,
  mono,
  /**
   * Offer a full-screen editor.
   *
   * For the fields nobody actually writes in a box: the system prompt runs to
   * five thousand tokens, and editing it sixteen rows at a time means scrolling
   * to find the section you meant to change and losing your place on the way
   * back.
   */
  expandable,
  ...props
}: {
  label: string
  hint?: React.ReactNode
  error?: string | null
  value: string
  onChange: (v: string) => void
  rows?: number
  mono?: boolean
  expandable?: boolean
} & Omit<React.TextareaHTMLAttributes<HTMLTextAreaElement>, 'value' | 'onChange' | 'rows'>) {
  const id = props.id ?? `f-${label.replace(/\W+/g, '-').toLowerCase()}`
  const [full, setFull] = useState(false)
  const boxRef = useRef<HTMLTextAreaElement>(null)

  // Escape closes it, and the page behind must not scroll while it is open -
  // otherwise closing returns you somewhere you never scrolled to.
  useEffect(() => {
    if (!full) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setFull(false)
    }
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', onKey)
    boxRef.current?.focus()
    return () => {
      document.body.style.overflow = prev
      window.removeEventListener('keydown', onKey)
    }
  }, [full])

  // `id` is stripped in the full-screen copy: the collapsed one is still in the
  // tree behind the overlay, and two elements sharing an id is how a <label>
  // ends up pointing at whichever the browser finds first.
  const { id: _idProp, ...rest } = props

  const box = (fullscreen: boolean) => (
    <textarea
      id={fullscreen ? undefined : id}
      ref={fullscreen ? boxRef : undefined}
      rows={fullscreen ? undefined : rows}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      aria-invalid={error ? true : undefined}
      className={cn(
        'scrollbar-thin w-full rounded-md border border-input bg-card px-3 py-2 text-sm shadow-xs',
        'placeholder:text-muted-foreground',
        'focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20',
        'aria-[invalid=true]:border-danger aria-[invalid=true]:ring-2 aria-[invalid=true]:ring-danger/20',
        mono && 'font-mono text-xs leading-relaxed',
        fullscreen && 'min-h-0 flex-1 resize-none',
      )}
      {...(fullscreen ? rest : props)}
    />
  )

  return (
    <>
      <Field label={label} hint={hint} error={error} htmlFor={id}>
        <div className="relative">
          {box(false)}
          {expandable && (
            <button
              type="button"
              onClick={() => setFull(true)}
              title="Edit full screen"
              aria-label="Edit full screen"
              className="absolute right-2 top-2 rounded-md border border-border bg-card/90 p-1 text-muted-foreground shadow-xs transition-colors hover:text-foreground"
            >
              <Maximize2 className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </Field>

      {full && (
        <div className="fixed inset-0 z-50 flex flex-col bg-background p-4 lg:p-6">
          <div className="mb-3 flex items-center gap-3">
            <Label className="text-sm">{label}</Label>
            <button
              type="button"
              onClick={() => setFull(false)}
              className="ml-auto inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
            >
              <Minimize2 className="h-3.5 w-3.5" />
              Done
              <kbd className="ml-1 rounded border border-border px-1 text-2xs">Esc</kbd>
            </button>
          </div>
          {box(true)}
          {/* The hint comes too - on the prompt it carries the token count, and
              that is the number you are watching while you cut it down. */}
          {hint && (
            <p className="mt-2 shrink-0 text-2xs leading-relaxed text-muted-foreground">
              {hint}
            </p>
          )}
        </div>
      )}
    </>
  )
}

/**
 * The whole row is the switch.
 *
 * The first version nested a <button> inside a <label>, which looks right and
 * behaves wrong: a button is not a labelable control, so clicking the text did
 * nothing while the cursor promised otherwise.
 */
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
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={cn(
        'flex w-full items-start gap-3 rounded-md border border-border bg-card p-3 text-left transition-colors',
        'hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25',
      )}
    >
      <span
        aria-hidden
        className={cn(
          'relative mt-0.5 block h-5 w-9 shrink-0 rounded-full transition-colors',
          checked ? 'bg-primary' : 'bg-muted-foreground/35',
        )}
      >
        <span
          className={cn(
            'absolute left-0.5 top-0.5 block h-4 w-4 rounded-full bg-white shadow-sm ring-1 ring-black/5 transition-transform',
            checked && 'translate-x-4',
          )}
        />
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-medium">{label}</span>
        {hint && (
          <span className="mt-0.5 block text-2xs font-normal leading-relaxed text-muted-foreground">
            {hint}
          </span>
        )}
      </span>
    </button>
  )
}

/**
 * A dropdown of known-good values with an escape hatch.
 *
 * A free text box invites a typo that only fails when a real call is in
 * progress; a closed list would block a model or voice the provider adds
 * tomorrow. This does both.
 */
export function ComboField({
  label,
  hint,
  value,
  onChange,
  options,
  placeholder,
  allowEmpty,
  emptyLabel = 'Use the default',
}: {
  label: string
  hint?: React.ReactNode
  value: string
  onChange: (v: string) => void
  options: { value: string; label?: string }[]
  placeholder?: string
  allowEmpty?: boolean
  emptyLabel?: string
}) {
  const id = `f-${label.replace(/\W+/g, '-').toLowerCase()}`
  const known = options.some((o) => o.value === value)
  const custom = value !== '' && !known

  return (
    <Field label={label} hint={hint} htmlFor={id}>
      <Select
        id={id}
        value={custom ? '__custom__' : value}
        onChange={(e) => {
          const v = e.target.value
          // Switching to custom must not silently keep the previous known value
          onChange(v === '__custom__' ? ' ' : v)
        }}
      >
        {allowEmpty && <option value="">{emptyLabel}</option>}
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label ?? o.value}
          </option>
        ))}
        <option value="__custom__">Custom…</option>
      </Select>

      {custom && (
        <Input
          autoFocus
          value={value.trimStart()}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className="mt-2 font-mono"
        />
      )}
    </Field>
  )
}
