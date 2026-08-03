import { useEffect, useRef, useState } from 'react'
import { Download, Loader2, Pause, Play, TriangleAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/primitives'
import { authedBlobUrl } from '@/lib/api'
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
}: {
  callId: number
  sizeBytes: number | null
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

    authedBlobUrl(`/calls/${callId}/recording`)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url)
          return
        }
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
    if (!el || !total) return
    const box = e.currentTarget.getBoundingClientRect()
    el.currentTime = ((e.clientX - box.left) / box.width) * total
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
            aria-valuemax={Math.round(total)}
            aria-valuenow={Math.round(now)}
            tabIndex={0}
          >
            <div
              className="h-full rounded-full bg-primary transition-[width] duration-100"
              style={{ width: total ? `${(now / total) * 100}%` : '0%' }}
            />
          </div>
          <div className="mt-1.5 flex items-center justify-between text-2xs text-muted-foreground">
            <span className="tnum">
              {clock(now)} / {clock(total)}
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
