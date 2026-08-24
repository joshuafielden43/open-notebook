'use client'

import { useQuery } from '@tanstack/react-query'

import { commandsApi } from '@/lib/api/commands'

export const BACKGROUND_JOBS_QUERY_KEY = ['commands', 'active'] as const

/**
 * Active background command jobs (podcasts, ingest, embeddings, …).
 * Polls while any job is non-terminal so the ambient indicator stays live.
 */
export function useBackgroundJobs(options?: { enabled?: boolean }) {
  const enabled = options?.enabled ?? true

  const query = useQuery({
    queryKey: BACKGROUND_JOBS_QUERY_KEY,
    queryFn: () => commandsApi.listActive(50),
    enabled,
    staleTime: 5_000,
    refetchInterval: (current) => {
      const jobs = current.state.data
      if (!jobs || jobs.length === 0) {
        // Keep a slow pulse so newly queued work appears without a full reload.
        return 30_000
      }
      return 10_000
    },
  })

  const jobs = query.data ?? []
  const activeCount = jobs.length
  return {
    ...query,
    jobs,
    activeCount,
  }
}
