import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { getActiveAnnotationJob, pollAnnotationJob, runAnnotationJob } from '../lib/api.js'

const AnnotationJobContext = createContext(null)

// App-wide tracker for the (single) running auto-annotate job. Pages start a
// job through startAnnotation and handle their own success/error side effects;
// the polled progress lives here so the floating card in Layout survives
// navigating between pages while the job runs.
export function AnnotationJobProvider({ children }) {
  const [job, setJob] = useState(null)
  // Guards the mount re-attach from racing a locally started job.
  const attached = useRef(false)

  const startAnnotation = useCallback(async body => {
    // Show the card immediately (indeterminate) instead of waiting for the
    // first poll to come back.
    attached.current = true
    setJob({ status: 'queued' })
    try {
      return await runAnnotationJob(body, setJob)
    } finally {
      setJob(null)
      attached.current = false
    }
  }, [])

  // On mount (e.g. a reload mid-run), the server may still be working a job we
  // lost React state for. Re-attach the progress card by resuming the poll.
  useEffect(() => {
    let cancelled = false
    getActiveAnnotationJob()
      .then(active => {
        if (cancelled || !active || attached.current) return
        attached.current = true
        setJob(active)
        pollAnnotationJob(active.id, setJob)
          .catch(() => {})
          .finally(() => {
            setJob(null)
            attached.current = false
          })
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])

  return (
    <AnnotationJobContext.Provider value={{ job, startAnnotation }}>
      {children}
    </AnnotationJobContext.Provider>
  )
}

export function useAnnotationJob() {
  return useContext(AnnotationJobContext)
}
