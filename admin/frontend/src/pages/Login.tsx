import { useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { AlertCircle, ArrowRight, Bot, Eye, EyeOff, Lock, Mail, Radio, User } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input, Label } from '@/components/ui/primitives'
import { useAuth } from '@/lib/auth'
import { ApiError } from '@/lib/api'

/** Bar heights for the idle waveform, in percent. Irregular on purpose - an
 *  even curve reads as a loading spinner rather than speech. */
const WAVE = [34, 62, 28, 88, 46, 72, 38, 96, 54, 30, 68, 42, 80, 26, 58]

function Waveform() {
  return (
    <div className="flex h-10 items-center gap-[3px]" aria-hidden>
      {WAVE.map((h, i) => (
        <span
          key={i}
          className="w-[3px] origin-center rounded-full bg-gradient-to-t from-blue-500/40 to-cyan-300/90 animate-wave"
          style={{ height: `${h}%`, animationDelay: `${i * 90}ms` }}
        />
      ))}
    </div>
  )
}

function ConversationPreview() {
  return (
    <div className="glass w-full max-w-sm rounded-2xl p-4 shadow-2xl">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-2xs font-medium uppercase tracking-wider text-slate-400">
          <span className="relative flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-400" />
          </span>
          Live call
        </span>
        <span className="tnum text-2xs text-slate-500">00:42</span>
      </div>

      <div className="mt-3.5 space-y-2.5">
        <div className="flex items-start gap-2">
          <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full bg-white/10 text-slate-300">
            <User className="h-3 w-3" />
          </span>
          <p className="rounded-2xl rounded-tl-sm bg-white/[0.08] px-3 py-1.5 text-xs text-slate-200">
            EMI ki last date kya hai?
          </p>
        </div>

        <div className="flex items-start gap-2">
          <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full bg-blue-500/20 text-blue-300">
            <Bot className="h-3 w-3" />
          </span>
          <div className="min-w-0">
            <p className="rounded-2xl rounded-tl-sm bg-blue-500/15 px-3 py-1.5 text-xs text-blue-50 ring-1 ring-inset ring-blue-400/20">
              Har mahine ki 5 taarikh tak, sir.
            </p>
            <div className="mt-1.5 flex items-center gap-1.5">
              <span className="tnum rounded-full bg-emerald-400/15 px-1.5 py-px text-2xs font-medium text-emerald-300 ring-1 ring-inset ring-emerald-400/25">
                1.9s
              </span>
              <span className="text-2xs text-slate-500">grounded · 2 sources</span>
            </div>
          </div>
        </div>
      </div>

      <div className="mt-4 border-t border-white/10 pt-3">
        <Waveform />
      </div>
    </div>
  )
}

