import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { api, clearAccessCode, getAccessCode, setAccessCode } from '@/api/client'

export interface AuthUser {
  name: string
  initials: string
  color: string
  /**
   * Admin flag from the backend (sourced from the optional ":admin"
   * suffix in ACCESS_CODES). Currently used to gate the per-article
   * relevance bucket badge — non-admins still see the ordering, just
   * not the noisy CRITICAL/HIGH/MEDIUM/LOW chip.
   */
  isAdmin: boolean
}

interface AuthState {
  user: AuthUser | null
  // `loading` is true while we're checking the stored code against the
  // backend on mount. Routes should render a neutral splash during this
  // window rather than flicker through the login page.
  loading: boolean
  login: (code: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthState | null>(null)

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Always check /api/auth/me on mount, even with no saved code. The
    // backend returns a "Guest" user when no ACCESS_CODES are configured
    // (dev mode) so the app stays usable without auth setup; with codes
    // configured + no header, we get 401 and stay logged out, which sends
    // the user to /login as intended.
    api.getCurrentUser()
      .then(u => setUser({ name: u.name, initials: u.initials, color: u.color, isAdmin: !!u.is_admin }))
      .catch(() => {
        clearAccessCode()
        setUser(null)
      })
      .finally(() => setLoading(false))
  }, [])

  // The API client dispatches `cwr:unauthorized` whenever the backend
  // rejects a request with 401. That's our cue to drop React state too,
  // so the route guard pushes the user back to /login.
  useEffect(() => {
    const onUnauth = () => setUser(null)
    window.addEventListener('cwr:unauthorized', onUnauth)
    return () => window.removeEventListener('cwr:unauthorized', onUnauth)
  }, [])

  const login = useCallback(async (code: string) => {
    // Verify BEFORE saving — a bad code shouldn't pollute localStorage.
    const verified = await api.verifyAccessCode(code)
    setAccessCode(code)
    setUser({ name: verified.name, initials: verified.initials, color: verified.color, isAdmin: !!verified.is_admin })
  }, [])

  const logout = useCallback(() => {
    clearAccessCode()
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}
