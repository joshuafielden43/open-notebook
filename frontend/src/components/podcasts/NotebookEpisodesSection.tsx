'use client'

import { useCallback, useState } from 'react'
import Link from 'next/link'
import { AlertCircle, Loader2, Mic, RefreshCcw } from 'lucide-react'

import { useDeletePodcastEpisode, usePodcastEpisodes, useRetryPodcastEpisode } from '@/lib/hooks/use-podcasts'
import { EpisodeCard } from '@/components/podcasts/EpisodeCard'
import { GeneratePodcastDialog } from '@/components/podcasts/GeneratePodcastDialog'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useTranslation } from '@/lib/hooks/use-translation'

interface NotebookEpisodesSectionProps {
  notebookId: string
}

/**
 * Podcast strip for a single notebook. Lives below the workspace (scroll to
 * reach) so it never competes with Sources/Notes/Chat for height.
 */
export function NotebookEpisodesSection({ notebookId }: NotebookEpisodesSectionProps) {
  const { t } = useTranslation()
  const [showGenerateDialog, setShowGenerateDialog] = useState(false)
  const {
    episodes,
    statusCounts,
    isLoading,
    isError,
    refetch,
    isFetching,
  } = usePodcastEpisodes({ notebookId })
  const deleteEpisode = useDeletePodcastEpisode()
  const retryEpisode = useRetryPodcastEpisode()

  const handleDelete = useCallback(
    (episodeId: string) => deleteEpisode.mutateAsync(episodeId),
    [deleteEpisode]
  )

  const handleRetry = useCallback(
    async (episodeId: string) => { await retryEpisode.mutateAsync(episodeId) },
    [retryEpisode]
  )

  const podcastsHref = `/podcasts?notebook_id=${encodeURIComponent(notebookId)}`

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm">
          <Mic className="h-4 w-4 text-muted-foreground" />
          <span className="font-medium">{t('podcasts.notebookEpisodesTitle')}</span>
          {!isLoading ? (
            <Badge variant="secondary" className="font-normal">
              {statusCounts.total}
            </Badge>
          ) : null}
          {isLoading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={() => setShowGenerateDialog(true)}>
            {t('podcasts.generateBtn')}
          </Button>
          <Button size="sm" variant="outline" asChild>
            <Link href={podcastsHref}>{t('podcasts.viewAllEpisodes')}</Link>
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => { void refetch() }}
            disabled={isFetching}
            aria-label={t('common.refresh')}
          >
            {isFetching ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <RefreshCcw className="h-4 w-4" />
            )}
          </Button>
        </div>
      </div>

      {isError ? (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>{t('podcasts.loadErrorTitle')}</AlertTitle>
          <AlertDescription>{t('podcasts.loadErrorDesc')}</AlertDescription>
        </Alert>
      ) : null}

      {!isLoading && episodes.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {t('podcasts.noEpisodesForNotebook')}
        </p>
      ) : null}

      {!isLoading && episodes.length > 0 ? (
        <div className="space-y-3">
          {episodes.map((episode) => (
            <EpisodeCard
              key={episode.id}
              episode={episode}
              onDelete={handleDelete}
              deleting={deleteEpisode.isPending}
              onRetry={handleRetry}
              retrying={retryEpisode.isPending}
            />
          ))}
        </div>
      ) : null}

      <GeneratePodcastDialog
        open={showGenerateDialog}
        onOpenChange={setShowGenerateDialog}
        defaultNotebookId={notebookId}
      />
    </section>
  )
}
