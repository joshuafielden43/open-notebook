// Reads the auth token persisted by the zustand auth store
// (key `auth-storage` in sessionStorage; localStorage kept as a one-time
// fallback for older builds). Single source of truth for the token-parsing
// ritual — use this instead of re-reading storage ad hoc.

const AUTH_STORAGE_KEY = 'auth-storage'

function tokenFromRaw(raw: string | null): string | null {
  if (!raw) return null
  try {
    const { state } = JSON.parse(raw)
    return state?.token ?? null
  } catch (error) {
    console.error('Error parsing auth storage:', error)
    return null
  }
}

export function getAuthToken(): string | null {
  if (typeof window === 'undefined') {
    return null
  }

  // Prefer sessionStorage (current auth-store persist target).
  const fromSession = tokenFromRaw(window.sessionStorage.getItem(AUTH_STORAGE_KEY))
  if (fromSession) {
    return fromSession
  }

  // Legacy localStorage (pre sessionStorage migration).
  return tokenFromRaw(window.localStorage.getItem(AUTH_STORAGE_KEY))
}

/** Clear persisted auth from both storages (logout / 401). */
export function clearAuthStorage(): void {
  if (typeof window === 'undefined') {
    return
  }
  try {
    window.sessionStorage.removeItem(AUTH_STORAGE_KEY)
  } catch {
    // ignore
  }
  try {
    window.localStorage.removeItem(AUTH_STORAGE_KEY)
  } catch {
    // ignore
  }
}
