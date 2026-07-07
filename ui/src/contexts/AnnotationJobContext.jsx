import { createContext, useCallback, useContext, useState } from 'react'
import { runAnnotationJob } from '../lib/api.js'

const AnnotationJobContext = createContext(null)

// App-wide tracker for the (single) running auto-annotate job. Pages start a
// job through startAnnotation and handle their own success/error side effects;
// the polled progress lives here so the floating card in Layout survives
// navigating between pages while the job runs.
export function AnnotationJobProvider({ children }) {
  const [job, setJob] = useState(null)

  const startAnnotation = useCallback(async body => {
    // Show the card immediately (indeterminate) instead of waiting for the
    // first poll to come back.
    setJob({ status: 'queued' })
    try {
      return await runAnnotationJob(body, setJob)
    } finally {
      setJob(null)
    }
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
