import { beforeEach, describe, expect, it, vi } from 'vitest'

import apiClient from './client'
import { SOURCES_PAGE_MAX, sourcesApi } from './sources'

vi.mock('./client', () => ({
  default: { post: vi.fn(), get: vi.fn() },
}))

beforeEach(() => {
  vi.mocked(apiClient.get).mockReset()
  vi.mocked(apiClient.post).mockReset()
})

describe('sourcesApi.create', () => {
  it('omits the deprecated processing-mode flag', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: {} })

    await sourcesApi.create({
      type: 'text',
      content: 'Queued source',
      async_processing: false,
    })

    const [, formData] = vi.mocked(apiClient.post).mock.calls[0]
    expect((formData as FormData).has('async_processing')).toBe(false)
  })
})

describe('sourcesApi.listAllForNotebook', () => {
  it('pages until a short page so large notebooks are not truncated at 50', async () => {
    const page1 = Array.from({ length: SOURCES_PAGE_MAX }, (_, i) => ({ id: `source:${i}` }))
    const page2 = Array.from({ length: SOURCES_PAGE_MAX }, (_, i) => ({
      id: `source:${i + SOURCES_PAGE_MAX}`,
    }))
    const page3 = Array.from({ length: 50 }, (_, i) => ({
      id: `source:${i + SOURCES_PAGE_MAX * 2}`,
    }))
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: page1 })
      .mockResolvedValueOnce({ data: page2 })
      .mockResolvedValueOnce({ data: page3 })

    const all = await sourcesApi.listAllForNotebook('notebook:big')

    expect(all).toHaveLength(SOURCES_PAGE_MAX * 2 + 50)
    expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(3)
    expect(vi.mocked(apiClient.get).mock.calls[0][1]).toMatchObject({
      params: {
        notebook_id: 'notebook:big',
        limit: SOURCES_PAGE_MAX,
        offset: 0,
      },
    })
    expect(vi.mocked(apiClient.get).mock.calls[1][1]).toMatchObject({
      params: { offset: SOURCES_PAGE_MAX },
    })
    expect(vi.mocked(apiClient.get).mock.calls[2][1]).toMatchObject({
      params: { offset: SOURCES_PAGE_MAX * 2 },
    })
  })

  it('stops after a single short page', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: [{ id: 'source:1' }, { id: 'source:2' }],
    })

    const all = await sourcesApi.listAllForNotebook('notebook:small')
    expect(all).toHaveLength(2)
    expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(1)
  })
})
