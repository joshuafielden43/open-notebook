'use client'

import { useState } from 'react'
import Link from 'next/link'
import { Activity, Loader2 } from 'lucide-react'

import { useBackgroundJobs } from '@/lib/hooks/use-background-jobs'
import { useTranslation } from '@/lib/hooks/use-translation'
import { Button } from '@/components/ui/button'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

function friendlyJobName(
  name: string,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  const key = (name || '').toLowerCase()
  if (key.includes('podcast') || key.includes('generate_podcast')) {
    return t('backgroundJobs.jobPodcast')
  }
  if (key.includes('source') || key.includes('process_source')) {
    return t('backgroundJobs.jobSource')
  }
  if (key.includes('embed') || key.includes('rebuild')) {
    return t('backgroundJobs.jobEmbed')
  }
  return name || t('backgroundJobs.jobGeneric')
}

function jobHref(name: string): string {
  const key = (name || '').toLowerCase()
  if (key.includes('podcast') || key.includes('generate_podcast')) return '/podcasts'
  if (key.includes('source') || key.includes('process_source')) return '/sources'
  if (key.includes('embed') || key.includes('rebuild')) return '/search'
  return '/'
}

export function BackgroundJobsIndicator() {
  const { t } = useTranslation()
  const { jobs, activeCount, isLoading, isFetching } = useBackgroundJobs()
  const [open, setOpen] = useState(false)

  if (activeCount === 0 && !isLoading) {
    return null
  }

  return (
    <div className="flex shrink-0 items-center justify-end border-b border-border/40 px-4 py-1.5">
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className={cn(
            'gap-2 border-dashed',
            activeCount > 0 && 'border-amber-300 text-amber-900 dark:text-amber-100'
          )}
          aria-label={t('backgroundJobs.chipAria', { count: activeCount })}
        >
          {isFetching || activeCount > 0 ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Activity className="h-3.5 w-3.5" />
          )}
          <span className="text-xs font-medium">
            {t('backgroundJobs.chipLabel', { count: activeCount })}
          </span>
          {activeCount > 0 ? (
            <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
              {activeCount}
            </Badge>
          ) : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <div className="border-b px-3 py-2">
          <p className="text-sm font-medium">{t('backgroundJobs.panelTitle')}</p>
          <p className="text-xs text-muted-foreground">
            {t('backgroundJobs.panelDesc')}
          </p>
        </div>
        <ul className="max-h-72 overflow-y-auto py-1">
          {jobs.length === 0 ? (
            <li className="px-3 py-4 text-center text-xs text-muted-foreground">
              {t('backgroundJobs.empty')}
            </li>
          ) : (
            jobs.map((job) => (
              <li
                key={job.job_id}
                className="border-b border-border/50 px-3 py-2 last:border-0"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 space-y-0.5">
                    <Link
                      href={jobHref(job.name)}
                      onClick={() => setOpen(false)}
                      className="block truncate text-sm font-medium hover:underline"
                    >
                      {friendlyJobName(job.name, t)}
                    </Link>
                    <p className="truncate font-mono text-[10px] text-muted-foreground">
                      {job.job_id}
                    </p>
                  </div>
                  <Badge variant="outline" className="shrink-0 text-[10px] uppercase">
                    {job.status}
                  </Badge>
                </div>
                {job.error_message ? (
                  <p className="mt-1 line-clamp-2 text-xs text-destructive">
                    {job.error_message}
                  </p>
                ) : null}
              </li>
            ))
          )}
        </ul>
      </PopoverContent>
    </Popover>
    </div>
  )
}
