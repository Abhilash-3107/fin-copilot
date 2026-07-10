import { Outlet, useLocation } from 'react-router-dom'
import Sidebar from './Sidebar.jsx'
import TopBar from './TopBar.jsx'
import AnnotationProgress from './AnnotationProgress.jsx'
import ErrorBoundary from './ErrorBoundary.jsx'
import { useAnnotationJob } from '../contexts/AnnotationJobContext.jsx'

export default function Layout() {
  const { job } = useAnnotationJob()
  const { pathname } = useLocation()
  return (
    <div className="flex h-screen bg-[#0f1117] overflow-hidden">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar />
        <main className="flex-1 overflow-y-auto">
          {/* Keyed on the route so navigating to another page clears a caught error. */}
          <ErrorBoundary resetKey={pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
      <AnnotationProgress job={job} />
    </div>
  )
}
