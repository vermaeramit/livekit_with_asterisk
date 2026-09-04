import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Code2, Copy, Globe, Plus, Trash2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { NumberField, TextField, Toggle } from '@/components/ui/field'
import { Badge, Card, Input, Label } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { ApiError, api } from '@/lib/api'
import { formatNumber } from '@/lib/utils'
import type { ChatWidget } from '@/types'

/**
 * The same agent, on a website.
 *
 * The key in the snippet is public - it sits in the page source of a site
 * anyone can view. So the two fields that actually protect this campaign are
 * the origin list and the daily cap, and they are presented as such rather
 * than as advanced settings at the bottom.
 */
export function ChatWidgetPanel({ campaignId }: { campaignId: number }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [origins, setOrigins] = useState<string[]>([])
  const [originDraft, setOriginDraft] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [cap, setCap] = useState(500000)
  const [title, setTitle] = useState('')
  const [welcome, setWelcome] = useState('')
  const [copied, setCopied] = useState(false)

  const widget = useQuery({
    queryKey: ['widget', campaignId],
    queryFn: () => api<ChatWidget | null>(`/campaigns/${campaignId}/widget`),
  })
  const w = widget.data ?? null

  useEffect(() => {
    if (!w) return
    setOrigins(w.allowed_origins)
    setEnabled(w.enabled)
    setCap(w.daily_token_cap)
    setTitle(w.title ?? '')
    setWelcome(w.welcome ?? '')
  }, [w])

  const save = useMutation({
    mutationFn: () =>
      api<ChatWidget>(`/campaigns/${campaignId}/widget`, {
        method: 'PUT',
        body: {
          allowed_origins: origins,
          enabled,
          daily_token_cap: cap,
          title: title.trim() || null,
          welcome: welcome.trim() || null,
        },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['widget', campaignId] })
      toast.success('Saved')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not save'),
  })

  const remove = useMutation({
    mutationFn: () => api(`/campaigns/${campaignId}/widget`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['widget', campaignId] })
      toast.success('Widget removed', 'Its conversations went with it.')
    },
  })

  const snippet = w
    ? `<script src="${window.location.origin}/widget.js" data-key="${w.public_key}" async></script>`
    : ''

  function addOrigin() {
    const o = originDraft.trim().replace(/\/+$/, '')
    if (!o) return
    if (!/^https?:\/\/[A-Za-z0-9.-]+(?::\d+)?$/.test(o)) {
      toast.error('That is not an origin', 'It should look like https://www.example.com, with no path.')
      return
    }
    if (!origins.includes(o)) setOrigins([...origins, o])
    setOriginDraft('')
  }

  if (widget.isLoading) return null

  if (!w) {
    return (
      <Card className="p-4">
        <p className="text-sm font-medium">Put this agent on a website</p>
        <p className="mt-1 max-w-2xl text-2xs leading-relaxed text-muted-foreground">
          The same prompt, knowledge base and tools, as a chat bubble on any page.
          Handoff works differently there — with no phone line to transfer, the
          agent takes a name and a number instead.
        </p>
        <Button className="mt-3" size="sm" onClick={() => save.mutate()} loading={save.isPending}>
          <Plus className="h-3.5 w-3.5" />
          Create a widget
        </Button>
      </Card>
    )
  }

  const nearCap = w.tokens_today / w.daily_token_cap

  return (
    <Card className="space-y-5 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium">Web chat</p>
          <p className="mt-0.5 text-2xs text-muted-foreground">
            {formatNumber(w.conversations_today)} conversation(s) and{' '}
            <span className="tnum">{formatNumber(w.tokens_today)}</span> tokens in the
            last 24 hours.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {!w.enabled && <Badge tone="muted">off</Badge>}
          {nearCap >= 0.8 && <Badge tone="warning">near the daily cap</Badge>}
          <Button
            size="sm"
            variant="outline"
            onClick={() => remove.mutate()}
            aria-label="Remove widget"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      <div className="space-y-1.5">
        <Label>Paste this into the site</Label>
        <div className="flex gap-2">
          <code className="min-w-0 flex-1 overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-2xs">
            {snippet}
          </code>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              void navigator.clipboard.writeText(snippet)
              setCopied(true)
              setTimeout(() => setCopied(false), 1500)
            }}
          >
            {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
          </Button>
        </div>
        <p className="flex items-start gap-1.5 text-2xs leading-relaxed text-muted-foreground">
          <Code2 className="mt-0.5 h-3 w-3 shrink-0" />
          {/* Said plainly, because somebody will otherwise treat it as one and
              be alarmed to find it in a page. */}
          The key is <strong className="font-medium">public</strong> — it is visible to
          anyone who views the page. What protects this campaign is the origin list
          and the cap below, not the key.
        </p>
      </div>

      <div className="space-y-2 rounded-lg border border-border/70 bg-muted/30 p-3">
        <Label>Sites allowed to use it</Label>
        {origins.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {origins.map((o) => (
              <span
                key={o}
                className="inline-flex items-center gap-1 rounded-md bg-card px-2 py-1 font-mono text-2xs ring-1 ring-inset ring-border"
              >
                <Globe className="h-3 w-3 text-muted-foreground" />
                {o}
                <button
                  type="button"
                  onClick={() => setOrigins(origins.filter((x) => x !== o))}
                  className="text-muted-foreground hover:text-danger"
                  aria-label={`Remove ${o}`}
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="flex gap-2">
          <Input
            value={originDraft}
            onChange={(e) => setOriginDraft(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addOrigin())}
            placeholder="https://www.example.com"
            className="font-mono text-xs"
          />
          <Button variant="outline" size="sm" onClick={addOrigin}>
            <Plus className="h-3.5 w-3.5" />
          </Button>
        </div>
        {origins.length === 0 ? (
          // Fail closed, and say so where the empty list is - not in a
          // tooltip somebody finds after wondering why nothing works.
          <p className="text-2xs leading-relaxed text-amber-600 dark:text-amber-500">
            No sites listed, so the widget will refuse every request. That is
            deliberate: an empty list means off, because the alternative is any
            site running this agent on this campaign&rsquo;s bill.
          </p>
        ) : (
          <p className="text-2xs leading-relaxed text-muted-foreground">
            Scheme and host exactly as the browser sends them — no path, no
            wildcards. <span className="font-mono">https://example.com</span> and{' '}
            <span className="font-mono">https://www.example.com</span> are different
            sites and both need listing.
          </p>
        )}
      </div>

      <div className="grid gap-5 sm:grid-cols-2">
        <TextField label="Title" value={title} onChange={setTitle} placeholder="Chat" />
        <NumberField
          label="Daily token cap"
          value={cap}
          onChange={setCap}
          min={1000}
          step={50000}
          hint="Counted across a rolling 24 hours. In tokens rather than rupees because a rupee cap needs every provider rate filled in, and a cap that fails quietly because one is missing is not a cap. Over it, the widget answers politely and stops."
        />
      </div>

      <TextField
        label="First message"
        value={welcome}
        onChange={setWelcome}
        placeholder="Hello. How can I help?"
        hint="Shown before anyone types. Separate from the campaign's spoken greeting, which is written to be heard on a phone."
      />

      <Toggle
        label="Widget is live"
        checked={enabled}
        onChange={setEnabled}
        hint="Turning this off stops it immediately, without removing the snippet from the site."
      />

      <div className="flex justify-end">
        <Button onClick={() => save.mutate()} loading={save.isPending}>
          Save
        </Button>
      </div>
    </Card>
  )
}
