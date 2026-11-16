// localStorage that never throws: a browser with blocked or full storage just
// forgets the preference instead of crashing the provider that asked for it.
export function readStored(key) {
  try { return localStorage.getItem(key) } catch { return null }
}

export function writeStored(key, value) {
  try {
    if (value == null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch { /* storage unavailable; the choice lasts until the tab closes */ }
}
