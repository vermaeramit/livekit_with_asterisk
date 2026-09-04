import { useEffect, useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { BookOpen, Bot, RotateCcw, Send, User, Wrench } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge, Card, Input } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { ApiError, postStream } from '@/lib/api'
import { formatNumber } from '@/lib/utils'
import type { ChatStep, ChatTurn } from '@/types'

type Msg = {
  role: 'user' | 'assistant'
  content: string
  steps?: ChatStep[]
  tokens?: { prompt: number; completion: number; cached: number }
  ms?: number
  // Time to the FIRST word. On a call this is what the caller waits through;
  // the total is not, because they are already being spoken to by then.
  firstTokenMs?: number
  streaming?: boolean
}

/** Replace the last message, which is always the one being written. */
function patchLast(msgs: Msg[], fn: (m: Msg) => Msg): Msg[] {
  if (!msgs.length) return msgs
  return [...msgs.slice(0, -1), fn(msgs[msgs.length - 1])]
}

/**
 * Talk to the agent without dialling it.
 *
 * The point is not the conversation - it is the working shown beneath each
 * answer. Which documents were retrieved and at what score, which tools ran
 * and with what arguments. A real call tells you what the agent said; this
 * tells you why, which is the thing you need when a prompt is wrong.
 */
export function CampaignChat({ campaignId }: { campaignId: number }) {
  const toast = useToast()
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [draft, setDraft] = useState('')
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [msgs])

  const send = useMutation({
    mutationFn: (text: string) => {
      // An empty assistant bubble to fill in. Words land in it as the model
      // produces them, and each tool appears the moment it returns - so a long
      // wait shows WHAT it is waiting on rather than a spinner.
      setMsgs((m) => [...m, { role: 'assistant', content: '', streaming: true }])
      return postStream<ChatTurn>(
        `/campaigns/${campaignId}/chat`,
        {
          message: text,
          // Only the words. The steps are for reading, not for the model -
          // sending them back would put the knowledge base's own output into
          // the history twice.
          history: msgs.map((m) => ({ role: m.role, content: m.content })),
        },
        (e: any) => {
          if (e.stage === 'delta') {
            setMsgs((m) => patchLast(m, (last) => ({ ...last, content: last.content + e.text })))
          } else if (e.stage === 'first_token') {
            setMsgs((m) => patchLast(m, (last) => ({ ...last, firstTokenMs: e.ms })))
          } else if (e.stage === 'step') {
            setMsgs((m) =>
              patchLast(m, (last) => ({ ...last, steps: [...(last.steps ?? []), e.step] })),
            )
          }
        },
      )
    },
    onSuccess: (r) =>
      setMsgs((m) =>
        patchLast(m, (last) => ({
          ...last,
          // From the final line, not from the deltas: a dropped frame would
          // otherwise leave a sentence half-written on screen for good.
          content: r.text,
          steps: r.steps,
          streaming: false,
          firstTokenMs: r.first_token_ms,
          tokens: {
            prompt: r.prompt_tokens,
            completion: r.completion_tokens,
            cached: r.cached_tokens,
          },
          ms: r.ms,
        })),
      ),
    onError: (e) => {
      // The half-written bubble goes; the user's own line stays. Retyping it
      // after a failure is the small insult that makes a tool annoying.
      setMsgs((m) => (m[m.length - 1]?.streaming ? m.slice(0, -1) : m))
      toast.error(e instanceof ApiError ? e.message : 'The turn failed')
    },
  })

  function submit() {
    const text = draft.trim()
    if (!text || send.isPending) return
    setMsgs((m) => [...m, { role: 'user', content: text }])
    setDraft('')
    send.mutate(text)
  }

  return (
    <Card className="flex h-[32rem] flex-col">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <div className="min-w-0">
          <p className="text-sm font-medium">Try the agent</p>
          <p className="text-2xs text-muted-foreground">
            The campaign&rsquo;s own prompt, knowledge base and tools — no speech.
            Real requests, billed to its own key.
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setMsgs([])}
          disabled={!msgs.length || send.isPending}
        >
          <RotateCcw className="h-3.5 w-3.5" />
          Start over
        </Button>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {!msgs.length && (
          <p className="mt-16 text-center text-xs text-muted-foreground">
            Say something a caller would say.
            <br />
            <span className="text-2xs">
              This proves the prompt, the knowledge and the tools. The timings are
              the model&rsquo;s own — a real call adds speech recognition, endpointing
              and playback on top, and none of that is here.
            </span>
          </p>
        )}

        {msgs.map((m, i) => (
          <div key={i} className="space-y-2">
            <div className="flex items-start gap-2">
              <span
                className={`mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full ${
                  m.role === 'user'
                    ? 'bg-muted text-muted-foreground'
                    : 'bg-primary/15 text-primary'
                }`}
              >
                {m.role === 'user' ? <User className="h-3 w-3" /> : <Bot className="h-3 w-3" />}
              </span>
              <div className="min-w-0 flex-1">
                <p className="whitespace-pre-wrap text-sm">
                  {m.content}
                  {m.streaming && (
                    <span className="ml-0.5 inline-block h-3.5 w-1 animate-pulse bg-foreground/50 align-middle" />
                  )}
                </p>
                {m.tokens && (
                  <p className="mt-1 flex flex-wrap gap-x-3 text-2xs text-muted-foreground">
                    {/* First word, then total. On a call the first is what the
                        caller sits through; the second they hear through. */}
                    {m.firstTokenMs ? (
                      <span className="tnum">
                        {m.firstTokenMs} ms to first word · {m.ms} ms total
                      </span>
                    ) : (
                      <span className="tnum">{m.ms} ms</span>
                    )}
                    <span className="tnum">
                      {formatNumber(m.tokens.prompt)} in
                      {m.tokens.cached > 0 && ` (${formatNumber(m.tokens.cached)} cached)`}
                      {' · '}
                      {formatNumber(m.tokens.completion)} out
                    </span>
                  </p>
                )}
              </div>
            </div>

            {/* The working. Indented under the answer it produced, because it
                only means anything next to that answer. */}
            {m.steps?.map((s, j) => <StepCard key={j} step={s} />)}
          </div>
        ))}

        <div ref={endRef} />
      </div>

      <div className="flex gap-2 border-t border-border p-3">
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
          placeholder="Splendor Plus ka price kya hai?"
          disabled={send.isPending}
        />
        <Button onClick={submit} loading={send.isPending} disabled={!draft.trim()}>
          <Send className="h-3.5 w-3.5" />
        </Button>
      </div>
    </Card>
  )
}

