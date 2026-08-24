'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { AlertCircle, Loader2, RefreshCcw } from 'lucide-react'

import { useDeletePodcastEpisode, usePodcastEpisodes, useRetryPodcastEpisode } from '@/lib/hooks/use-podcasts'
import { useNotebooks } from '@/lib/hooks/use-notebooks'
import { useCapabilities } from '@/lib/hooks/use-capabilities'
import { EpisodeCard } from '@/components/podcasts/EpisodeCard'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { GeneratePodcastDialog } from '@/components/podcasts/GeneratePodcastDialog'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { TFunction } from 'i18next'

const ALL_NOTEBOOKS = 'all'

const getSTATUS_ORDER = (t: TFunction): Array<{
  key: 'running' | 'completed' | 'failed' | 'pending'
  title: string
  description?: string
}> => [
  {
    key: 'running',
    title: t('podcasts.statusRunningTitle'),
    description: t('podcasts.statusRunningDesc'),
  },
  {
    key: 'pending',
    title: t('podcasts.statusPendingTitle'),
    description: t('podcasts.statusPendingDesc'),
  },
  {
    key: 'completed',
    title: t('podcasts.statusCompletedTitle'),
    description: t('podcasts.statusCompletedDesc'),
  },
  {
    key: 'failed',
    title: t('podcasts.statusFailedTitle'),
    description: t('podcasts.statusFailedDesc'),
  },
]

function SummaryBadge({ label, value }: { label: string; value: number }) {
  return (
    <Badge variant="outline" className="font-medium">
      <span className="text-muted-foreground mr-1.5">{label}</span>
      <span className="font-mono text-foreground">{value}</span>
    </Badge>
  )
}

export interface EpisodesTabProps {
  /**
   * When set, only episodes for this notebook are shown and the notebook
   * filter control is hidden (scope is fixed by the parent).
   */
  notebookId?: string
}

