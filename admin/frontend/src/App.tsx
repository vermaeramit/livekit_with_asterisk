import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { Layout } from '@/components/Layout'
import { Calls } from '@/pages/Calls'
import { CallDetail } from '@/pages/CallDetail'
import { Campaigns } from '@/pages/Campaigns'
import { ChangePassword } from '@/pages/ChangePassword'
import { Login } from '@/pages/Login'
import { Tenants } from '@/pages/Tenants'
import { Users } from '@/pages/Users'
import { useAuth } from '@/lib/auth'
import type { Role } from '@/types'

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
      </Route>
      <Route path="*" element={<Navigate to="/calls" replace />} />
    </Routes>
  )
}
