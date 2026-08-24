import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'

// Radix Select measures its trigger via ResizeObserver, which jsdom lacks.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub)

import { SettingsForm } from './SettingsForm'

// useTranslation is mocked globally in setup.ts (t returns the key string),
// so hint keys render as their literal key names below.

vi.mock('@/lib/hooks/use-settings', () => ({
  useSettings: vi.fn(),
  useUpdateSettings: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
}))

vi.mock('@/lib/hooks/use-capabilities', () => ({
  useCapabilities: vi.fn(),
}))

import { useSettings } from '@/lib/hooks/use-settings'
import { useCapabilities } from '@/lib/hooks/use-capabilities'

const settingsData = {
  default_content_processing_engine_doc: 'auto',
  default_content_processing_engine_url: 'auto',
  default_embedding_option: 'ask',
  auto_delete_files: 'no',
  docling_ocr: true,
}

function mockCapabilities(caps: unknown, { isError = false } = {}) {
  ;(useSettings as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    data: settingsData,
    isLoading: false,
    error: null,
  })
  ;(useCapabilities as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    data: caps,
    isError,
  })
}

describe('SettingsForm engine gating', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('disables OCR when the runtime is unavailable', () => {
    mockCapabilities({
      docling_available: false,
      crawl4ai_available: false,
      crawl4ai_remote_configured: false,
    })
    render(<SettingsForm />)

    // Target the OCR toggle by its accessible name (from the associated Label).
    expect(
      screen.getByRole('checkbox', { name: 'settings.ocrEnabled' })
    ).toBeDisabled()
  })

  it('enables OCR when the runtime is available', () => {
    mockCapabilities({
      docling_available: true,
      crawl4ai_available: true,
      crawl4ai_remote_configured: false,
    })
    render(<SettingsForm />)

    expect(
      screen.getByRole('checkbox', { name: 'settings.ocrEnabled' })
    ).not.toBeDisabled()
  })

  it('treats runtimes as available while the capability probe is still loading', () => {
    mockCapabilities(undefined)
    render(<SettingsForm />)

    // Optimistic default avoids a flash of disabled controls on a working setup.
    expect(
      screen.getByRole('checkbox', { name: 'settings.ocrEnabled' })
    ).not.toBeDisabled()
  })

  it('fails closed when the capability probe errors', () => {
    mockCapabilities(undefined, { isError: true })
    render(<SettingsForm />)

    // A failed probe must not advertise engines the backend couldn't verify.
    expect(
      screen.getByRole('checkbox', { name: 'settings.ocrEnabled' })
    ).toBeDisabled()
  })
})
