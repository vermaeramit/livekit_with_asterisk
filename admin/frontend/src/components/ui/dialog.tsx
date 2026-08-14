import { useEffect, useRef } from 'react'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * Minimal modal. Not a full focus-trap implementation, but it does the three
 * things whose absence is actually noticed: escape closes, the backdrop closes,
 * and the page behind stops scrolling.
 */
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = 'md',
}: {
  open: boolean
  onClose: () => void
  title: string
  description?: string
  children: React.ReactNode
  footer?: React.ReactNode
  size?: 'md' | 'lg'
}) {
  const body = useRef<HTMLDivElement>(null)

  // Callers pass an inline arrow, so `onClose` is a new function on every
  // render. Reading it through a ref keeps the effect below keyed on `open`
  // alone - otherwise it re-ran on every keystroke and kept stealing focus.
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    if (!open) return

    const onEsc = (e: KeyboardEvent) => e.key === 'Escape' && onCloseRef.current()
    document.addEventListener('keydown', onEsc)

    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    // Focus the first field, not the first focusable element - the close button
    // comes earlier in the DOM and would otherwise win. Scoped to the content
    // area so the header is never a candidate.
    body.current
      ?.querySelector<HTMLElement>('input, select, textarea')
      ?.focus()

    return () => {
      document.removeEventListener('keydown', onEsc)
      document.body.style.overflow = prevOverflow
    }
  }, [open])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="fixed inset-0 animate-fade-in bg-foreground/30 backdrop-blur-[2px]"
        onClick={onClose}
      />
      {/*
        Capped to the viewport, with the FIELDS scrolling rather than the whole
        dialog.

        The previous arrangement let the dialog grow past the screen and scrolled
        the wrapper instead. On a long form that hid the first field permanently:
        a flex item taller than its `items-center` container overflows equally
        top and bottom, and the top half cannot be scrolled to. The tool dialog's
        Name input was unreachable, which is exactly where its error message
        pointed.

        Keeping the header and footer outside the scroll area is the other half:
        Save stays on screen instead of being hunted for at the bottom.
      */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cn(
          'relative flex max-h-full w-full flex-col animate-fade-up rounded-xl',
          'border border-border bg-card shadow-lg',
          size === 'lg' ? 'max-w-2xl' : 'max-w-md',
        )}
      >
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-border px-5 py-4">
          <div>
            <h2 className="text-base font-semibold tracking-tight">{title}</h2>
            {description && (
              <p className="mt-1 text-xs text-muted-foreground">{description}</p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="-mr-1 -mt-1 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div ref={body} className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {children}
        </div>

        {footer && (
          <div className="flex shrink-0 items-center justify-end gap-2 border-t border-border bg-muted/40 px-5 py-3">
            {footer}
          </div>
        )}
      </div>
    </div>
  )
}
