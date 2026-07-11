import { lazy, Suspense } from 'react'
import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import { ToastProvider } from './contexts/ToastContext.jsx'
import { StatementProvider } from './contexts/StatementContext.jsx'
import { PrivacyProvider } from './contexts/PrivacyContext.jsx'
import { PeriodProvider } from './contexts/PeriodContext.jsx'
import { AnnotationJobProvider } from './contexts/AnnotationJobContext.jsx'
import Layout from './components/Layout.jsx'

// Route-level code splitting: each page is its own chunk, so the ~150 KB
// Chart.js bundle only loads on the pages that render a chart (Dashboard,
// Insights, Settings) instead of on every route including the review queue.
const Dashboard = lazy(() => import('./pages/Dashboard.jsx'))
const Transactions = lazy(() => import('./pages/Transactions.jsx'))
const ReviewQueue = lazy(() => import('./pages/ReviewQueue.jsx'))
const Groups = lazy(() => import('./pages/Groups.jsx'))
const People = lazy(() => import('./pages/People.jsx'))
const Upload = lazy(() => import('./pages/Upload.jsx'))
const Insights = lazy(() => import('./pages/Insights.jsx'))
const Settings = lazy(() => import('./pages/Settings.jsx'))

function PageFallback() {
  return <div className="p-6 text-sm text-[#475569]">Loading…</div>
}

export default function App() {
  return (
    <ToastProvider>
      <StatementProvider>
      <PrivacyProvider>
      <PeriodProvider>
      <AnnotationJobProvider>
      <HashRouter>
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route index element={<Navigate to="/dashboard" replace />} />
            <Route path="dashboard" element={<Suspense fallback={<PageFallback />}><Dashboard /></Suspense>} />
            <Route path="transactions" element={<Suspense fallback={<PageFallback />}><Transactions /></Suspense>} />
            <Route path="review" element={<Suspense fallback={<PageFallback />}><ReviewQueue /></Suspense>} />
            <Route path="groups" element={<Suspense fallback={<PageFallback />}><Groups /></Suspense>} />
            <Route path="people" element={<Suspense fallback={<PageFallback />}><People /></Suspense>} />
            <Route path="upload" element={<Suspense fallback={<PageFallback />}><Upload /></Suspense>} />
            <Route path="insights" element={<Suspense fallback={<PageFallback />}><Insights /></Suspense>} />
            <Route path="settings" element={<Suspense fallback={<PageFallback />}><Settings /></Suspense>} />
          </Route>
        </Routes>
      </HashRouter>
      </AnnotationJobProvider>
      </PeriodProvider>
      </PrivacyProvider>
      </StatementProvider>
    </ToastProvider>
  )
}
