import { useQuery } from '@tanstack/react-query'
import { Mic, MessageSquare, Waves, Wallet } from 'lucide-react'
import { PAGE, PageHeader } from '@/components/Layout'
import { Card, CardBody, CardHeader, CardTitle, EmptyState, Skeleton } from '@/components/ui/primitives'
import { api } from '@/lib/api'
import type { ComparedRate, RateComparison } from '@/types'

/**
 * Every stored price on one axis per layer, in rupees.
 *
 * The Provider rates page stores what each provider BILLS, in the unit it bills
 * it — per million characters, per hour of audio, per million tokens, dollars
 * or rupees — because a table that reads back differently from the page it was
 * copied from invites somebody to "fix" it. That is right for storing and
 * useless for choosing between them.
 *
 * This page does the converting, and shows its working: the price as entered
 * stays beside the converted figure, and anything that had to be assumed is
 * printed rather than folded into the number.
 *
 * A price that cannot be converted shows a dash and the reason. A zero would
 * read exactly like something that is free, and one of these providers really
 * is free, so the difference has to be visible.
 */

const inr = (n: number) =>
  `₹${n < 1 ? n.toFixed(4) : n.toFixed(2)}`

function Rows({ rows, axis }: { rows: ComparedRate[]; axis: string }) {
  if (!rows.length) {
    return (
      <EmptyState
        icon={Wallet}
        title="No prices yet"
        hint="Add them on Provider rates — a layer with no price shows as zero spend, which reads exactly like a cheap one."
      />
    )
  }
  return (
    <div className="divide-y divide-border/60">
      {rows.map((r, i) => (
        <div key={`${r.provider}-${r.model ?? ''}-${r.kind}-${i}`} className="flex items-baseline gap-4 py-2.5">
          <div className="min-w-0 flex-1">
            <div className="text-sm">
              {r.provider}
              {r.model && <span className="text-muted-foreground"> · {r.model}</span>}
              {r.kind.startsWith('llm_') && (
                <span className="ml-2 text-2xs uppercase tracking-wide text-muted-foreground">
                  {r.kind.replace('llm_', '')}
                </span>
              )}
            </div>
            <div className="text-2xs text-muted-foreground">
              billed {r.price} {r.currency} {r.unit.replace('_', ' ')}
              {r.caveat && <> — {r.caveat}</>}
            </div>
          </div>
          <div className="shrink-0 text-right">
            {r.inr === null ? (
              <span className="text-sm text-muted-foreground">—</span>
            ) : (
              <>
                <span className="text-sm font-medium tabular-nums">{inr(r.inr)}</span>
                <span className="ml-1 text-2xs text-muted-foreground">{axis}</span>
              </>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

export function PriceComparison() {
  const { data, isLoading } = useQuery({
    queryKey: ['rate-comparison'],
    queryFn: () => api<RateComparison>('/rates/comparison'),
  })

  return (
    <div className={PAGE}>
      <PageHeader
        title="Price comparison"
        description="What each provider costs, in rupees, on one basis per layer."
      />

      {isLoading && <Skeleton className="h-64" />}

      {data && (
        <>
          {/* Said before the numbers rather than under them. Both of these
              change every figure on the page, and a reader who does not know
              them cannot tell whether to trust what they are looking at. */}
          <Card>
            <CardBody className="space-y-1.5 text-2xs leading-relaxed text-muted-foreground">
              <p>
                <span className="font-medium text-foreground">Speech is priced per minute.</span>{' '}
                Some providers bill per character and some per second of audio, which
                cannot be compared directly. Both are converted at{' '}
                <span className="font-medium text-foreground">{data.chars_per_second} characters of speech per second</span>,
                measured on this system&rsquo;s own calls. A campaign running faster than
                1.0× says more characters per minute and pays a little more than this shows.
              </p>
              <p>
                {data.usd_to_inr ? (
                  <>
                    Dollar prices are converted at{' '}
                    <span className="font-medium text-foreground">₹{data.usd_to_inr} to $1</span>,
                    the rate set on Provider rates — held rather than fetched, so a month&rsquo;s
                    spend does not move while you read it.
                  </>
                ) : (
                  <span className="text-warning">
                    No exchange rate is set, so every dollar price shows a dash rather than
                    an invented rupee figure. Set one on Provider rates.
                  </span>
                )}
              </p>
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>
                <Waves className="h-4 w-4" />
                Text to speech
              </CardTitle>
            </CardHeader>
            <CardBody>
              <Rows rows={data.tts} axis="/ min of speech" />
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>
                <Mic className="h-4 w-4" />
                Speech to text
              </CardTitle>
            </CardHeader>
            <CardBody>
              <Rows rows={data.stt} axis="/ min of audio" />
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>
                <MessageSquare className="h-4 w-4" />
                Language model
              </CardTitle>
            </CardHeader>
            <CardBody>
              {/* Not per minute. A model is billed by tokens and how many a
                  minute uses depends on the prompt, the knowledge base and how
                  much the caller says — three things that differ per campaign.
                  Converting it here would be a guess wearing a number. */}
              <Rows rows={data.llm} axis="/ 1M tokens" />
            </CardBody>
          </Card>
        </>
      )}
    </div>
  )
}
