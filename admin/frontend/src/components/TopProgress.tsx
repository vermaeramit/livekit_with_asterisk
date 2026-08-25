import { useEffect, useState } from 'react'
import { useIsFetching, useIsMutating } from '@tanstack/react-query'

/**
 * A thin bar across the top while anything is in flight.
 *
 * Held back for a moment first. Most requests here finish in well under a
 * tenth of a second, and a bar that flashes on every one of them reads as a
 * glitch rather than as progress — it is noise on the pages that are fine and
 * says nothing about the pages that are not.
 *
 * It also never claims to know how far along it is. The width is an animation,
 * not a measurement; a bar that creeps to 90% and waits is a lie told smoothly.
 */
export function TopProgress({ delayMs = 300 }: { delayMs?: number }) {
  const busy = useIsFetching() + useIsMutating()
  const [show, setShow] = useState(false)

  useEffect(() => {
    if (!busy) {
      setShow(false)
      return
    }
    const t = setTimeout(() => setShow(true), delayMs)
    return () => clearTimeout(t)
  }, [busy, delayMs])

  if (!show) return null

  return (
    <div
      className="pointer-events-none fixed inset-x-0 top-0 z-50 h-0.5 overflow-hidden bg-primary/15"
      role="status"
      aria-label="Loading"
    >
      <div className="h-full w-1/3 animate-[topbar_1.1s_ease-in-out_infinite] bg-primary" />
    </div>
  )
}
