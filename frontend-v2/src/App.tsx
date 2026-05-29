import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { Analytics } from './pages/Analytics'
import { ArticleDetail } from './pages/ArticleDetail'
import { Articles } from './pages/Articles'
import { Dashboard } from './pages/Dashboard'
import { Monitors } from './pages/Monitors'
import { MorningBriefing } from './pages/MorningBriefing'
import { Forecast } from './pages/Forecast'
import { GeographicOverlay } from './pages/GeographicOverlay'
import { Landscape } from './pages/Landscape'
import { Narratives } from './pages/Narratives'
import { NarrativeDetail } from './pages/NarrativeDetail'
import { Notifications } from './pages/Notifications'
import { Opponents } from './pages/Opponents'
import { ReviewQueue } from './pages/ReviewQueue'
import { SearchResults } from './pages/SearchResults'
import { Setup } from './pages/Setup'
import { Timeline } from './pages/Timeline'

export default function App() {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/forecast" element={<Forecast />} />
          <Route path="/briefing" element={<MorningBriefing />} />
          <Route path="/articles" element={<Articles />} />
          <Route path="/articles/:id" element={<ArticleDetail />} />
          <Route path="/narratives" element={<Narratives />} />
          <Route path="/narratives/:id" element={<NarrativeDetail />} />
          <Route path="/landscape" element={<Landscape />} />
          {/* /entity-network and /entity-review hidden 2026-05-29 —
              backing data is legacy v14.x extraction. v15.0 claim_records
              surface as evidence inside frames + briefing, not as a
              standalone graph page. Component files preserved for reference.
              See INTER_SESSION.md Session F. */}
          <Route path="/entity-network" element={<Navigate to="/" replace />} />
          <Route path="/entity-review" element={<Navigate to="/review" replace />} />
          <Route path="/map" element={<GeographicOverlay />} />
          <Route path="/timeline" element={<Timeline />} />
          <Route path="/review" element={<ReviewQueue />} />
          <Route path="/opponents" element={<Opponents />} />
          <Route path="/monitors" element={<Monitors />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/notifications" element={<Notifications />} />
          <Route path="/search" element={<SearchResults />} />
          <Route path="/setup" element={<Setup />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  )
}
