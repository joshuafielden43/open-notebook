import { afterEach, describe, expect, it } from 'vitest'
import { clearAuthStorage, getAuthToken } from './auth-token'

describe('getAuthToken', () => {
  afterEach(() => {
    clearAuthStorage()
  })

  it('reads token from sessionStorage (current store)', () => {
    sessionStorage.setItem(
      'auth-storage',
      JSON.stringify({ state: { token: 'from-session', isAuthenticated: true } })
    )
    expect(getAuthToken()).toBe('from-session')
  })

  it('falls back to localStorage for legacy builds', () => {
    localStorage.setItem(
      'auth-storage',
      JSON.stringify({ state: { token: 'from-local', isAuthenticated: true } })
    )
    expect(getAuthToken()).toBe('from-local')
  })

  it('prefers sessionStorage over localStorage', () => {
    sessionStorage.setItem(
      'auth-storage',
      JSON.stringify({ state: { token: 'session-wins' } })
    )
    localStorage.setItem(
      'auth-storage',
      JSON.stringify({ state: { token: 'local-loses' } })
    )
    expect(getAuthToken()).toBe('session-wins')
  })
})
