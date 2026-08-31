import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import {
  Bell, BookOpenCheck, Building2, Wallet, ChevronDown, Database, KeyRound, LayoutDashboard, LogOut, Megaphone, Menu, Moon, PhoneCall, Radio, Sun, Users2, X,
} from 'lucide-react'
import { TopProgress } from '@/components/TopProgress'
import { useAuth } from '@/lib/auth'
import { cn, initials } from '@/lib/utils'
import type { Role } from '@/types'

/**
 * The page shell. Every page uses this and nothing sets its own width.
 *
 * There were seven different widths across twelve pages - 896px to 1500px - and
 * the console read as several products stitched together. Each one was a
 * defensible choice on its own page and none of them were defensible next to
 * each other.
 *
 * A wide table is worth less than a console that looks like one thing.
 */
export const PAGE = 'mx-auto max-w-[1300px] space-y-5 p-5 lg:p-7'

type NavSection = { kind: 'section'; label: string }
type NavLinkItem = {
  kind: 'link'
  to: string
  label: string
  icon: React.ComponentType<{ className?: string }>
  roles?: Role[]
  soon?: boolean
  /** which live counter to show alongside the label */
  badge?: 'alerts' | 'gaps'
}
type NavItem = NavSection | NavLinkItem

const NAV: NavItem[] = [
  { kind: 'link', to: '/calls', label: 'Calls', icon: PhoneCall },
  { kind: 'link', to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { kind: 'link', to: '/live', label: 'Live monitor', icon: Radio },
  { kind: 'link', to: '/alerts', label: 'Alerts', icon: Bell, badge: 'alerts' },
  { kind: 'link', to: '/gaps', label: 'Knowledge gaps', icon: BookOpenCheck, badge: 'gaps' },
  { kind: 'section', label: 'Manage' },
  { kind: 'link', to: '/campaigns', label: 'Campaigns', icon: Megaphone, roles: ['tenant_admin'] },
  { kind: 'link', to: '/users', label: 'Users', icon: Users2, roles: ['tenant_admin'] },
  // roles: [] means superadmin only - the guard passes superadmin unconditionally
  { kind: 'link', to: '/tenants', label: 'Clients', icon: Building2, roles: [] },
  // Infrastructure, not a tenant's business - and the disk figures are about
  // the platform rather than anyone's campaigns.
  // Platform economics rather than a tenant's business: a wrong price here
  // misprices every call on the system, not one campaign's.
  { kind: 'link', to: '/rates', label: 'Provider rates', icon: Wallet, roles: [] },
  { kind: 'link', to: '/backups', label: 'Backups', icon: Database, roles: [] },
]

const ROLE_LABEL: Record<Role, string> = {
  superadmin: 'Super Admin',
  tenant_admin: 'Administrator',
  agent: 'Agent',
  viewer: 'Viewer',
}

function useTheme() {
  // Light is the default; dark is opt-in and remembered.
  const [dark, setDark] = useState(() => localStorage.getItem('aivoice.theme') === 'dark')
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    localStorage.setItem('aivoice.theme', dark ? 'dark' : 'light')
  }, [dark])
  return { dark, toggle: () => setDark((d) => !d) }
}

function Wordmark() {
  return (
    <div className="flex items-center gap-2.5">
      <span className="grid h-8 w-8 place-items-center rounded-lg bg-primary text-primary-foreground shadow-xs">
        <Radio className="h-4 w-4" />
      </span>
      <span className="flex flex-col leading-none">
        <span className="text-sm font-semibold tracking-tight text-foreground">Voice Console</span>
        <span className="mt-0.5 text-2xs text-muted-foreground">AI calling platform</span>
      </span>
    </div>
  )
}

