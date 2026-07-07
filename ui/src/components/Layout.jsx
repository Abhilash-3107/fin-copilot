import { Outlet } from 'react-router-dom'
import Sidebar from './Sidebar.jsx'
import TopBar from './TopBar.jsx'
import AnnotationProgress from './AnnotationProgress.jsx'
import { useAnnotationJob } from '../contexts/AnnotationJobContext.jsx'

export default function Layout() {
  const { job } = useAnnotationJob()
  return (
    <div className="flex h-screen bg-[#0f1117] overflow-hidden">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar />
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
      <AnnotationProgress job={job} />
    </div>
  )
}
