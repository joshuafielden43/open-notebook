'use client'

import { AlertCircle } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { useCapabilities } from '@/lib/hooks/use-capabilities'
import { useTranslation } from '@/lib/hooks/use-translation'

/**
 * Global banner when the surreal-commands worker is not proven live.
 * Jobs (sources, embeddings, podcasts) queue forever without it.
 */
export function WorkerStatusBanner() {
  const { t } = useTranslation()
  const { data: capabilities, isError } = useCapabilities()

  // Fail open on probe error so a capabilities outage does not brick the UI.
  if (isError || !capabilities) {
    return null
  }
  if (capabilities.worker_likely_ready !== false) {
    return null
  }

  return (
    <div className="px-4 pt-3">
      <Alert variant="destructive">
        <AlertCircle className="h-4 w-4" />
        <AlertTitle>{t('podcasts.workerOfflineBannerTitle')}</AlertTitle>
        <AlertDescription>
          {t('podcasts.workerOfflineBannerDesc')}
        </AlertDescription>
      </Alert>
    </div>
  )
}
