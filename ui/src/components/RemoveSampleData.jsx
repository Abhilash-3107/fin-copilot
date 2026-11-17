import { useState } from 'react'
import { api } from '../lib/api.js'
import { reloadWithToast, useToast } from '../contexts/ToastContext.jsx'
import ConfirmDialog from './ConfirmDialog.jsx'
import { couldnt } from '../lib/errors.js'

// The one way out of the sample ledger, used by the banner, the upload page and
// Settings > Data. Asks first, removes only the sample rows, then reloads onto
// the dashboard so no page keeps figures from the data that just went.
export default function RemoveSampleData({ className }) {
  const toast = useToast()
  const [asking, setAsking] = useState(false)
  const [busy, setBusy] = useState(false)

  async function remove() {
    setAsking(false)
    setBusy(true)
    try {
      await api.delete('/data/demo')
      window.location.hash = '#/dashboard'
      reloadWithToast('Sample data removed.', 'info')
    } catch (e) {
      toast(couldnt('remove the sample data', e), 'error', 5000)
      setBusy(false)
    }
  }

  return (
    <>
      <button type="button" className={className} disabled={busy} onClick={() => setAsking(true)}>
        {busy ? 'Removing…' : 'Remove sample data'}
      </button>
      <ConfirmDialog
        open={asking}
        danger
        title="Remove the sample data?"
        description="The sample payments, people, groups and rule go away. Anything you added yourself stays."
        confirmLabel="Remove sample data"
        onConfirm={remove}
        onCancel={() => setAsking(false)}
      />
    </>
  )
}