function NavItems({ onNavigate }: { onNavigate?: () => void }) {
  const { user } = useAuth()

  // Polled in the shell rather than on the Alerts page, so something arriving
  // while you are looking at a call still shows up.
  const unread = useQuery({
    queryKey: ['alerts-unread'],
    queryFn: () => api<{ count: number }>('/alerts/unread-count'),
    refetchInterval: 60_000,
  })

  // Counts QUESTIONS, not occurrences: twenty callers asking the same thing is
  // one document to write, and a badge reading 20 would send someone looking
  // for twenty pieces of work that do not exist.
  const gaps = useQuery({
    queryKey: ['gaps-unread'],
    queryFn: () => api<{ count: number }>('/gaps/unread-count'),
    refetchInterval: 60_000,
  })

  const visible = NAV.filter((item) => {
    if (item.kind !== 'link' || !item.roles) return true
    if (user?.role === 'superadmin') return true
    return item.roles.includes(user!.role)
  })

  // A section heading with nothing under it is just a stray label.
  const pruned = visible.filter((item, i) => {
    if (item.kind === 'link') return true
    return visible.slice(i + 1).some((n) => n.kind === 'link')
  })

  return (
    <nav className="space-y-0.5">
      {pruned.map((item) => {
        if (item.kind === 'section') {
          return (
            <p
              key={`section-${item.label}`}
              className="px-2.5 pb-1 pt-4 text-2xs font-semibold uppercase tracking-wider text-muted-foreground/70"
            >
              {item.label}
            </p>
          )
        }

        const { to, label, icon: Icon, soon } = item
        if (soon) {
          return (
            <span
              key={to}
              title="Arriving in a later phase"
              className="flex cursor-default select-none items-center gap-2.5 rounded-md px-2.5 py-2 text-sm text-muted-foreground/55"
            >
              <Icon className="h-4 w-4" />
              {label}
              <span className="ml-auto rounded border border-border px-1 py-px text-2xs uppercase tracking-wide text-muted-foreground/70">
                soon
              </span>
            </span>
          )
        }

        return (
          <NavLink
            key={to}
            to={to}
            onClick={onNavigate}
            className={({ isActive }) =>
              cn(
                'relative flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors',
                isActive
                  ? 'bg-primary/10 font-semibold text-primary'
                  : 'text-sidebar-foreground hover:bg-accent hover:text-foreground',
              )
            }
          >
            {({ isActive }) => (
              <>
                {isActive && (
                  <span className="absolute inset-y-1.5 -left-2 w-[3px] rounded-full bg-primary" />
                )}
                <Icon className="h-4 w-4" />
                {label}
                {item.badge === 'alerts' && (unread.data?.count ?? 0) > 0 && (
                  <span className="ml-auto tnum rounded-full bg-danger px-1.5 py-px text-2xs font-semibold text-danger-foreground">
                    {unread.data!.count}
                  </span>
                )}
                {/* Muted, not red. A gap is work to do, not something on
                    fire - and a red badge that never clears stops being read. */}
                {item.badge === 'gaps' && (gaps.data?.count ?? 0) > 0 && (
                  <span className="ml-auto tnum rounded-full bg-muted px-1.5 py-px text-2xs font-semibold text-muted-foreground">
                    {gaps.data!.count}
                  </span>
                )}
              </>
            )}
          </NavLink>
        )
      })}
    </nav>
  )
}

