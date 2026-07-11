import { useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import Sidebar from './Sidebar.jsx'
import TopBar from './TopBar.jsx'
import AnnotationProgress from './AnnotationProgress.jsx'
import CommandPalette from './CommandPalette.jsx'
import ErrorBoundary from './ErrorBoundary.jsx'
import { useAnnotationJob } from '../contexts/AnnotationJobContext.jsx'

export default function Layout() {
  const { job } = useAnnotationJob()
  const { pathname } = useLocation()
  // Off-canvas sidebar state, only meaningful below md; a route change closes it.
  const [navOpen, setNavOpen] = useState(false)

  return (
    <div className="flex h-screen bg-[#0f1117] overflow-hidden">
      <Sidebar open={navOpen} onClose={() => setNavOpen(false)} />
      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        <TopBar onMenu={() => setNavOpen(true)} />
        <main className="flex-1 overflow-y-auto">
          {/* Keyed on the route so navigating to another page clears a caught error. */}
          <ErrorBoundary resetKey={pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
      <AnnotationProgress job={job} />
      <CommandPalette />
    </div>
  )
}
