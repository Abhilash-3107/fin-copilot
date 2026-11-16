import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { JobBusyError, getActiveAnnotationJob, pollAnnotationJob, runAnnotationJob } from '../lib/api.js'

const AnnotationJobContext = createContext(null)

// App-wide tracker for the running auto-annotate job (and the queue behind it).
// Pages start a job through startAnnotation and handle their own success/error
// side effects;
// the polled progress lives here so the floating card in Layout survives
// navigating between pages while the job runs.
export function AnnotationJobProvider({ children }) {
  const [job, setJob] = useState(null)
  // Guards the mount re-attach from racing a locally started job.
  const attached = useRef(false)
  // The tracked job's kind, for a busy message that names what's running.
  const trackedKind = useRef(null)
  useEffect(() => { trackedKind.current = job?.kind ?? null }, [job])

  // Show the progress card for whatever job the server is working on, and
  // carry on to the next queued one when it ends. Used on mount (a reload
  // mid-run lost our state), and after a statement is uploaded: the server
  // starts sorting by itself, and a second statement queues behind the first.
  // A no-op while a job is already tracked: that loop picks up the next one.
  const mounted = useRef(true)
  const watchJobs = useCallback(async () => {
    if (attached.current) return
    let active
    try { active = await getActiveAnnotationJob() } catch { return }
    if (!active || attached.current || !mounted.current) return
    attached.current = true
    setJob(active)
    try {
      await pollAnnotationJob(active.id, setJob)
    } catch {
      // A failed run is reported by whoever started it; the card just goes.
    }
    setJob(null)
    attached.current = false
    if (mounted.current) watchJobs()
  }, [])

  const startAnnotation = useCallback(async body => {
    // A job this tab already tracks (started here or re-attached on mount)
    // holds the slot; don't overwrite its card with a run that can't start.
    if (attached.current) throw new JobBusyError(trackedKind.current)
    // Show the card immediately (indeterminate) instead of waiting for the
    // first poll to come back.
    attached.current = true
    setJob({ status: 'queued', kind: 'annotate' })
    try {
      return await runAnnotationJob(body, setJob)
    } finally {
      setJob(null)
      attached.current = false
      // Statements may have queued behind this run (an upload while it ran):
      // keep the card, and with it the disabled Sort again, until they finish.
      if (mounted.current) watchJobs()
    }
  }, [watchJobs])

  useEffect(() => {
    mounted.current = true
    watchJobs()
    return () => { mounted.current = false }
  }, [watchJobs])

  return (
    <AnnotationJobContext.Provider value={{ job, startAnnotation, watchJobs }}>
      {children}
    </AnnotationJobContext.Provider>
  )
}

export function useAnnotationJob() {
  return useContext(AnnotationJobContext)
}