function UserMenu() {
  const { user, signOut } = useAuth()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()

  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onEsc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onClick)
    document.addEventListener('keydown', onEsc)
    return () => {
      document.removeEventListener('mousedown', onClick)
      document.removeEventListener('keydown', onEsc)
    }
  }, [open])

  if (!user) return null

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 rounded-md py-1 pl-1 pr-1.5 transition-colors hover:bg-accent"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span className="grid h-7 w-7 place-items-center rounded-full bg-primary/10 text-2xs font-semibold text-primary ring-1 ring-inset ring-primary/20">
          {initials(user.name || user.email)}
        </span>
        <span className="hidden text-left sm:block">
          <span className="block text-xs font-medium leading-tight">{user.name || user.email}</span>
          <span className="block text-2xs leading-tight text-muted-foreground">
            {ROLE_LABEL[user.role]}
            {user.tenant_name ? ` · ${user.tenant_name}` : ''}
          </span>
        </span>
        <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 z-40 mt-1.5 w-60 animate-fade-in overflow-hidden rounded-lg border border-border bg-card shadow-lg"
        >
          <div className="border-b border-border px-3 py-2.5">
            <p className="truncate text-xs font-medium">{user.name || user.email}</p>
            <p className="truncate text-2xs text-muted-foreground">{user.email}</p>
            <p className="mt-1 text-2xs text-muted-foreground">
              {ROLE_LABEL[user.role]} · {user.tenant_name ?? 'All tenants'}
            </p>
          </div>
          <button
            role="menuitem"
            onClick={() => {
              setOpen(false)
              navigate('/change-password')
            }}
            className="flex w-full items-center gap-2 px-3 py-2.5 text-xs transition-colors hover:bg-accent"
          >
            <KeyRound className="h-3.5 w-3.5" />
            Change password
          </button>
          <button
            role="menuitem"
            onClick={async () => {
              await signOut()
              navigate('/login', { replace: true })
            }}
            className="flex w-full items-center gap-2 border-t border-border px-3 py-2.5 text-xs transition-colors hover:bg-accent"
          >
            <LogOut className="h-3.5 w-3.5" />
            Sign out
          </button>
        </div>
      )}
    </div>
  )
}

export function Layout() {
  const { dark, toggle } = useTheme()
  const [mobileOpen, setMobileOpen] = useState(false)

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      {/* Fixed, so it sits above everything and does not shift the layout when
          it appears. Held back 300ms - see TopProgress. */}
      <TopProgress />

      {/* desktop sidebar */}
      <aside className="hidden w-60 shrink-0 flex-col border-r border-border bg-sidebar md:flex">
        <div className="flex h-16 items-center px-5">
          <Wordmark />
        </div>
        <div className="flex-1 px-4 py-2">
          <NavItems />
        </div>
        <div className="border-t border-border px-5 py-3">
          <p className="text-2xs text-muted-foreground">Phase 2 · access &amp; campaigns</p>
        </div>
      </aside>

      {/* mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 md:hidden">
          <div
            className="absolute inset-0 bg-foreground/25 backdrop-blur-[1px]"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="absolute inset-y-0 left-0 flex w-64 animate-slide-in flex-col border-r border-border bg-sidebar shadow-lg">
            <div className="flex h-16 items-center justify-between px-5">
              <Wordmark />
              <button
                onClick={() => setMobileOpen(false)}
                className="rounded-md p-1.5 text-muted-foreground hover:bg-accent"
                aria-label="Close navigation"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="flex-1 px-4 py-2">
              <NavItems onNavigate={() => setMobileOpen(false)} />
            </div>
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center justify-between gap-3 border-b border-border bg-card px-4 lg:px-6">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setMobileOpen(true)}
              className="rounded-md p-2 text-muted-foreground transition-colors hover:bg-accent md:hidden"
              aria-label="Open navigation"
            >
              <Menu className="h-4 w-4" />
            </button>
            <div className="md:hidden">
              <Wordmark />
            </div>
          </div>

          <div className="flex items-center gap-1">
            <button
              onClick={toggle}
              className="rounded-md p-2 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              aria-label={dark ? 'Switch to light theme' : 'Switch to dark theme'}
              title={dark ? 'Light theme' : 'Dark theme'}
            >
              {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
            <div className="mx-1 h-5 w-px bg-border" />
            <UserMenu />
          </div>
        </header>

        <main className="scrollbar-thin flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

/** Shared page heading so every screen has the same rhythm. */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: React.ReactNode
  description?: React.ReactNode
  actions?: React.ReactNode
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}
