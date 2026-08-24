'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { formatDistanceToNow } from 'date-fns'
import { getDateLocale } from '@/lib/utils/date-locale'
import { Download, InfoIcon, Play, RefreshCcw, Trash2 } from 'lucide-react'

import apiClient from '@/lib/api/client'
import { resolvePodcastAssetUrl } from '@/lib/api/podcasts'
import { EpisodeStatus, FAILED_EPISODE_STATUSES, PodcastEpisode } from '@/lib/types/podcasts'
import { cn } from '@/lib/utils'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useTranslation } from '@/lib/hooks/use-translation'
import { anonymousApiClient } from '@/lib/api/client'
import type { TFunction } from 'i18next'

interface EpisodeCardProps {
  episode: PodcastEpisode
  onDelete: (episodeId: string) => Promise<void> | void
  deleting?: boolean
  onRetry?: (episodeId: string) => Promise<void> | void
  retrying?: boolean
}

const getSTATUS_META = (t: TFunction): Record<
  EpisodeStatus | 'unknown',
  { label: string; className: string }
> => ({
  running: {
    label: t('podcasts.processingLabel'),
    className: 'bg-warn-tint text-warn border-warn/30',
  },
  processing: {
    label: t('podcasts.processingLabel'),
    className: 'bg-warn-tint text-warn border-warn/30',
  },
  completed: {
    label: t('podcasts.completedLabel'),
    className: 'bg-fern-tint text-fern border-fern/30',
  },
  failed: {
    label: t('podcasts.failedLabel'),
    className: 'bg-destructive-tint text-destructive border-destructive/30',
  },
  error: {
    label: t('podcasts.failedLabel'),
    className: 'bg-destructive-tint text-destructive border-destructive/30',
  },
  pending: {
    label: t('podcasts.pendingLabel'),
    className: 'bg-teal-tint text-teal border-teal/30',
  },
  submitted: {
    label: t('podcasts.pendingLabel'),
    className: 'bg-teal-tint text-teal border-teal/30',
  },
  unknown: {
    label: t('common.unknown'),
    className: 'bg-muted text-muted-foreground border-transparent',
  },
})

function stageLabel(t: TFunction, stage?: string | null): string | null {
  if (!stage) return null
  const map: Record<string, string> = {
    queued: t('podcasts.stageQueued'),
    outline: t('podcasts.stageOutline'),
    transcript: t('podcasts.stageTranscript'),
    tts: t('podcasts.stageTts'),
    combine: t('podcasts.stageCombine'),
    completed: t('podcasts.stageCompleted'),
    failed: t('podcasts.stageFailed'),
  }
  return map[stage] ?? stage
}

function StatusBadge({
  status,
  stage,
}: {
  status?: EpisodeStatus | null
  stage?: string | null
}) {
  const { t } = useTranslation()
  // Don't show badge for completed episodes
  if (status === 'completed') {
    return null
  }

  const meta = getSTATUS_META(t)[status ?? 'unknown']
  const stageText = stageLabel(t, stage)
  return (
    <Badge
      variant="outline"
      className={cn('uppercase tracking-wide text-xs', meta.className)}
    >
      {stageText && status !== 'failed' && status !== 'error'
        ? stageText
        : meta.label}
    </Badge>
  )
}

type OutlineSegment = {
  name?: string
  description?: string
  size?: string
}

type OutlineData = {
  segments?: OutlineSegment[]
}

type TranscriptEntry = {
  speaker?: string
  dialogue?: string
}

type TranscriptData = {
  transcript?: TranscriptEntry[]
}

function extractOutlineSegments(outline: unknown): OutlineSegment[] {
  if (outline && typeof outline === 'object' && 'segments' in outline) {
    const data = outline as OutlineData
    if (Array.isArray(data.segments)) {
      return data.segments
    }
  }
  return []
}

/**
 * "provider / name" label for a snapshot model row. Prefers the display
 * fields the API resolves from the snapshot's model references, falls back
 * to the legacy snapshot strings (pre-#1107 episodes), then to a dash.
 */
function formatModelLabel(
  provider?: string | null,
  name?: string | null,
  legacyProvider?: string | null,
  legacyName?: string | null
): string {
  return `${provider || legacyProvider || '—'} / ${name || legacyName || '—'}`
}

function extractTranscriptEntries(transcript: unknown): TranscriptEntry[] {
  if (transcript && typeof transcript === 'object' && 'transcript' in transcript) {
    const data = transcript as TranscriptData
    if (Array.isArray(data.transcript)) {
      return data.transcript
    }
  }
  return []
}

