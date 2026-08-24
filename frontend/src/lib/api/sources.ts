import type { AxiosResponse } from 'axios'

import apiClient from './client'
import { 
  SourceListResponse, 
  SourceDetailResponse, 
  SourceResponse,
  SourceStatusResponse,
  CreateSourceRequest, 
  UpdateSourceRequest 
} from '@/lib/types/api'

export type SourceSortField = 'type' | 'title' | 'created' | 'updated' | 'insights_count' | 'embedded'

/** API max for GET /sources limit (see api/routers/sources.py). */
export const SOURCES_PAGE_MAX = 100

export const sourcesApi = {
  list: async (params?: {
    notebook_id?: string
    limit?: number
    offset?: number
    sort_by?: SourceSortField
    sort_order?: 'asc' | 'desc'
  }) => {
    const response = await apiClient.get<SourceListResponse[]>('/sources', { params })
    return response.data
  },

  /**
   * Every source in a notebook. Pages at the API ceiling until a short page
   * — never stop at the default first-page 50.
   */
  listAllForNotebook: async (notebookId: string): Promise<SourceListResponse[]> => {
    const all: SourceListResponse[] = []
    let offset = 0
    for (;;) {
      const page = await sourcesApi.list({
        notebook_id: notebookId,
        limit: SOURCES_PAGE_MAX,
        offset,
        sort_by: 'updated',
        sort_order: 'desc',
      })
      all.push(...page)
      if (page.length < SOURCES_PAGE_MAX) {
        break
      }
      offset += page.length
    }
    return all
  },

  get: async (id: string) => {
    const response = await apiClient.get<SourceDetailResponse>(`/sources/${id}`)
    return response.data
  },

  create: async (data: CreateSourceRequest & { file?: File }) => {
    // Always use FormData to match backend expectations
    const formData = new FormData()
    
    // Add basic fields
    formData.append('type', data.type)
    
    if (data.notebooks !== undefined) {
      formData.append('notebooks', JSON.stringify(data.notebooks))
    }
    if (data.notebook_id) {
      formData.append('notebook_id', data.notebook_id)
    }
    if (data.title) {
      formData.append('title', data.title)
    }
    if (data.url) {
      formData.append('url', data.url)
    }
    if (data.content) {
      formData.append('content', data.content)
    }
    if (data.transformations !== undefined) {
      formData.append('transformations', JSON.stringify(data.transformations))
    }
    
    const dataWithFile = data as CreateSourceRequest & { file?: File }
    if (dataWithFile.file instanceof File) {
      formData.append('file', dataWithFile.file)
    }
    
    formData.append('embed', String(data.embed ?? false))
    formData.append('delete_source', String(data.delete_source ?? false))
    
    const response = await apiClient.post<SourceResponse>('/sources', formData)
    return response.data
  },

  update: async (id: string, data: UpdateSourceRequest) => {
    const response = await apiClient.put<SourceListResponse>(`/sources/${id}`, data)
    return response.data
  },

  delete: async (id: string) => {
    await apiClient.delete(`/sources/${id}`)
  },

  status: async (id: string) => {
    const response = await apiClient.get<SourceStatusResponse>(`/sources/${id}/status`)
    return response.data
  },

  upload: async (file: File, notebook_id: string) => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('notebook_id', notebook_id)
    formData.append('type', 'upload')
    
    const response = await apiClient.post<SourceResponse>('/sources', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    })
    return response.data
  },

  retry: async (id: string) => {
    const response = await apiClient.post<SourceResponse>(`/sources/${id}/retry`)
    return response.data
  },

  downloadFile: async (id: string): Promise<AxiosResponse<Blob>> => {
    return apiClient.get(`/sources/${id}/download`, {
      responseType: 'blob',
    })
  },
}
