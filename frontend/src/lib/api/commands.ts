import apiClient from './client'

export interface ActiveCommandJob {
  job_id: string
  name: string
  app: string
  status: string
  error_message?: string | null
  created?: string | null
  updated?: string | null
}

export interface CommandJobStatus {
  job_id: string
  status: string
  result?: Record<string, unknown> | null
  error_message?: string | null
  created?: string | null
  updated?: string | null
  progress?: Record<string, unknown> | null
}

export const commandsApi = {
  listActive: async (limit = 50) => {
    const response = await apiClient.get<ActiveCommandJob[]>('/commands/jobs', {
      params: { limit },
    })
    return response.data
  },

  getStatus: async (jobId: string) => {
    const response = await apiClient.get<CommandJobStatus>(
      `/commands/jobs/${encodeURIComponent(jobId)}`
    )
    return response.data
  },
}
