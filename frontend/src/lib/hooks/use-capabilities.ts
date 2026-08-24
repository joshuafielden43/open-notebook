import { useQuery } from '@tanstack/react-query'
import { capabilitiesApi } from '@/lib/api/capabilities'

export const CAPABILITIES_QUERY_KEYS = {
  capabilities: ['capabilities'] as const,
}

/** Poll extraction and worker capabilities so failures surface app-wide. */
export function useCapabilities() {
  return useQuery({
    queryKey: CAPABILITIES_QUERY_KEYS.capabilities,
    queryFn: () => capabilitiesApi.get(),
    staleTime: 10_000,
    refetchInterval: 15_000,
    refetchOnWindowFocus: true,
    gcTime: 24 * 60 * 60 * 1000,
  })
}
