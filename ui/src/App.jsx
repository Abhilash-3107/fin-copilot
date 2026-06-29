import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import { ToastProvider } from './contexts/ToastContext.jsx'
import { StatementProvider } from './contexts/StatementContext.jsx'
import Layout from './components/Layout.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Transactions from './pages/Transactions.jsx'
import ReviewQueue from './pages/ReviewQueue.jsx'
import Groups from './pages/Groups.jsx'
import People from './pages/People.jsx'
import Upload from './pages/Upload.jsx'
import Insights from './pages/Insights.jsx'
import Settings from './pages/Settings.jsx'

export default function App() {
  return (
    <ToastProvider>
      <StatementProvider>
      <HashRouter>
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route index element={<Navigate to="/dashboard" replace />} />
            <Route path="dashboard" element={<Dashboard />} />
            <Route path="transactions" element={<Transactions />} />
            <Route path="review" element={<ReviewQueue />} />
            <Route path="groups" element={<Groups />} />
            <Route path="people" element={<People />} />
            <Route path="upload" element={<Upload />} />
            <Route path="insights" element={<Insights />} />
            <Route path="settings" element={<Settings />} />
          </Route>
        </Routes>
      </HashRouter>
      </StatementProvider>
    </ToastProvider>
  )
}