export function Login() {
  const { user, ready, signIn } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [reveal, setReveal] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (ready && user) {
    const to = (location.state as { from?: string } | null)?.from ?? '/calls'
    return <Navigate to={to} replace />
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await signIn(email, password)
      navigate((location.state as { from?: string } | null)?.from ?? '/calls', { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong')
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      {/* ── brand panel ──────────────────────────────────────────────────────
          Deliberately fixed dark in both themes. It is a marketing surface, not
          a working one, and it should not flip with the theme toggle. */}
      <div className="relative hidden flex-col justify-between overflow-hidden bg-[#0a1020] p-12 xl:p-16 lg:flex">
        <div className="absolute inset-0 bg-dot-grid" aria-hidden />
        <div
          aria-hidden
          className="pointer-events-none absolute -right-32 -top-40 h-[32rem] w-[32rem] animate-float rounded-full bg-blue-600/25 blur-[100px]"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -bottom-40 -left-24 h-[30rem] w-[30rem] animate-float rounded-full bg-indigo-500/20 blur-[100px]"
          style={{ animationDelay: '-7s' }}
        />
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 bottom-0 h-40 bg-gradient-to-t from-[#0a1020] to-transparent"
        />

        <div className="relative flex items-center gap-2.5">
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 text-white shadow-lg shadow-blue-950/50">
            <Radio className="h-4 w-4" />
          </span>
          <span className="flex flex-col leading-none">
            <span className="font-semibold tracking-tight text-white">Voice Console</span>
            <span className="mt-1 text-2xs text-slate-400">AI calling platform</span>
          </span>
        </div>

        <div className="relative max-w-lg">
          <h1
            className="animate-fade-up text-[2.5rem] font-semibold leading-[1.1] tracking-tight text-white"
            style={{ animationDelay: '60ms' }}
          >
            Every call, every turn,
            <br />
            <span className="bg-gradient-to-r from-blue-400 via-cyan-300 to-indigo-300 bg-clip-text text-transparent">
              every millisecond.
            </span>
          </h1>
          <p
            className="mt-4 animate-fade-up text-sm leading-relaxed text-slate-400"
            style={{ animationDelay: '140ms' }}
          >
            Transcripts, latency breakdowns and the knowledge-base sources behind every answer —
            across every client and campaign, in one console.
          </p>

          <div className="mt-8 animate-fade-up" style={{ animationDelay: '220ms' }}>
            <ConversationPreview />
          </div>
        </div>

        <p className="relative text-2xs text-slate-600">
          Authorised access only · sessions are recorded against your account
        </p>
      </div>

      {/* ── form ─────────────────────────────────────────────────────────── */}
      <div className="relative flex items-center justify-center overflow-hidden bg-background px-6 py-12">
        <div
          aria-hidden
          className="pointer-events-none absolute -top-40 right-0 h-96 w-96 rounded-full bg-primary/5 blur-3xl"
        />

        <div className="relative w-full max-w-[23rem] animate-fade-up">
          <div className="mb-10 flex items-center gap-2.5 lg:hidden">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 text-white">
              <Radio className="h-4 w-4" />
            </span>
            <span className="font-semibold tracking-tight">Voice Console</span>
          </div>

          <h2 className="text-[1.75rem] font-semibold leading-tight tracking-tight">
            Welcome back
          </h2>
          <p className="mt-1.5 text-sm text-muted-foreground">
            Sign in with the account your administrator issued.
          </p>

          <form onSubmit={onSubmit} className="mt-8 space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="email">Email address</Label>
              <div className="relative">
                <Mail className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
                <Input
                  id="email"
                  type="email"
                  autoComplete="username"
                  required
                  autoFocus
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="h-10 pl-9"
                  placeholder="you@company.com"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="password">Password</Label>
              <div className="relative">
                <Lock className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
                <Input
                  id="password"
                  type={reveal ? 'text' : 'password'}
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="h-10 pl-9 pr-10"
                  placeholder="••••••••••••"
                />
                <button
                  type="button"
                  onClick={() => setReveal((r) => !r)}
                  className="absolute right-0 top-0 grid h-10 w-10 place-items-center rounded-r-md text-muted-foreground transition-colors hover:text-foreground"
                  aria-label={reveal ? 'Hide password' : 'Show password'}
                  tabIndex={-1}
                >
                  {reveal ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            {error && (
              <div
                role="alert"
                className="flex animate-fade-in items-start gap-2 rounded-lg bg-danger/10 p-3 text-xs text-danger ring-1 ring-inset ring-danger/20"
              >
                <AlertCircle className="mt-px h-3.5 w-3.5 shrink-0" />
                <span>{error}</span>
              </div>
            )}

            <Button
              type="submit"
              size="lg"
              loading={busy}
              className="group w-full bg-gradient-to-r from-blue-600 to-indigo-600 shadow-md shadow-blue-600/20 transition-all hover:from-blue-600 hover:to-indigo-500 hover:shadow-lg hover:shadow-blue-600/25"
            >
              {busy ? 'Signing in…' : 'Sign in'}
              {!busy && (
                <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
              )}
            </Button>
          </form>

          <p className="mt-10 text-center text-2xs text-muted-foreground">
            Trouble signing in? Contact your administrator.
          </p>

          {/* In the form column rather than the brand panel: that panel is
              hidden below lg, and this is the half of the page everybody sees.
              120px matches the sidebar - one size for the mark across the
              product, so it does not read as two different logos. */}
          <div className="mt-8 border-t border-border/60 pt-6">
            <img
              src="/worxpertise.png"
              alt="Worxpertise"
              className="mx-auto w-[120px] dark:brightness-0 dark:invert"
              onError={(e) => {
                e.currentTarget.style.display = 'none'
              }}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
