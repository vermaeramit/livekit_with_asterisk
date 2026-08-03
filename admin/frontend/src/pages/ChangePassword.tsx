import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertCircle, Check, KeyRound, ShieldAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, Input, Label } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { ApiError, api, storeTokens } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn } from '@/lib/utils'
import type { TokenPair } from '@/types'

const RULES = [
  { label: 'At least 12 characters', test: (p: string) => p.length >= 12 },
  { label: 'A lowercase and an uppercase letter', test: (p: string) => /[a-z]/.test(p) && /[A-Z]/.test(p) },
  { label: 'At least one digit', test: (p: string) => /\d/.test(p) },
]

export function ChangePassword() {
  const { user, refreshUser } = useAuth()
  const navigate = useNavigate()
  const toast = useToast()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const forced = user?.must_change_password ?? false
  const passes = RULES.every((r) => r.test(next))
  const matches = next.length > 0 && next === confirm
  const ready = current.length > 0 && passes && matches

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      // The server revokes every session, including this one, and hands back a
      // fresh pair - store it or the next request 401s.
      const tokens = await api<TokenPair>('/auth/change-password', {
        method: 'POST',
        body: { current_password: current, new_password: next },
      })
      storeTokens(tokens)
      await refreshUser()
      toast.success('Password changed', 'You have been signed out of all other sessions.')
      navigate('/calls', { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not change the password')
      setCurrent('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-lg p-5 lg:p-7">
      {forced && (
        <div className="mb-5 flex items-start gap-2.5 rounded-lg bg-warning/10 p-3.5 text-sm ring-1 ring-inset ring-warning/25">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
          <div>
            <p className="font-medium">Choose your own password to continue</p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Your account was created with a password an administrator picked, so they can sign in
              as you until you replace it. The console stays locked until you do.
            </p>
          </div>
        </div>
      )}

      <Card>
        <div className="border-b border-border px-5 py-4">
          <h1 className="flex items-center gap-2 text-base font-semibold tracking-tight">
            <KeyRound className="h-4 w-4 text-muted-foreground" />
            Change password
          </h1>
        </div>

        <form onSubmit={onSubmit} className="space-y-4 px-5 py-4">
          <div className="space-y-1.5">
            <Label htmlFor="cp-current">{forced ? 'Password you were given' : 'Current password'}</Label>
            <Input
              id="cp-current"
              type="password"
              autoComplete="current-password"
              autoFocus
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="cp-new">New password</Label>
            <Input
              id="cp-new"
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
            />
            <ul className="mt-2 space-y-1">
              {RULES.map((r) => {
                const ok = r.test(next)
                return (
                  <li
                    key={r.label}
                    className={cn(
                      'flex items-center gap-1.5 text-2xs',
                      ok ? 'text-success' : 'text-muted-foreground',
                    )}
                  >
                    <Check className={cn('h-3 w-3', !ok && 'opacity-30')} />
                    {r.label}
                  </li>
                )
              })}
            </ul>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="cp-confirm">Confirm new password</Label>
            <Input
              id="cp-confirm"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
            {confirm.length > 0 && !matches && (
              <p className="text-2xs text-danger">The passwords do not match.</p>
            )}
          </div>

          {error && (
            <div
              role="alert"
              className="flex items-start gap-2 rounded-md bg-danger/10 p-3 text-xs text-danger ring-1 ring-inset ring-danger/20"
            >
              <AlertCircle className="mt-px h-3.5 w-3.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-1">
            {!forced && (
              <Button type="button" variant="ghost" onClick={() => navigate(-1)}>
                Cancel
              </Button>
            )}
            <Button type="submit" loading={busy} disabled={!ready}>
              Change password
            </Button>
          </div>
        </form>
      </Card>
    </div>
  )
}
