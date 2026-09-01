import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import * as apiClient from '@/lib/api'
import type { Permission, User } from '@/types'

interface AuthState {
  user: User | null
  ready: boolean
  signIn: (email: string, password: string) => Promise<void>
  signOut: () => Promise<void>
  refreshUser: () => Promise<void>
  can: (...permissions: Permission[]) => boolean
}

const AuthContext = createContext<AuthState | null>(null)

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [ready, setReady] = useState(false)

  // The api layer calls this when a refresh fails, so an expired session drops
  // straight to the login screen instead of leaving a half-dead UI behind.
  useEffect(() => {
    apiClient.setAuthLostHandler(() => setUser(null))
    return () => apiClient.setAuthLostHandler(null)
  }, [])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      if (await apiClient.restoreSession()) {
        try {
          const me = await apiClient.api<User>('/auth/me')
          if (!cancelled) setUser(me)
        } catch {
          apiClient.clearTokens()
        }
      }
      if (!cancelled) setReady(true)
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const signIn = useCallback(async (email: string, password: string) => {
    await apiClient.login(email, password)
    setUser(await apiClient.api<User>('/auth/me'))
  }, [])

  const signOut = useCallback(async () => {
    await apiClient.logout()
    setUser(null)
  }, [])

  const refreshUser = useCallback(async () => {
    setUser(await apiClient.api<User>('/auth/me'))
  }, [])

  /**
   * What this user may do, by permission rather than by role name.
   *
   * Roles are rows somebody can create now, so a name says nothing about what
   * it carries - a role called "desk" could hold anything. Every call site asks
   * for the capability it actually needs.
   *
   * This only hides things. The API refuses them either way, and the figures a
   * permission protects are never sent at all rather than sent and hidden.
   */
  const can = useCallback(
    (...permissions: Permission[]) => {
      if (!user) return false
      return permissions.every((p) => user.permissions?.includes(p))
    },
    [user],
  )

  const value = useMemo<AuthState>(
    () => ({ user, ready, signIn, signOut, refreshUser, can }),
    [user, ready, signIn, signOut, refreshUser, can],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