export function EpisodeCard({ episode, onDelete, deleting, onRetry, retrying }: EpisodeCardProps) {
  const { t, language } = useTranslation()
  const [audioSrc, setAudioSrc] = useState<string | undefined>()
  const [audioError, setAudioError] = useState<string | null>(null)
  const [audioLoading, setAudioLoading] = useState(false)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const revokeUrlRef = useRef<string | undefined>(undefined)

  const outlineSegments = useMemo(() => extractOutlineSegments(episode.outline), [episode.outline])
  const transcriptEntries = useMemo(() => extractTranscriptEntries(episode.transcript), [episode.transcript])

  const hasAudio = Boolean(episode.audio_url ?? episode.audio_file)

  useEffect(() => {
    return () => {
      if (revokeUrlRef.current) {
        URL.revokeObjectURL(revokeUrlRef.current)
      }
    }
  }, [])

  // With OPEN_NOTEBOOK_PUBLIC_AUDIO the audio route answers without auth and
  // the media element streams natively (Range seeking, buffer-ahead). A HEAD
  // probe detects that; otherwise fall back to the authed blob fetch.
  const resolveAudioAccess = useCallback(async (): Promise<
    { url: string; publicRoute: boolean } | null
  > => {
    const directAudioUrl = await resolvePodcastAssetUrl(episode.audio_url ?? episode.audio_file)
    if (!directAudioUrl) {
      return null
    }
    if (!episode.audio_url) {
      return { url: directAudioUrl, publicRoute: true }
    }
    try {
      const probe = await anonymousApiClient.head(directAudioUrl)
      if (probe.status >= 200 && probe.status < 300) {
        return { url: directAudioUrl, publicRoute: true }
      }
    } catch {
      // Probe failure means the route needs auth; use the blob path.
    }
    return { url: directAudioUrl, publicRoute: false }
  }, [episode.audio_url, episode.audio_file])

  // Episodes can be hundreds of MB; nothing downloads on mount — only when
  // the listener asks for it.
  const loadAudio = useCallback(async () => {
    setAudioError(null)
    setAudioLoading(true)
    try {
      const access = await resolveAudioAccess()
      if (!access) {
        setAudioSrc(undefined)
        return
      }

      if (access.publicRoute) {
        setAudioSrc(access.url)
        return
      }

      // apiClient attaches the auth header; the URL is absolute so the
      // dynamic baseURL is ignored.
      const response = await apiClient.get<Blob>(access.url, {
        responseType: 'blob',
      })

      revokeUrlRef.current = URL.createObjectURL(response.data)
      setAudioSrc(revokeUrlRef.current)
    } catch (error) {
      console.error('Unable to load podcast audio', error)
      setAudioError(t('podcasts.audioUnavailable'))
      setAudioSrc(undefined)
    } finally {
      setAudioLoading(false)
    }
  }, [resolveAudioAccess, t])

  const [downloading, setDownloading] = useState(false)

  const handleDownload = useCallback(async () => {
    setDownloading(true)
    try {
      const access = await resolveAudioAccess()
      if (!access) {
        return
      }
      const sep = access.url.includes('?') ? '&' : '?'
      if (access.publicRoute) {
        // Server sets Content-Disposition: attachment with a human filename.
        const anchor = document.createElement('a')
        anchor.href = `${access.url}${sep}download=1`
        anchor.download = ''
        document.body.appendChild(anchor)
        anchor.click()
        anchor.remove()
        return
      }
      const response = await apiClient.get<Blob>(`${access.url}${sep}download=1`, {
        responseType: 'blob',
      })
      const objectUrl = URL.createObjectURL(response.data)
      const anchor = document.createElement('a')
      anchor.href = objectUrl
      anchor.download = `${(episode.name || 'episode').replace(/[^\w\s.-]/g, '').trim() || 'episode'}.mp3`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(objectUrl)
    } catch (error) {
      console.error('Unable to download podcast audio', error)
      setAudioError(t('podcasts.audioUnavailable'))
    } finally {
      setDownloading(false)
    }
  }, [resolveAudioAccess, episode.name, t])

  const distance = episode.created
    ? formatDistanceToNow(new Date(episode.created), {
        addSuffix: true,
        locale: getDateLocale(language),
      })
    : null

  const createdLabel = distance
    ? t('podcasts.created', { time: distance })
    : null

  const handleDelete = () => {
    void onDelete(episode.id)
  }

  const handleRetry = () => {
    if (onRetry) {
      void onRetry(episode.id)
    }
  }

  const isFailed = FAILED_EPISODE_STATUSES.includes(episode.job_status as EpisodeStatus)

  return (
    <Card>
      <CardContent className="space-y-4 p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-base font-semibold text-foreground">
                {episode.name}
              </h3>
              <StatusBadge
                status={episode.job_status}
                stage={episode.generation_stage}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              {t('podcasts.profile')}: {episode.episode_profile?.name || t('common.unknown')}
              {createdLabel ? ` • ${createdLabel}` : ''}
              {episode.generation_stage &&
              episode.job_status !== 'completed' &&
              episode.job_status !== 'failed' &&
              episode.job_status !== 'error'
                ? ` • ${stageLabel(t, episode.generation_stage)}`
                : ''}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {hasAudio && !isFailed ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => void handleDownload()}
                disabled={downloading}
                aria-label={t('common.download')}
                title={t('common.download')}
              >
                <Download className={cn('h-4 w-4', downloading && 'animate-pulse')} />
              </Button>
            ) : null}
            <Dialog open={detailsOpen} onOpenChange={setDetailsOpen}>
              <DialogTrigger asChild>
                <Button variant="outline" size="sm">
                  <InfoIcon className="mr-2 h-4 w-4" /> {t('podcasts.details')}
                </Button>
              </DialogTrigger>
              <DialogContent className="w-[min(90vw,720px)] max-h-[85vh] overflow-hidden">
                <DialogHeader>
                  <DialogTitle>{episode.name}</DialogTitle>
                  <DialogDescription>
                    {episode.episode_profile?.name || t('common.unknown')}
                    {createdLabel ? ` • ${createdLabel}` : ''}
                  </DialogDescription>
                </DialogHeader>
                <div className="space-y-4 overflow-hidden">
                  {/* Playback lives only on the card — see ADR-012. */}
                  <Tabs defaultValue="summary" className="h-[60vh] flex flex-col">
                    <TabsList className="grid w-full grid-cols-3">
                      <TabsTrigger value="summary">{t('podcasts.summaryTab')}</TabsTrigger>
                      <TabsTrigger value="outline">{t('podcasts.outlineTab')}</TabsTrigger>
                      <TabsTrigger value="transcript">{t('podcasts.transcriptTab')}</TabsTrigger>
                    </TabsList>

                    <TabsContent value="summary" className="flex-1 overflow-hidden">
                      <ScrollArea className="h-full pr-4">
                        <div className="space-y-6">
                          <section className="space-y-2">
                            <h4 className="text-sm font-semibold text-foreground">{t('podcasts.episodeProfile')}</h4>
                            <div className="grid gap-2 text-sm md:grid-cols-2">
                              <div>
                                <p className="text-muted-foreground">{t('podcasts.outlineModel')}</p>
                                <p>
                                  {formatModelLabel(
                                    episode.episode_profile?.outline_model_provider,
                                    episode.episode_profile?.outline_model_name,
                                    episode.episode_profile?.outline_provider,
                                    episode.episode_profile?.outline_model
                                  )}
                                </p>
                              </div>
                              <div>
                                <p className="text-muted-foreground">{t('podcasts.transcriptModel')}</p>
                                <p>
                                  {formatModelLabel(
                                    episode.episode_profile?.transcript_model_provider,
                                    episode.episode_profile?.transcript_model_name,
                                    episode.episode_profile?.transcript_provider,
                                    episode.episode_profile?.transcript_model
                                  )}
                                </p>
                              </div>
                              <div>
                                <p className="text-muted-foreground">{t('podcasts.segments')}</p>
                                <p>{episode.episode_profile?.num_segments ?? '—'}</p>
                              </div>
                              <div>
                                <p className="text-muted-foreground">{t('podcasts.maxTokens')}</p>
                                <p>{episode.episode_profile?.max_tokens ?? '—'}</p>
                              </div>
                            </div>
                            {episode.episode_profile?.default_briefing ? (
                              <div className="rounded border bg-muted/30 p-3 text-xs whitespace-pre-wrap">
                                {episode.episode_profile.default_briefing}
                              </div>
                            ) : null}
                          </section>

                          <section className="space-y-2">
                            <h4 className="text-sm font-semibold text-foreground">{t('podcasts.speakerProfile')}</h4>
                            <p className="text-xs text-muted-foreground">
                              {formatModelLabel(
                                episode.speaker_profile?.voice_model_provider,
                                episode.speaker_profile?.voice_model_name,
                                episode.speaker_profile?.tts_provider,
                                episode.speaker_profile?.tts_model
                              )}
                            </p>
                            {episode.speaker_profile?.speakers?.map((speaker, index) => (
                              <div
                                key={`${speaker.name}-${index}`}
                                className="rounded-md border bg-muted/20 p-3 text-xs"
                              >
                                <p className="font-semibold text-foreground">{speaker.name}</p>
                                <p className="text-muted-foreground">{t('podcasts.voiceId')}: {speaker.voice_id}</p>
                                <p className="mt-2 whitespace-pre-wrap text-muted-foreground">
                                  <span className="font-semibold">{t('podcasts.backstory')}:</span> {speaker.backstory}
                                </p>
                                <p className="mt-2 whitespace-pre-wrap text-muted-foreground">
                                  <span className="font-semibold">{t('podcasts.personality')}:</span> {speaker.personality}
                                </p>
                              </div>
                            ))}
                          </section>

                          {episode.briefing ? (
                            <section className="space-y-2">
                              <h4 className="text-sm font-semibold text-foreground">{t('podcasts.briefing')}</h4>
                              <div className="rounded border bg-muted/30 p-3 text-xs whitespace-pre-wrap">
                                {episode.briefing}
                              </div>
                            </section>
                          ) : null}
                        </div>
                      </ScrollArea>
                    </TabsContent>

                    <TabsContent value="outline" className="flex-1 overflow-hidden">
                      <ScrollArea className="h-full pr-4">
                        {outlineSegments.length > 0 ? (
                          <div className="space-y-3">
                            {outlineSegments.map((segment, index) => (
                              <div key={index} className="rounded border bg-muted/20 p-3 text-xs space-y-1">
                                <div className="flex items-center justify-between gap-2">
                                  <p className="font-semibold text-foreground">{segment.name ?? `${t('podcasts.segment')} ${index + 1}`}</p>
                                  {segment.size ? (
                                    <Badge variant="outline" className="text-[10px] uppercase tracking-wide">{segment.size}</Badge>
                                  ) : null}
                                </div>
                                <p className="text-muted-foreground whitespace-pre-wrap">{segment.description ?? t('podcasts.noDescription')}</p>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <p className="text-xs text-muted-foreground">{t('podcasts.noOutline')}</p>
                        )}
                      </ScrollArea>
                    </TabsContent>

                    <TabsContent value="transcript" className="flex-1 overflow-hidden">
                      <ScrollArea className="h-full pr-4 space-y-3">
                        {transcriptEntries.length > 0 ? (
                          transcriptEntries.map((entry, index) => (
                            <div key={index} className="rounded border bg-muted/20 p-3 text-xs space-y-1">
                              <p className="font-semibold text-foreground">{entry.speaker ?? t('podcasts.speaker')}</p>
                              <p className="text-muted-foreground whitespace-pre-wrap">{entry.dialogue ?? ''}</p>
                            </div>
                          ))
                        ) : (
                          <p className="text-xs text-muted-foreground">{t('podcasts.noTranscript')}</p>
                        )}
                      </ScrollArea>
                    </TabsContent>
                  </Tabs>
                </div>
              </DialogContent>
            </Dialog>
            {isFailed && onRetry ? (
              <Button
                variant="outline"
                size="sm"
                onClick={handleRetry}
                disabled={retrying}
              >
                <RefreshCcw className={cn('mr-2 h-4 w-4', retrying && 'animate-spin')} />
                {retrying ? t('podcasts.retrying') : t('podcasts.retry')}
              </Button>
            ) : null}
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="ghost" size="sm" className="text-destructive">
                  <Trash2 className="mr-2 h-4 w-4" />
                  {t('podcasts.delete')}
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>{t('podcasts.deleteEpisodeTitle')}</AlertDialogTitle>
                  <AlertDialogDescription>
                    {t('podcasts.deleteEpisodeDesc', { name: episode.name })}
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
                  <AlertDialogAction onClick={handleDelete} disabled={deleting}>
                    {deleting ? t('podcasts.deleting') : t('podcasts.delete')}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </div>

        {audioSrc ? (
          /* Inline card player on md+; on phone widths it docks to the bottom
             edge as a Spotify-style mini-player (overlay, so it owns a shadow). */
          <div className="rounded-md border bg-card p-2 max-md:fixed max-md:inset-x-2 max-md:bottom-2 max-md:z-50 max-md:rounded-lg max-md:bg-card/95 max-md:p-3 max-md:pb-[calc(0.75rem+env(safe-area-inset-bottom))] max-md:shadow-lg max-md:backdrop-blur">
            <p className="mb-1.5 truncate text-xs font-medium md:hidden">{episode.name}</p>
            <audio controls autoPlay preload="none" src={audioSrc} className="w-full" />
          </div>
        ) : audioError ? (
          <p className="text-sm text-destructive">{audioError}</p>
        ) : hasAudio && !isFailed ? (
          <Button
            variant="outline"
            size="sm"
            onClick={() => void loadAudio()}
            disabled={audioLoading}
            className="w-fit"
          >
            <Play className="mr-1.5 h-3.5 w-3.5" />
            {audioLoading ? t('podcasts.loadingAudio') : t('podcasts.loadAudio')}
          </Button>
        ) : null}

        {isFailed && episode.error_message ? (
          <div className="rounded-md border border-destructive/30 bg-destructive-tint p-3">
            <p className="text-xs font-medium text-destructive">{t('podcasts.errorDetails')}</p>
            <p className="mt-1 text-xs whitespace-pre-wrap text-destructive">{episode.error_message}</p>
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}
