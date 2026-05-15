import { useEffect } from 'react'
import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import ErrorBoundary from './components/ErrorBoundary'
import { ToastProvider, useToast } from './components/Toast'
import { registerToast } from './api/client'
import MorningBriefing from './pages/MorningBriefing'
import Narratives from './pages/Narratives'
import ReviewQueue from './pages/ReviewQueue'
import OpponentTracker from './pages/OpponentTracker'
import RssFeeds from './pages/RssFeeds'
import CampaignSetup from './pages/CampaignSetup'

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
          <Route path="/" element={<MorningBriefing />} />
          <Route path="/briefing" element={<MorningBriefing />} />
          <Route path="/narratives" element={<Narratives />} />
          <Route path="/review" element={<ReviewQueue />} />
          <Route path="/opponents" element={<OpponentTracker />} />
          <Route path="/feeds" element={<RssFeeds />} />
          <Route path="/campaign" element={<CampaignSetup />} />
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
