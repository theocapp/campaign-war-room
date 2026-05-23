import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { Analytics } from './pages/Analytics'
import { Dashboard } from './pages/Dashboard'
import { Monitors } from './pages/Monitors'
import { MorningBriefing } from './pages/MorningBriefing'
import { Narratives } from './pages/Narratives'
import { NarrativeDetail } from './pages/NarrativeDetail'
import { Opponents } from './pages/Opponents'
import { ReviewQueue } from './pages/ReviewQueue'
import { Setup } from './pages/Setup'

export default function App() {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/briefing" element={<MorningBriefing />} />
          <Route path="/narratives" element={<Narratives />} />
          <Route path="/narratives/:id" element={<NarrativeDetail />} />
          <Route path="/review" element={<ReviewQueue />} />
          <Route path="/opponents" element={<Opponents />} />
          <Route path="/monitors" element={<Monitors />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/setup" element={<Setup />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  )
}
