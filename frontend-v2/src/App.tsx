import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth/AuthContext'
import { ErrorBoundary } from './components/ErrorBoundary'
import { Layout } from './components/Layout'
import { ToastProvider } from './components/Toast'
import { Analytics } from './pages/Analytics'
import { ArticleDetail } from './pages/ArticleDetail'
import { Articles } from './pages/Articles'
import { Dashboard } from './pages/Dashboard'
import { EntityDetail } from './pages/EntityDetail'
import { Login } from './pages/Login'
import { Monitors } from './pages/Monitors'
import { GeographicOverlay } from './pages/GeographicOverlay'
// Landscape + Opponents temporarily hidden 2026-05-30 at user request.
// Page component files preserved; routes redirect to / for now. Flip back on
// when product is ready to expose them again.
import { Narratives } from './pages/Narratives'
import { NarrativeDetail } from './pages/NarrativeDetail'
import { Notifications } from './pages/Notifications'
import { ReviewQueue } from './pages/ReviewQueue'
import { SearchResults } from './pages/SearchResults'
import { Setup } from './pages/Setup'
import { Timeline } from './pages/Timeline'

// Routes wrapped in an ErrorBoundary so a crash in one page only blanks
// that page's content area — the Layout (header + sidebar) keeps working,
// and the user can navigate away. The boundary is keyed on the pathname
// so it remounts on route change, clearing any error state from a prior
// page instead of trapping the user on the error screen.
function RoutesWithErrorBoundary() {
  const location = useLocation()
  return (
    <ErrorBoundary key={location.pathname}>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        {/* /forecast retired 2026-05-29 — the race-sentiment chart moved to
            /analytics. Recent-events cards were dropped; Timeline covers
            event/market impact in a richer way. Redirect kept so old
            bookmarks resolve. */}
        <Route path="/forecast" element={<Navigate to="/analytics" replace />} />
        {/* /briefing was consolidated into the homepage on 2026-05-29 —
            Race Situation, Needs Response, What Changed in the Race, and
            Activity This Week now render directly on /. Redirect kept so
            old bookmarks resolve. */}
        <Route path="/briefing" element={<Navigate to="/" replace />} />
        <Route path="/articles" element={<Articles />} />
        <Route path="/articles/:id" element={<ArticleDetail />} />
        <Route path="/narratives" element={<Narratives />} />
        <Route path="/narratives/:id" element={<NarrativeDetail />} />
        {/* /landscape temporarily hidden 2026-05-30 at user request. Redirect
            kept so old bookmarks resolve. Page component preserved. */}
        <Route path="/landscape" element={<Navigate to="/" replace />} />
        {/* /entity-network and /entity-review hidden 2026-05-29 —
            backing data is legacy v14.x extraction. v15.0 claim_records
            surface as evidence inside frames + briefing, not as a
            standalone graph page. Component files preserved for reference.
            See INTER_SESSION.md Session F. */}
        <Route path="/entities/:id" element={<EntityDetail />} />
        <Route path="/entity-network" element={<Navigate to="/" replace />} />
        <Route path="/entity-review" element={<Navigate to="/review" replace />} />
        <Route path="/map" element={<GeographicOverlay />} />
        <Route path="/timeline" element={<Timeline />} />
        <Route path="/review" element={<ReviewQueue />} />
        {/* /opponents temporarily hidden 2026-05-30 at user request. Redirect
            kept so old bookmarks resolve. Page component preserved. */}
        <Route path="/opponents" element={<Navigate to="/" replace />} />
        <Route path="/monitors" element={<Monitors />} />
        <Route path="/analytics" element={<Analytics />} />
        <Route path="/notifications" element={<Notifications />} />
        <Route path="/search" element={<SearchResults />} />
        {/* /setup is open to every signed-in user. Setup.tsx renders a
            read-only view + admin-only banner for non-admins and disables
            the cost-incurring action buttons (Pick race, Save, Discover);
            the backend require_admin gate is still the real authority. */}
        <Route path="/setup" element={<Setup />} />
      </Routes>
    </ErrorBoundary>
  )
}

// Gate every non-/login route on a logged-in user. While the AuthProvider
// is still validating the saved code against the backend, render a blank
// background so we don't flicker through the login page in the meantime.
function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) {
    return <div style={{ minHeight: '100vh', background: 'var(--bg-1)' }} />
  }
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }
  return <>{children}</>
}

// Wraps a route that should only be reachable by admins (Setup wizard is
// the main example — every meaningful action in there hits an LLM-gated
// backend endpoint, so a non-admin would just hit 403 walls). Non-admins
// land on the dashboard instead. Backend is still the real authority — this
// just keeps non-admins from seeing a screen full of buttons they can't use.
function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { user } = useAuth()
  if (!user?.isAdmin) {
    return <Navigate to="/" replace />
  }
  return <>{children}</>
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route
              path="/*"
              element={
                <RequireAuth>
                  <Layout>
                    <RoutesWithErrorBoundary />
                  </Layout>
                </RequireAuth>
              }
            />
          </Routes>
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  )
}
