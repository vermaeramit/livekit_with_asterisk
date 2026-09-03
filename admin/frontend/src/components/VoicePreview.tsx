import { useEffect, useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Loader2, Play, Square } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input, Label } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { ApiError, authedAudio } from '@/lib/api'
import type { AgentConfig } from '@/types'

/**
 * Hear the chosen voice before a caller does.
 *
 * Soniox offers seventy voices on tts-rt-v2, listed as a name, a gender and
 * half a sentence. Choosing from that is guessing, and the campaign finds out
 * what it picked on a live call.
 *
 * Campaign-scoped because the KEY is: the audio is synthesised for real, on
 * this campaign's own provider key, and billed to it exactly as its calls are.
 */
export function VoicePreview({ value, campaignId }: { value: AgentConfig; campaignId: number }) {
  const toast = useToast()
  // The greeting, because that is the line a caller actually hears first - the
  // voice should be judged on the words it will really say, not on a sample
  // sentence chosen by us.
  const [text, setText] = useState(value.greeting || 'Namaste, main aapki kya madad kar sakti hoon?')
  const [speed, setSpeed] = useState(1)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [playing, setPlaying] = useState(false)

  // Every URL created here holds a blob in memory until it is revoked.
  const urls = useRef<string[]>([])
  useEffect(
    () => () => {
      urls.current.forEach(URL.revokeObjectURL)
      audioRef.current?.pause()
    },
    [],
  )

  const play = useMutation({
    mutationFn: async () => {
      const url = await authedAudio(`/campaigns/${campaignId}/tts-preview`, {
        provider: value.tts_provider,
        model: value.tts_model,
        voice: value.tts_voice,
        language: value.language,
        text: text.slice(0, 400),
        speed,
      })
      urls.current.push(url)
      return url
    },
    onSuccess: (url) => {
      audioRef.current?.pause()
      const el = new Audio(url)
      audioRef.current = el
      el.onended = () => setPlaying(false)
      el.onerror = () => setPlaying(false)
      setPlaying(true)
      void el.play()
    },
    onError: (e) =>
      toast.error(
        e instanceof ApiError ? e.message : 'Could not synthesise that',
        'The preview is made on this campaign’s own provider key.',
      ),
  })

  function stop() {
    audioRef.current?.pause()
    setPlaying(false)
  }

  // Nothing to synthesise with. Said plainly rather than left as a button that
  // fails when pressed.
  const missing = !value.tts_model || !value.tts_voice

  return (
    <div className="space-y-3 rounded-lg border border-border/70 bg-muted/30 p-3">
      <div>
        <p className="text-xs font-medium">Hear it first</p>
        <p className="mt-0.5 text-2xs leading-relaxed text-muted-foreground">
          Synthesised for real, on this campaign&rsquo;s own key and billed to it.
          Change the voice above and press play again to compare — the same line in
          each voice is the only way to tell them apart.
        </p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="tts-try">What it should say</Label>
        <Input
          id="tts-try"
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={400}
          placeholder="Namaste, main aapki kya madad kar sakti hoon?"
        />
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="tts-speed" className="text-2xs">
            Speed {speed.toFixed(2)}×
          </Label>
          <input
            id="tts-speed"
            type="range"
            min={0.7}
            max={1.3}
            step={0.05}
            value={speed}
            onChange={(e) => setSpeed(Number(e.target.value))}
            className="w-40 accent-primary"
          />
        </div>

        {playing ? (
          <Button size="sm" variant="outline" onClick={stop}>
            <Square className="h-3.5 w-3.5" />
            Stop
          </Button>
        ) : (
          <Button
            size="sm"
            onClick={() => play.mutate()}
            disabled={missing || !text.trim() || play.isPending}
          >
            {play.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Play className="h-3.5 w-3.5" />
            )}
            Play
          </Button>
        )}

        <span className="text-2xs text-muted-foreground">
          {missing
            ? 'Choose a model and a voice above first.'
            : `${value.tts_voice} · ${value.tts_model}`}
        </span>
      </div>

      {/* The speed here is a preview control, not a campaign setting. Saying so
          prevents somebody tuning it, liking it, and wondering why calls sound
          the same. */}
      <p className="text-2xs text-muted-foreground">
        Speed applies to this preview only — it is not saved with the campaign.
      </p>
    </div>
  )
}