export function EpisodesTab({ notebookId: scopedNotebookId }: EpisodesTabProps = {}) {
  const { t } = useTranslation()
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const [showGenerateDialog, setShowGenerateDialog] = useState(false)

  // URL/filter-driven scope when the parent does not pin a notebook.
  const urlNotebookId = searchParams.get('notebook_id') || undefined
  const [filterNotebookId, setFilterNotebookId] = useState<string | undefined>(
    () => scopedNotebookId ?? urlNotebookId
  )

  useEffect(() => {
    if (scopedNotebookId) {
      setFilterNotebookId(scopedNotebookId)
      return
    }
    setFilterNotebookId(urlNotebookId)
  }, [scopedNotebookId, urlNotebookId])

  const notebookId = scopedNotebookId ?? filterNotebookId
  const showNotebookFilter = !scopedNotebookId

  const {
    episodes,
    statusGroups,
    statusCounts,
    isLoading,
    isError,
    refetch,
    isFetching,
  } = usePodcastEpisodes({ notebookId })

  const notebooksQuery = useNotebooks(false)
  const notebooks = useMemo(
    () => notebooksQuery.data ?? [],
    [notebooksQuery.data]
  )

  const { data: capabilities } = useCapabilities()
  const workerLikelyReady = capabilities?.worker_likely_ready !== false
  const deleteEpisode = useDeletePodcastEpisode()
  const retryEpisode = useRetryPodcastEpisode()

  const handleRefresh = useCallback(() => {
    void refetch()
  }, [refetch])

  const handleDelete = useCallback(
    (episodeId: string) => deleteEpisode.mutateAsync(episodeId),
    [deleteEpisode]
  )

  const handleRetry = useCallback(
    async (episodeId: string) => { await retryEpisode.mutateAsync(episodeId) },
    [retryEpisode]
  )

  const handleNotebookFilterChange = useCallback(
    (value: string) => {
      const next = value === ALL_NOTEBOOKS ? undefined : value
      setFilterNotebookId(next)

      const params = new URLSearchParams(searchParams.toString())
      if (next) {
        params.set('notebook_id', next)
      } else {
        params.delete('notebook_id')
      }
      const query = params.toString()
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false })
    },
    [pathname, router, searchParams]
  )

  const emptyState = !isLoading && episodes.length === 0
  const emptyMessage = notebookId
    ? t('podcasts.noEpisodesForNotebook')
    : t('podcasts.noEpisodesYet')

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="space-y-1">
          <h2 className="font-display text-xl font-semibold tracking-tight">
            {notebookId
              ? t('podcasts.notebookEpisodesTitle')
              : t('podcasts.overviewTitle')}
          </h2>
          <p className="text-sm text-muted-foreground">
            {notebookId
              ? t('podcasts.notebookEpisodesDesc')
              : t('podcasts.overviewDesc')}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button onClick={() => setShowGenerateDialog(true)}>
            {t('podcasts.generateBtn')}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleRefresh}
            disabled={isFetching}
          >
            {isFetching ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <RefreshCcw className="mr-2 h-4 w-4" />
            )}
            {t('common.refresh')}
          </Button>
        </div>
      </div>

      {showNotebookFilter ? (
        <div className="flex flex-wrap items-center gap-3">
          <label
            htmlFor="podcast-notebook-filter"
            className="text-sm font-medium text-muted-foreground"
          >
            {t('podcasts.filterByNotebook')}
          </label>
          <Select
            value={filterNotebookId ?? ALL_NOTEBOOKS}
            onValueChange={handleNotebookFilterChange}
          >
            <SelectTrigger id="podcast-notebook-filter" className="w-[min(100%,20rem)]">
              <SelectValue placeholder={t('podcasts.allNotebooks')} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_NOTEBOOKS}>
                {t('podcasts.allNotebooks')}
              </SelectItem>
              {notebooks.map((notebook) => (
                <SelectItem key={notebook.id} value={notebook.id}>
                  {notebook.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : null}

      <div className="flex flex-wrap gap-2">
        <SummaryBadge label={t('podcasts.total')} value={statusCounts.total} />
        <SummaryBadge label={t('podcasts.processingLabel')} value={statusCounts.running} />
        <SummaryBadge label={t('podcasts.completedLabel')} value={statusCounts.completed} />
        <SummaryBadge label={t('podcasts.failedLabel')} value={statusCounts.failed} />
        <SummaryBadge label={t('podcasts.pendingLabel')} value={statusCounts.pending} />
      </div>

      {!workerLikelyReady ? (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>{t('podcasts.workerOfflineBannerTitle')}</AlertTitle>
          <AlertDescription>
            {t('podcasts.workerOfflineBannerDesc')}
          </AlertDescription>
        </Alert>
      ) : null}

      {isError ? (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>{t('podcasts.loadErrorTitle')}</AlertTitle>
          <AlertDescription>
            {t('podcasts.loadErrorDesc')}
          </AlertDescription>
        </Alert>
      ) : null}

      {isLoading ? (
        <div className="flex items-center gap-3 rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          {t('podcasts.loadingEpisodes')}
        </div>
      ) : null}

      {emptyState ? (
        <div className="rounded-md border border-dashed p-10 text-center">
          <p className="text-sm text-muted-foreground">
            {emptyMessage}
          </p>
        </div>
      ) : null}

      {getSTATUS_ORDER(t).map(({ key, title, description }) => {
        const data = statusGroups[key]
        if (!data || data.length === 0) {
          return null
        }

        return (
          <section key={key} className="space-y-4">
            <div>
              <h3 className="text-lg font-semibold leading-tight">{title}</h3>
              {description ? (
                <p className="text-sm text-muted-foreground">{description}</p>
              ) : null}
            </div>
            <Separator />
            <div className="space-y-4">
              {data.map((episode) => (
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
          </section>
        )
      })}

      <GeneratePodcastDialog
        open={showGenerateDialog}
        onOpenChange={setShowGenerateDialog}
        defaultNotebookId={notebookId}
      />
    </div>
  )
}
