import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Database, HardDrive, KeyRound, TriangleAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader, CardTitle, Skeleton } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { api } from '@/lib/api'
import { formatDateTime, formatRelative } from '@/lib/utils'
import type { BackupStatus } from '@/types'

function size(bytes: number | null | undefined): string {
  if (bytes == null) return '—'
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${Math.round(bytes / 1024)} KB`
}

export function Backups() {
  const qc = useQueryClient()
  const toast = useToast()
  const q = useQuery({
    queryKey: ['backups'],
    queryFn: () => api<BackupStatus>('/system/backups'),
    refetchInterval: 60_000,
  })

  const ack = useMutation({
    mutationFn: () => api('/system/acks/secrets-key', { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['backups'] })
      toast.success('Recorded', 'The warning stays gone until SECRETS_KEY changes.')
    },
    onError: (e) => toast.error('Could not record it', (e as Error).message),
  })

  if (q.isLoading) {
    return (
      <div className="mx-auto max-w-4xl space-y-5 p-5 lg:p-7">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-32" />
      </div>
    )
  }

  // Without this the page renders an empty shell when the request fails: every
  // figure reads "—", the list says "nothing here yet", and it looks exactly
  // like a server with no backups. That is the same failure the recording
  // player had - a component that cannot say why it is empty.
  if (q.isError || !q.data) {
    return (
      <div className="mx-auto max-w-4xl space-y-5 p-5 lg:p-7">
        <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
          <Database className="h-5 w-5 text-muted-foreground" />
          Backups
        </h1>
        <Card className="border-danger/30 bg-danger/5 p-4">
          <div className="flex items-start gap-2 text-sm">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
            <div>
              <p className="font-medium">Could not read the backup status</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {(q.error as Error | null)?.message ?? 'the request returned nothing'}
              </p>
              <p className="mt-2 text-xs text-muted-foreground">
                This says nothing about whether backups are running — only that this page
                cannot see them. Check on the server:{' '}
                <span className="font-mono">ls -la /opt/aivoice/backups</span>
              </p>
            </div>
          </div>
        </Card>
      </div>
    )
  }

  const s = q.data
  const healthy = s.configured && !s.problem

  return (
    <div className="mx-auto max-w-4xl space-y-5 p-5 lg:p-7">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
          <Database className="h-5 w-5 text-muted-foreground" />
          Backups
        </h1>
        <p className="mt-1 text-xs text-muted-foreground">
          Nightly dumps of the database — every call and transcript, the campaign
          configuration, the knowledge base, and the encrypted provider keys.
        </p>
      </div>

      {/* The headline. A list of files does not answer "is this working"; the age
          of the newest one does — and a timer that is armed but failing every
          night looks identical to a working one until somebody checks. */}
      {s?.problem ? (
        <Card className="border-danger/30 bg-danger/5 p-4">
          <div className="flex items-start gap-2 text-sm">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
            <div>
              <p className="font-medium">Backups are not healthy</p>
              <p className="mt-0.5 text-xs text-muted-foreground">{s.problem}</p>
            </div>
          </div>
        </Card>
      ) : healthy ? (
        <Card className="border-success/30 bg-success/5 p-4">
          <div className="flex items-start gap-2 text-sm">
            <Check className="mt-0.5 h-4 w-4 shrink-0 text-success" />
            <div>
              <p className="font-medium">
                Last backup {s.newest_at ? formatRelative(s.newest_at) : '—'}
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {s.files.length} kept, {size(s.total_bytes)} in total. Each one is read back
                with pg_restore before it is kept — a dump nobody has verified is a file,
                not a backup.
              </p>
            </div>
          </div>
        </Card>
      ) : null}

      {/* Nothing here can look inside a password manager, so this is a person's
          statement rather than a check. It was a permanent warning before, and a
          warning that can never be satisfied is one people learn to scroll past -
          taking the real ones with it. */}
      {s.secrets_key_ack && !s.secrets_key_ack.stale ? (
        <Card className="p-4">
          <div className="flex items-start gap-2 text-sm">
            <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
            <div>
              <p className="font-medium">
                <span className="font-mono">SECRETS_KEY</span> is stored off this server
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                Confirmed by {s.secrets_key_ack.acked_by ?? 'someone'}
                {s.secrets_key_ack.acked_at
                  ? ` on ${formatDateTime(s.secrets_key_ack.acked_at)}`
                  : ''}
                . This is a statement, not a check — and it will come back on its own if
                the key is ever rotated.
              </p>
            </div>
          </div>
        </Card>
      ) : (
        <Card className="border-warning/30 bg-warning/5 p-4">
          <div className="flex items-start gap-2 text-sm">
            <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
            <div className="min-w-0 flex-1">
              <p className="font-medium">
                {s.secrets_key_ack?.stale
                  ? 'SECRETS_KEY has changed since it was last stored'
                  : 'A dump alone cannot restore this system'}
              </p>
              <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                {s.secrets_key_ack?.stale ? (
                  <>
                    The key on this server no longer matches the one confirmed as stored.
                    Every dump taken since is encrypted with the new key — store that one
                    too, then confirm again.
                  </>
                ) : (
                  <>
                    Provider keys, tool credentials and the postback secret are encrypted in
                    these dumps. The key that opens them —{' '}
                    <span className="font-mono">SECRETS_KEY</span> — is in the server&rsquo;s{' '}
                    <span className="font-mono">.env</span>, not in the database. Restore
                    without it and every campaign stops taking calls.
                  </>
                )}
              </p>
              <Button
                size="sm"
                variant="outline"
                className="mt-3"
                loading={ack.isPending}
                onClick={() => ack.mutate()}
              >
                I have stored it somewhere else
              </Button>
            </div>
          </div>
        </Card>
      )}

      <div className="grid gap-3 sm:grid-cols-3">
        <Card className="p-4">
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Database className="h-3.5 w-3.5" />
            Newest
          </div>
          <p className="mt-2 text-xl font-semibold leading-none">
            {s?.newest_at ? formatRelative(s.newest_at) : 'never'}
          </p>
          {s?.age_hours != null && (
            <p className="mt-1.5 text-2xs text-muted-foreground">
              {s.age_hours < 24
                ? `${Math.round(s.age_hours)} hours ago`
                : `${Math.floor(s.age_hours / 24)} days ago`}
            </p>
          )}
        </Card>

        <Card className="p-4">
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Check className="h-3.5 w-3.5" />
            Last run
          </div>
          <p className="mt-2 text-xl font-semibold leading-none">{s?.last_result ?? '—'}</p>
          <p className="mt-1.5 text-2xs text-muted-foreground">
            {s?.last_run ? formatDateTime(s.last_run) : 'no run recorded'}
          </p>
        </Card>

        <Card className="p-4">
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <HardDrive className="h-3.5 w-3.5" />
            Disk free
          </div>
          <p className="mt-2 text-xl font-semibold leading-none">{size(s?.disk_free_bytes)}</p>
          <p className="mt-1.5 text-2xs text-muted-foreground">
            of {size(s?.disk_total_bytes)} — a full disk stops backups silently
          </p>
        </Card>
      </div>

      <Card className="overflow-hidden">
        <CardHeader className="flex items-center justify-between">
          <CardTitle>Kept dumps</CardTitle>
          <span className="text-2xs text-muted-foreground">14 day retention</span>
        </CardHeader>
        {!s?.files.length ? (
          <CardBody className="text-center text-xs text-muted-foreground">
            Nothing here yet. The timer and the install steps are in docs/DATABASE.md.
          </CardBody>
        ) : (
          <div className="divide-y divide-border/60">
            {s.files.map((f) => (
              <div key={f.name} className="flex items-center justify-between gap-4 px-4 py-2.5">
                <span className="truncate font-mono text-xs">{f.name}</span>
                <span className="flex shrink-0 items-center gap-4 text-2xs text-muted-foreground">
                  <span className="tnum">{size(f.bytes)}</span>
                  <span className="tnum">{formatDateTime(f.at)}</span>
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>

      <p className="text-2xs leading-relaxed text-muted-foreground">
        These dumps sit on the same disk as the database. That covers a bad migration or a
        campaign someone wiped, and none of the cases that end with the disk itself.
        Restoring, and the one line that would fix that, are in{' '}
        <span className="font-mono">docs/DATABASE.md</span>.
      </p>
    </div>
  )
}
