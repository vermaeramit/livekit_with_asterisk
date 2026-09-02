/**
 * What the caller actually spoke, as opposed to what the campaign is set to.
 *
 * The two are easy to confuse, and the console confused them for months: the
 * language shown against a call was the campaign's setting, identical on every
 * row. Soniox has been identifying the real thing all along.
 *
 * Detail page only. A one-line version for the calls table existed briefly and
 * was taken out - the list is scanned for which call to open, and a language
 * mix is something you read once you are looking at one.
 */

// Only the languages these campaigns run in are named. Anything else shows its
// code, which is honest - a wrong name is worse than a code somebody can look
// up.
const NAMES: Record<string, string> = {
  hi: 'Hindi',
  en: 'English',
  mr: 'Marathi',
  gu: 'Gujarati',
  ta: 'Tamil',
  te: 'Telugu',
  kn: 'Kannada',
  bn: 'Bengali',
  pa: 'Punjabi',
  ml: 'Malayalam',
  ur: 'Urdu',
}

export function languageName(code: string) {
  return NAMES[code] ?? code
}

/** Sorted by how much was said, with the share of the whole. */
export function languageShares(detected: Record<string, number> | null | undefined) {
  if (!detected) return []
  const total = Object.values(detected).reduce((a, b) => a + b, 0)
  if (!total) return []
  return Object.entries(detected)
    .sort((a, b) => b[1] - a[1])
    .map(([code, chars]) => ({
      code,
      chars,
      share: chars / total,
    }))
}

/** The full breakdown, with a bar. For the call detail page. */
export function LanguageBreakdown({
  detected,
}: {
  detected: Record<string, number> | null | undefined
}) {
  const shares = languageShares(detected)
  if (!shares.length) return null

  return (
    <div className="space-y-2">
      <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-muted">
        {shares.map((s, i) => (
          <div
            key={s.code}
            // A fixed ramp rather than a colour per language: the point is the
            // proportion, and the legend below says which is which.
            className={
              ['bg-primary', 'bg-primary/60', 'bg-primary/35', 'bg-primary/20'][
                Math.min(i, 3)
              ]
            }
            style={{ width: `${s.share * 100}%` }}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        {shares.map((s) => (
          <span key={s.code} className="text-xs">
            {languageName(s.code)}{' '}
            <span className="tnum text-muted-foreground">
              {Math.round(s.share * 100)}%
            </span>
            <span className="tnum text-2xs text-muted-foreground">
              {' '}
              ({s.chars.toLocaleString()} chars)
            </span>
          </span>
        ))}
      </div>
    </div>
  )
}
