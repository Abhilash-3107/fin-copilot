import { useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import Sidebar from './Sidebar.jsx'
import TopBar from './TopBar.jsx'
import AnnotationProgress from './AnnotationProgress.jsx'
import CommandPalette from './CommandPalette.jsx'
import ShortcutSheet from './ShortcutSheet.jsx'
import ErrorBoundary from './ErrorBoundary.jsx'
import DemoBanner from './DemoBanner.jsx'
import InlineError from './InlineError.jsx'
import { useAnnotationJob } from '../contexts/AnnotationJobContext.jsx'
import { useStatement } from '../contexts/StatementContext.jsx'

export default function Layout() {
  const { job } = useAnnotationJob()
  const { error: statementsError, reload: reloadStatements } = useStatement()
  const { pathname } = useLocation()
  // Off-canvas sidebar state, only meaningful below md; a route change closes it.
  const [navOpen, setNavOpen] = useState(false)

  return (
    <div className="flex h-screen bg-[#0f1117] overflow-hidden">
      <Sidebar open={navOpen} onClose={() => setNavOpen(false)} />
      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        <DemoBanner />
        <TopBar onMenu={() => setNavOpen(true)} />
        <main className="flex-1 overflow-y-auto">
          {/* Keyed on the route so navigating to another page clears a caught error. */}
          {/* Add Statement shows its own list, with its own Retry. */}
          {statementsError && pathname !== '/upload' && (
            <InlineError what="your statements" error={statementsError} onRetry={reloadStatements} className="mx-5 mt-4" />
          )}
          <ErrorBoundary resetKey={pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
      <AnnotationProgress job={job} />
      <CommandPalette />
      <ShortcutSheet />
    </div>
  )
}
