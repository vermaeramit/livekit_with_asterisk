import { Suspense, lazy } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { Layout } from '@/components/Layout'
import { Alerts } from '@/pages/Alerts'
import { KnowledgeGaps } from '@/pages/KnowledgeGaps'
import { Calls } from '@/pages/Calls'
import { CallDetail } from '@/pages/CallDetail'
import { Backups } from '@/pages/Backups'
import { Campaigns } from '@/pages/Campaigns'
import { CampaignConfig } from '@/pages/CampaignConfig'
import { ChangePassword } from '@/pages/ChangePassword'
import { Live } from '@/pages/Live'
import { Login } from '@/pages/Login'
import { Tenants } from '@/pages/Tenants'
import { Users } from '@/pages/Users'
import { useAuth } from '@/lib/auth'
import type { Role } from '@/types'

// Recharts is ~250 kB gzipped and only the dashboard needs it. Loading it
// eagerly tripled the bundle that the login screen has to fetch.
const Dashboard = lazy(() =>
  import('@/pages/Dashboard').then((m) => ({ default: m.Dashboard })),
)

function Spinner() {
  return (
    <div className="grid h-screen place-items-center">
      <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
    </div>
  )
}

function Protected({ children, roles }: { children: React.ReactNode; roles?: Role[] }) {
  const { user, ready } = useAuth()
  const location = useLocation()

  // Wait for the stored refresh token to be exchanged before deciding. Without
  // this a reload bounces the user to /login for a frame and loses their page.
  if (!ready) return <Spinner />
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />

  // The API refuses everything else for these accounts anyway (see deps.active_user);
  // this just makes the console say why instead of showing a wall of 403s.
  if (user.must_change_password && location.pathname !== '/change-password') {
    return <Navigate to="/change-password" replace />
  }

  if (roles && user.role !== 'superadmin' && !roles.includes(user.role)) {
    return <Navigate to="/calls" replace />
  }
  return <>{children}</>
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <Protected>
            <Layout />
          </Protected>
        }
      >
        <Route
          path="/dashboard"
          element={
            <Suspense fallback={<Spinner />}>
              <Dashboard />
            </Suspense>
          }
        />
        <Route path="/live" element={<Live />} />
        <Route path="/alerts" element={<Alerts />} />
        <Route path="/gaps" element={<KnowledgeGaps />} />
        <Route path="/calls" element={<Calls />} />
        <Route path="/calls/:id" element={<CallDetail />} />
        <Route path="/change-password" element={<ChangePassword />} />
        <Route
          path="/campaigns"
          element={
            <Protected roles={['tenant_admin']}>
              <Campaigns />
            </Protected>
          }
        />
        <Route
          path="/campaigns/:id/config"
          element={
            <Protected roles={['tenant_admin']}>
              <CampaignConfig />
            </Protected>
          }
        />
        <Route
          path="/users"
          element={
            <Protected roles={['tenant_admin']}>
              <Users />
            </Protected>
          }
        />
        <Route
          path="/tenants"
          element={
            <Protected roles={[]}>
              <Tenants />
            </Protected>
          }
        />
        {/* roles={[]} is superadmin only, same as Clients. Backups are
            infrastructure - a tenant admin cannot act on them, and the disk
            figures describe the platform rather than their campaigns. */}
        <Route
          path="/backups"
          element={
            <Protected roles={[]}>
              <Backups />
            </Protected>
          }
        />
      </Route>
      <Route path="*" element={<Navigate to="/calls" replace />} />
    </Routes>
  )
}
