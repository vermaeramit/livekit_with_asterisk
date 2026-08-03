import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { Layout } from '@/components/Layout'
import { Calls } from '@/pages/Calls'
import { CallDetail } from '@/pages/CallDetail'
import { Login } from '@/pages/Login'
import { useAuth } from '@/lib/auth'

function Protected({ children }: { children: React.ReactNode }) {
  const { user, ready } = useAuth()
  const location = useLocation()

  // Wait for the stored refresh token to be exchanged before deciding. Without
  // this a reload bounces the user to /login for a frame and loses their page.
  if (!ready) {
    return (
      <div className="grid h-screen place-items-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    )
  }
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />
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
      </Route>
      <Route path="*" element={<Navigate to="/calls" replace />} />
    </Routes>
  )
}
