import { useEffect, useRef, useState } from 'react'
import { Download, Loader2, Pause, Play, TriangleAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/primitives'
import { authedBlob } from '@/lib/api'
import { cn } from '@/lib/utils'

function clock(seconds: number): string {
  if (!Number.isFinite(seconds)) return '0:00'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

export function RecordingPlayer({
  callId,
  sizeBytes,
  durationMs,
}: {
  callId: number
  sizeBytes: number | null
  // The call's measured length, used when the browser will not report the
  // media's own. See effectiveTotal.
  durationMs?: number | null
}) {
  const audio = useRef<HTMLAudioElement>(null)
  const [src, setSrc] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [playing, setPlaying] = useState(false)
  const [now, setNow] = useState(0)
  const [total, setTotal] = useState(0)
  const [rate, setRate] = useState(1)

  // <audio src> cannot carry an Authorization header, and the endpoint is not
  // public. Fetch it once with the token and play from a blob instead.
  useEffect(() => {
    let revoke: string | null = null
    let cancelled = false
    setLoading(true)
    setError(null)

    authedBlob(`/calls/${callId}/recording`)
      .then((blob) => {
        if (cancelled) return
        // What arrived, against what the server said it holds. A short blob
        // decodes as a corrupt stream and the browser only reports "cannot
        // decode", which sends you looking at the codec - the one place the
        // fault is not.
        if (sizeBytes != null && blob.size !== sizeBytes) {
          setError(
            `Only ${blob.size.toLocaleString()} of ${sizeBytes.toLocaleString()} ` +
              'bytes arrived, so the audio is incomplete. The file on the server ' +
              'is fine — something between it and this page is cutting the ' +
              'response short.',
          )
          return
        }
        const url = URL.createObjectURL(blob)
        revoke = url
        setSrc(url)
      })
      .catch((e) => !cancelled && setError((e as Error).message))
      .finally(() => !cancelled && setLoading(false))

    return () => {
      cancelled = true
      if (revoke) URL.revokeObjectURL(revoke)
    }
  }, [callId])

  /**
   * The length to draw the scrubber against.
   *
   * Chrome reports `duration` as Infinity for an Ogg stream until it has read
   * the last page, so the media element cannot be relied on for this. The
   * previous attempt forced it out by seeking to 1e101 - which Chrome answers
   * with an error event, turning a cosmetic problem into a player that refused
   * to load at all.
   *
   * There was never any need to ask the browser. The call's duration is
   * measured server-side and already on the page; the recording is a few
   * seconds shorter because it starts after the answer, which is close enough
   * for a progress bar and exact once the browser does report a real duration.
   */
  const effectiveTotal =
    Number.isFinite(total) && total > 0 ? total : (durationMs ?? 0) / 1000

  function toggle() {
    const el = audio.current
    if (!el) return
    if (el.paused) {
      el.play()
      setPlaying(true)
    } else {
      el.pause()
      setPlaying(false)
    }
  }

  function seek(e: React.MouseEvent<HTMLDivElement>) {
    const el = audio.current
    if (!el || !effectiveTotal) return
    const box = e.currentTarget.getBoundingClientRect()
    el.currentTime = ((e.clientX - box.left) / box.width) * effectiveTotal
  }

  if (loading) {
    return (
      <Card className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading recording…
      </Card>
    )
  }

  if (error) {
    return (
      <Card className="flex items-start gap-2 p-4 text-sm">
        <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
        <div>
          <p className="font-medium">Recording unavailable</p>
          <p className="mt-0.5 text-xs text-muted-foreground">{error}</p>
        </div>
      </Card>
    )
  }

  return (
    <Card className="p-4">
      <audio
        ref={audio}
        src={src ?? undefined}
        preload="metadata"
        onLoadedMetadata={(e) => setTotal(e.currentTarget.duration)}
        onTimeUpdate={(e) => setNow(e.currentTarget.currentTime)}
        onEnded={() => setPlaying(false)}
        // Without this a codec the browser cannot decode leaves a player that
        // looks fine, does nothing, and says nothing about why.
        onError={() =>
          setError(
            'The browser could not decode this recording. The file is on the ' +
              'server and downloads fine — try the download button.',
          )
        }
      />

      <div className="flex items-center gap-3">
        <Button size="icon" onClick={toggle} aria-label={playing ? 'Pause' : 'Play'}>
          {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
        </Button>

        <div className="min-w-0 flex-1">
          <div
            onClick={seek}
            className="group h-2 cursor-pointer rounded-full bg-muted"
            role="slider"
            aria-label="Seek"
            aria-valuemin={0}
            aria-valuemax={Math.round(effectiveTotal)}
            aria-valuenow={Math.round(now)}
            tabIndex={0}
          >
            <div
              className="h-full rounded-full bg-primary transition-[width] duration-100"
              style={{ width: effectiveTotal ? `${(now / effectiveTotal) * 100}%` : '0%' }}
            />
          </div>
          <div className="mt-1.5 flex items-center justify-between text-2xs text-muted-foreground">
            <span className="tnum">
              {clock(now)} / {clock(effectiveTotal)}
            </span>
            <span className="tnum">
              {sizeBytes != null ? `${Math.round(sizeBytes / 1024)} KB` : ''}
            </span>
          </div>
        </div>

        {/* Reviewing a long call at 1× is a chore; 1.5× is the usual default in
            QA tools and speech stays intelligible well past it. */}
        <div className="flex items-center gap-1">
          {[1, 1.5, 2].map((r) => (
            <button
              key={r}
              onClick={() => {
                setRate(r)
                if (audio.current) audio.current.playbackRate = r
              }}
              className={cn(
                'rounded px-1.5 py-0.5 text-2xs tnum transition-colors',
                rate === r
                  ? 'bg-primary/10 font-medium text-primary'
                  : 'text-muted-foreground hover:bg-accent',
              )}
            >
              {r}×
            </button>
          ))}
        </div>

        <a
          href={src ?? '#'}
          download={`call-${callId}.opus`}
          className="rounded-md p-2 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          title="Download"
        >
          <Download className="h-4 w-4" />
        </a>
      </div>
    </Card>
  )
}