function StepCard({ step }: { step: ChatStep }) {
  const [open, setOpen] = useState(false)
  const kb = step.kind === 'kb'

  return (
    <div className="ml-8 rounded-lg border border-border/70 bg-muted/30 text-2xs">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left"
      >
        {kb ? (
          <BookOpen className="h-3 w-3 shrink-0 text-muted-foreground" />
        ) : (
          <Wrench className="h-3 w-3 shrink-0 text-muted-foreground" />
        )}
        <span className="font-medium">{step.name}</span>
        <span className="truncate text-muted-foreground">
          {kb ? `“${step.args.query ?? ''}”` : JSON.stringify(step.args)}
        </span>
        <span className="tnum ml-auto shrink-0 text-muted-foreground">{step.ms} ms</span>
      </button>

      {kb && step.hits.length > 0 && (
        <div className="space-y-0.5 px-2.5 pb-2">
          {step.hits.map((h, i) => (
            <div key={i} className="flex items-center gap-2">
              {/* The score is the number that explains a wrong answer. A weak
                  match that still won is the whole story. */}
              <Badge tone={h.score >= 0.6 ? 'success' : h.score >= 0.4 ? 'default' : 'warning'}>
                {h.score.toFixed(2)}
              </Badge>
              <span className="truncate">{h.document}</span>
              {h.heading && <span className="truncate text-muted-foreground">{h.heading}</span>}
            </div>
          ))}
        </div>
      )}

      {kb && step.hits.length === 0 && (
        <p className="px-2.5 pb-2 text-muted-foreground">
          Nothing matched above the campaign&rsquo;s minimum score.
        </p>
      )}

      {open && (
        <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all border-t border-border/70 px-2.5 py-2 text-muted-foreground">
          {step.result || '(empty)'}
        </pre>
      )}
    </div>
  )
}
