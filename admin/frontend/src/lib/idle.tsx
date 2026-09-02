import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'

/**
 * Sign someone out when they walk away.
 *
 * "Away" is measured from REAL input - pointer, keyboard, touch, scroll - and
 * never from network traffic. The layout polls alert and gap counts every 60
 * seconds on every page, so an abandoned tab talks to the server for as long
 * as it is open; an idle timer built on requests would never once fire.
 *
 * The browser half is the courtesy: a warning, then a clean sign-out that
 * revokes the session server-side. The rule itself lives in the refresh
 * endpoint, because a closed laptop has no browser left to enforce anything.
 */

// Kept in step with settings.idle_timeout_minutes on the backend, which allows
// a further minute on top of this. That grace is what makes the browser always
// the first to act, so the warning below is what a user sees rather than a
// sudden trip to the login page.
const IDLE_MS = 30 * 60 * 1000

// Long enough to notice and move the mouse, short enough that it is a warning
// and not a nap.
const WARN_MS = 60 * 1000

// Once a minute at most. The heartbeat exists to say "someone is here", and
// saying it on every mousemove would be a request per animation frame.
const HEARTBEAT_MS = 60 * 1000

const ACTIVITY = [
  // Moving the mouse counts. Reading a long transcript can go minutes without
  // a click, and being signed out mid-sentence reads as a broken console
  // rather than a security policy. An abandoned laptop moves no mouse.
  'pointermove',
  'pointerdown',
  'keydown',
  'wheel',
  'touchstart',
  // Scrolling a long transcript is the one way to read this console for
  // minutes without touching anything else.
  'scroll',
] as const

export function useIdleTimeout(enabled: boolean, onTimeout: () => void) {
  const [warningLeft, setWarningLeft] = useState<number | null>(null)

  const lastActivity = useRef(Date.now())
  const lastBeat = useRef(0)
  // Held in a ref so the interval below never has to be torn down and rebuilt
  // when the callback identity changes - a re-created interval resets its own
  // phase, and this one is supposed to tick steadily.
  const timeoutRef = useRef(onTimeout)
  timeoutRef.current = onTimeout

  const markActive = useCallback(() => {
    lastActivity.current = Date.now()
    if (Date.now() - lastBeat.current >= HEARTBEAT_MS) {
      lastBeat.current = Date.now()
      // Failure is deliberately ignored. A missed heartbeat means the session
      // looks idler than it is, and the worst case is being signed out early -
      // which is the safe direction, and not worth a toast about.
      void api('/auth/heartbeat', { method: 'POST' }).catch(() => {})
    }
  }, [])

  useEffect(() => {
    if (!enabled) return

    // Passive: none of these handlers call preventDefault, and saying so keeps
    // scrolling off the main thread.
    const opts = { passive: true, capture: true } as const
    for (const e of ACTIVITY) window.addEventListener(e, markActive, opts)

    // Coming back to the tab is activity, and it is also the moment the clock
    // may have jumped - a laptop that slept for an hour wakes up here.
    const onVisible = () => {
      if (document.visibilityState === 'visible') markActive()
    }
    document.addEventListener('visibilitychange', onVisible)

    const tick = setInterval(() => {
      const idle = Date.now() - lastActivity.current
      if (idle >= IDLE_MS) {
        setWarningLeft(null)
        timeoutRef.current()
      } else if (idle >= IDLE_MS - WARN_MS) {
        setWarningLeft(Math.ceil((IDLE_MS - idle) / 1000))
      } else {
        setWarningLeft((prev) => (prev === null ? prev : null))
      }
    }, 1000)

    return () => {
      for (const e of ACTIVITY) window.removeEventListener(e, markActive, opts)
      document.removeEventListener('visibilitychange', onVisible)
      clearInterval(tick)
    }
  }, [enabled, markActive])

  return { warningLeft, staySignedIn: markActive }
}

export function IdleWarning({
  secondsLeft,
  onStay,
}: {
  secondsLeft: number
  onStay: () => void
}) {
  return (
    <div
      role="alertdialog"
      aria-live="assertive"
      className="fixed inset-x-0 bottom-0 z-[60] flex justify-center p-4 sm:bottom-6"
    >
      <div className="flex w-full max-w-md items-start gap-3 rounded-xl border border-amber-500/30 bg-card p-4 shadow-2xl ring-1 ring-black/5">
        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-500" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">Still there?</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            You'll be signed out in{' '}
            <span className="tnum font-medium text-foreground">{secondsLeft}s</span>{' '}
            after 30 minutes without activity.
          </p>
        </div>
        <Button size="sm" onClick={onStay}>
          Stay signed in
        </Button>
      </div>
    </div>
  )
}
