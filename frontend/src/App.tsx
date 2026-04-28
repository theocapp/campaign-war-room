import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import IssueTracker from './pages/IssueTracker'
import OpponentTracker from './pages/OpponentTracker'
import CanvassingInsights from './pages/CanvassingInsights'
import TalkingPoints from './pages/TalkingPoints'
import Sources from './pages/Sources'
import CampaignSetup from './pages/CampaignSetup'
import ReviewQueue from './pages/ReviewQueue'
import RssFeeds from './pages/RssFeeds'

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/issues" element={<IssueTracker />} />
        <Route path="/opponents" element={<OpponentTracker />} />
        <Route path="/review" element={<ReviewQueue />} />
        <Route path="/canvassing" element={<CanvassingInsights />} />
        <Route path="/talking" element={<TalkingPoints />} />
        <Route path="/sources" element={<Sources />} />
        <Route path="/feeds" element={<RssFeeds />} />
        <Route path="/campaign" element={<CampaignSetup />} />
      </Routes>
    </Layout>
  )
}
