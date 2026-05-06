import { useEffect } from 'react'
import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import ErrorBoundary from './components/ErrorBoundary'
import { ToastProvider, useToast } from './components/Toast'
import { registerToast } from './api/client'
import Dashboard from './pages/Dashboard'
import IssueTracker from './pages/IssueTracker'
import OpponentTracker from './pages/OpponentTracker'
import CanvassingInsights from './pages/CanvassingInsights'
import TalkingPoints from './pages/TalkingPoints'
import Sources from './pages/Sources'
import CampaignSetup from './pages/CampaignSetup'
import ReviewQueue from './pages/ReviewQueue'
import RssFeeds from './pages/RssFeeds'
import Monitors from './pages/Monitors'
import MessageLibrary from './pages/MessageLibrary'
import Narratives from './pages/Narratives'
import NarrativeDetail from './pages/NarrativeDetail'
import MessageBattle from './pages/MessageBattle'

function ToastRegistrar() {
  const { addToast } = useToast()
  useEffect(() => { registerToast(addToast) }, [addToast])
  return null
}

function AppRoutes() {
  return (
    <Layout>
      <ErrorBoundary>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/narratives" element={<Narratives />} />
          <Route path="/battle" element={<MessageBattle />} />
          <Route path="/issues" element={<IssueTracker />} />
          <Route path="/opponents" element={<OpponentTracker />} />
          <Route path="/review" element={<ReviewQueue />} />
          <Route path="/canvassing" element={<CanvassingInsights />} />
          <Route path="/talking" element={<TalkingPoints />} />
          <Route path="/sources" element={<Sources />} />
          <Route path="/monitors" element={<Monitors />} />
          <Route path="/message-library" element={<MessageLibrary />} />
          <Route path="/feeds" element={<RssFeeds />} />
          <Route path="/campaign" element={<CampaignSetup />} />
          <Route path="/narratives/:id" element={<NarrativeDetail />} />
        </Routes>
      </ErrorBoundary>
    </Layout>
  )
}

export default function App() {
  return (
    <ToastProvider>
      <ToastRegistrar />
      <AppRoutes />
    </ToastProvider>
  )
}
