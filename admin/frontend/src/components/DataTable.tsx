import { TriangleAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, EmptyState, Skeleton } from '@/components/ui/primitives'
import { cn } from '@/lib/utils'

export interface Column<T> {
  key: string
  header: string
  align?: 'left' | 'right'
  className?: string
  render: (row: T) => React.ReactNode
}

/**
 * The list shell every settings screen shares: loading, error, empty and data
 * all resolved in one place so the four states cannot drift apart between pages.
 */
export function DataTable<T>({
  columns,
  rows,
  isLoading,
  error,
  onRetry,
  empty,
  rowKey,
  onRowClick,
}: {
  columns: Column<T>[]
  rows: T[] | undefined
  isLoading?: boolean
  error?: Error | null
  onRetry?: () => void
  empty: { icon: React.ComponentType<{ className?: string }>; title: string; hint?: string; action?: React.ReactNode }
  rowKey: (row: T) => string | number
  onRowClick?: (row: T) => void
}) {
  const th =
    'px-4 py-2.5 text-left text-2xs font-semibold uppercase tracking-wider text-muted-foreground'
  const td = 'px-4 py-3 align-middle'

  return (
    <Card className="overflow-hidden">
      {error ? (
        <EmptyState
          icon={TriangleAlert}
          title="Could not load"
          hint={error.message}
          action={
            onRetry && (
              <Button size="sm" variant="outline" onClick={onRetry}>
                Try again
              </Button>
            )
          }
        />
      ) : isLoading ? (
        <div className="space-y-2.5 p-4">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : !rows?.length ? (
        <EmptyState {...empty} />
      ) : (
        <div className="scrollbar-thin overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-muted/50">
              <tr className="border-b border-border">
                {columns.map((c) => (
                  <th key={c.key} className={cn(th, c.align === 'right' && 'text-right')}>
                    {c.header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={rowKey(row)}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  className={cn(
                    'border-b border-border/70 transition-colors last:border-0',
                    onRowClick && 'cursor-pointer hover:bg-accent/50',
                  )}
                >
                  {columns.map((c) => (
                    <td
                      key={c.key}
                      className={cn(td, c.align === 'right' && 'text-right', c.className)}
                    >
                      {c.render(row)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
