import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import {
  Building2,
  ChevronDown,
  LayoutDashboard,
  LogOut,
  Megaphone,
  Moon,
  PhoneCall,
  Radio,
  Sun,
} from 'lucide-react'
import { useAuth } from '@/lib/auth'
import { cn, initials } from '@/lib/utils'
import type { Role } from '@/types'

interface NavItem {
  to: string
  label: string
  icon: React.ComponentType<{ className?: string }>
  roles?: Role[]
  soon?: boolean
}

const NAV: NavItem[] = [
  { to: '/calls', label: 'Calls', icon: PhoneCall },
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard, soon: true },
  { to: '/campaigns', label: 'Campaigns', icon: Megaphone, roles: ['tenant_admin'], soon: true },
  { to: '/live', label: 'Live', icon: Radio, soon: true },
  { to: '/tenants', label: 'Tenants', icon: Building2, roles: [], soon: true },
]

const ROLE_LABEL: Record<Role, string> = {
  superadmin: 'Super Admin',
  tenant_admin: 'Admin',
  agent: 'Agent',
  viewer: 'Viewer',
}

function useTheme() {
  const [dark, setDark] = useState(() => localStorage.getItem('aivoice.theme') !== 'light')
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    localStorage.setItem('aivoice.theme', dark ? 'dark' : 'light')
  }, [dark])
  return { dark, toggle: () => setDark((d) => !d) }
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
        className="flex items-center gap-2 rounded-md px-2 py-1.5 transition-colors hover:bg-accent"
      >
        <span className="grid h-7 w-7 place-items-center rounded-full bg-primary text-[11px] font-semibold text-primary-foreground">
          {initials(user.name || user.email)}
        </span>
        <span className="hidden text-left sm:block">
          <span className="block text-xs font-medium leading-tight">{user.name || user.email}</span>
          <span className="block text-[11px] leading-tight text-muted-foreground">
            {ROLE_LABEL[user.role]}
            {user.tenant_name ? ` · ${user.tenant_name}` : ''}
          </span>
        </span>
        <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
      </button>

      {open && (
        <div className="absolute right-0 z-40 mt-1 w-56 animate-fade-in rounded-lg border border-border bg-card p-1 shadow-lg">
          <div className="px-2 py-1.5">
            <p className="truncate text-xs font-medium">{user.email}</p>
            <p className="text-[11px] text-muted-foreground">
              {user.tenant_name ?? 'All tenants'}
            </p>
          </div>
          <div className="my-1 h-px bg-border" />
          <button
            onClick={async () => {
              await signOut()
              navigate('/login', { replace: true })
            }}
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-xs transition-colors hover:bg-accent"
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
  const { user } = useAuth()
  const { dark, toggle } = useTheme()

  const visible = NAV.filter((item) => {
    if (!item.roles) return true
    if (user?.role === 'superadmin') return true
    return item.roles.includes(user!.role)
  })

  return (
    <div className="flex h-screen overflow-hidden">
      <aside className="hidden w-56 shrink-0 flex-col bg-sidebar text-sidebar-foreground md:flex">
        <div className="flex h-14 items-center gap-2 px-4">
          <span className="grid h-7 w-7 place-items-center rounded-md bg-primary text-primary-foreground">
            <Radio className="h-4 w-4" />
          </span>
          <span className="text-sm font-semibold tracking-tight">Voice Console</span>
        </div>

        <nav className="flex-1 space-y-0.5 px-2 py-2">
          {visible.map(({ to, label, icon: Icon, soon }) =>
            soon ? (
              <span
                key={to}
                className="flex cursor-not-allowed items-center gap-2.5 rounded-md px-2.5 py-2 text-sm opacity-40"
                title="Coming in a later phase"
              >
                <Icon className="h-4 w-4" />
                {label}
                <span className="ml-auto text-[10px] uppercase tracking-wide">soon</span>
              </span>
            ) : (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  cn(
                    'flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors',
                    isActive
                      ? 'bg-white/10 font-medium text-white'
                      : 'text-sidebar-foreground/75 hover:bg-white/5 hover:text-white',
                  )
                }
              >
                <Icon className="h-4 w-4" />
                {label}
              </NavLink>
            ),
          )}
        </nav>

        <div className="px-4 py-3 text-[11px] text-sidebar-foreground/40">
          Phase 1 · calls &amp; auth
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-border px-4">
          <div className="flex items-center gap-2 md:hidden">
            <Radio className="h-4 w-4 text-primary" />
            <span className="text-sm font-semibold">Voice Console</span>
          </div>
          <div className="hidden md:block" />
          <div className="flex items-center gap-1">
            <button
              onClick={toggle}
              className="rounded-md p-2 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              aria-label={dark ? 'Switch to light theme' : 'Switch to dark theme'}
            >
              {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
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
