import apiClient from './client'
import { NoteResponse, CreateNoteRequest, UpdateNoteRequest } from '@/lib/types/api'

/** API max for GET /notes limit (see api/routers/notes.py). */
export const NOTES_PAGE_MAX = 500

export const notesApi = {
  list: async (params?: {
    notebook_id?: string
    limit?: number
    offset?: number
  }) => {
    const response = await apiClient.get<NoteResponse[]>('/notes', { params })
    return response.data
  },

  /**
   * Every note in a notebook. Pages at the API ceiling until a short page.
   */
  listAllForNotebook: async (notebookId: string): Promise<NoteResponse[]> => {
    const all: NoteResponse[] = []
    let offset = 0
    for (;;) {
      const page = await notesApi.list({
        notebook_id: notebookId,
        limit: NOTES_PAGE_MAX,
        offset,
      })
      all.push(...page)
      if (page.length < NOTES_PAGE_MAX) {
        break
      }
      offset += page.length
    }
    return all
  },

  get: async (id: string) => {
    const response = await apiClient.get<NoteResponse>(`/notes/${id}`)
    return response.data
  },

  create: async (data: CreateNoteRequest) => {
    const response = await apiClient.post<NoteResponse>('/notes', data)
    return response.data
  },

  update: async (id: string, data: UpdateNoteRequest) => {
    const response = await apiClient.put<NoteResponse>(`/notes/${id}`, data)
    return response.data
  },

  delete: async (id: string) => {
    await apiClient.delete(`/notes/${id}`)
  }
}