import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Code2, Copy, Globe, Plus, Trash2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { NumberField, TextField, Toggle } from '@/components/ui/field'
import { Badge, Card, Input, Label } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { ApiError, api, uploadFile } from '@/lib/api'
import { formatNumber } from '@/lib/utils'
import type { ChatWidget } from '@/types'

/**
 * Black or white text on a given background.
 *
 * The same relative-luminance formula the widget itself uses, so the preview
 * here and the thing on the customer's page cannot disagree. Duplicated rather
 * than shared because the widget is plain JS served as written, with no build
 * step and nothing to import from.
 */
function onAccent(hex: string): string {
  if (!/^#[0-9a-fA-F]{6}$/.test(hex)) return '#fff'
  const n = parseInt(hex.slice(1), 16)
  const lum = [(n >> 16) & 255, (n >> 8) & 255, n & 255]
    .map((v) => {
      const x = v / 255
      return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4)
    })
    .reduce((acc, x, i) => acc + [0.2126, 0.7152, 0.0722][i] * x, 0)
  return lum > 0.45 ? '#111' : '#fff'
}

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
  const [anyOrigin, setAnyOrigin] = useState(false)
  const [originDraft, setOriginDraft] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [cap, setCap] = useState(500000)
  const [title, setTitle] = useState('')
  const [welcome, setWelcome] = useState('')
  const [accent, setAccent] = useState('#2563eb')
  const [copied, setCopied] = useState(false)
  const [iconV, setIconV] = useState(0)

  const widget = useQuery({
    queryKey: ['widget', campaignId],
    queryFn: () => api<ChatWidget | null>(`/campaigns/${campaignId}/widget`),
  })
  const w = widget.data ?? null

  useEffect(() => {
    if (!w) return
    setOrigins(w.allowed_origins)
    setAnyOrigin(w.allow_any_origin)
    setEnabled(w.enabled)
    setCap(w.daily_token_cap)
    setTitle(w.title ?? '')
    setWelcome(w.welcome ?? '')
    setAccent(w.accent_color || '#2563eb')
  }, [w])

  const save = useMutation({
    mutationFn: () =>
      api<ChatWidget>(`/campaigns/${campaignId}/widget`, {
        method: 'PUT',
        body: {
          allowed_origins: origins,
          allow_any_origin: anyOrigin,
          enabled,
          daily_token_cap: cap,
          title: title.trim() || null,
          welcome: welcome.trim() || null,
          accent_color: accent,
        },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['widget', campaignId] })
      toast.success('Saved')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not save'),
  })

  const upload = useMutation({
    mutationFn: (f: File) => uploadFile<ChatWidget>(`/campaigns/${campaignId}/widget/icon`, f),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['widget', campaignId] })
      // Cache-busted, or the preview keeps showing the old logo while the
      // customer's site shows the new one - and the person here concludes the
      // upload failed.
      setIconV(Date.now())
      toast.success('Icon uploaded')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not upload that'),
  })

  const clearIcon = useMutation({
    mutationFn: () => api(`/campaigns/${campaignId}/widget/icon`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['widget', campaignId] })
      setIconV(Date.now())
    },
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
          anyone who views the page.{' '}
          {anyOrigin
            ? 'With any site allowed, the daily cap below is the only thing protecting this campaign.'
            : 'What protects this campaign is the origin list and the cap below, not the key.'}
        </p>
      </div>

      <div className="space-y-3 rounded-lg border border-border/70 bg-muted/30 p-3">
        <Label>Sites allowed to use it</Label>

        <Toggle
          label="Any site"
          checked={anyOrigin}
          onChange={setAnyOrigin}
          hint="For a customer with many subdomains, or a site whose address is not settled yet. The list below is then ignored."
        />

        {anyOrigin ? (
          // The consequence, in the place where the decision is made. The
          // Origin header is the only check a browser cannot be talked out of;
          // without it the cap below is genuinely the only limit left.
          <p className="text-2xs leading-relaxed text-amber-600 dark:text-amber-500">
            The widget will answer any site, and anything that is not a browser.
            The daily token cap is now the only limit — set it to what you are
            willing to spend in a day if somebody points a script at it.
          </p>
        ) : null}

        {!anyOrigin && origins.length > 0 && (
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
        <div className={`flex gap-2 ${anyOrigin ? 'hidden' : ''}`}>
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
        {anyOrigin ? null : origins.length === 0 ? (
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

      <div className="space-y-4 rounded-lg border border-border/70 bg-muted/30 p-3">
        <p className="text-xs font-medium">How it looks</p>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="w-accent">Colour</Label>
            <div className="flex items-center gap-2">
              <input
                id="w-accent"
                type="color"
                value={accent}
                onChange={(e) => setAccent(e.target.value)}
                className="h-9 w-12 cursor-pointer rounded border border-border bg-transparent p-0.5"
              />
              <Input
                value={accent}
                onChange={(e) => setAccent(e.target.value)}
                className="font-mono text-xs"
                placeholder="#2563eb"
              />
            </div>
            <p className="text-2xs leading-relaxed text-muted-foreground">
              {/* Said because somebody will otherwise look for the setting and
                  not find it. */}
              The text on top of it is worked out from the colour, not chosen —
              a pale accent gets dark text, so the header cannot end up
              unreadable.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="w-icon">Icon</Label>
            <div className="flex items-center gap-2">
              <input
                id="w-icon"
                type="file"
                accept="image/png,image/jpeg,image/webp"
                className="min-w-0 flex-1 text-2xs file:mr-2 file:rounded-md file:border-0 file:bg-muted file:px-2 file:py-1 file:text-2xs"
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (f) upload.mutate(f)
                  e.target.value = ''
                }}
              />
              {w.has_icon && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => clearIcon.mutate()}
                  aria-label="Remove icon"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              )}
            </div>
            <p className="text-2xs leading-relaxed text-muted-foreground">
              PNG, JPEG or WebP, under 512&nbsp;KB. Shown at 28 pixels, so a
              large file only costs the visitor time.{' '}
              {/* This is the rejection people will actually hit, so it is
                  said before they hit it. */}
              SVG is not accepted — it can carry script, and this file is served
              from our own address.
            </p>
          </div>
        </div>

        {/* What it will actually look like, next to the fields that decide it.
            Copying a hex code and then loading a customer's site to see it is
            how a colour ends up nearly right. */}
        <div className="flex items-center gap-3">
          <span
            className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-lg"
            style={{ background: accent, color: onAccent(accent) }}
          >
            {w.has_icon ? (
              <img
                src={`/api/widget/${w.public_key}/icon?v=${iconV}`}
                alt=""
                className="h-6 w-6 rounded object-contain"
              />
            ) : (
              '💬'
            )}
          </span>
          <span
            className="rounded-xl px-3 py-1.5 text-xs"
            style={{ background: accent, color: onAccent(accent) }}
          >
            {title.trim() || 'Chat'}
          </span>
        </div>
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
