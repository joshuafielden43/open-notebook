import { SourceListResponse } from '@/lib/types/api'

export type SourceMode = 'off' | 'insights' | 'full'

export interface NotebookSelection {
  sources: Record<string, SourceMode>
  notes: Record<string, SourceMode>
}

export interface NotebookSummary {
  notebookId: string
  sources: number
  notes: number
}

export interface NotebookContextConfig {
  notebookId: string
  contextConfig: {
    sources: Record<string, string>
    notes: Record<string, string>
  }
}

// Helper function to format large numbers with K/M suffixes
export function formatNumber(num: number): string {
  if (num >= 1000000) {
    return `${(num / 1000000).toFixed(1)}M`
  }
  if (num >= 1000) {
    return `${(num / 1000).toFixed(1)}K`
  }
  return num.toString()
}

export function hasSelections(selection?: NotebookSelection): boolean {
  if (!selection) {
    return false
  }
  return (
    Object.values(selection.sources).some((mode) => mode !== 'off') ||
    Object.values(selection.notes).some((mode) => mode !== 'off')
  )
}

export function getSourceDefaultMode(source: SourceListResponse): SourceMode {
  return source.insights_count && source.insights_count > 0 ? 'insights' : 'full'
}

/**
 * True when every known source/note for the notebook is included (not off).
 * In that case generate can pass notebook_id alone and let the server assemble
 * context — avoiding a client-side mega JSON.stringify that pegs the CPU.
 */
export function isFullNotebookSelection(
  selection: NotebookSelection | undefined,
  sources: { id: string }[],
  notes: { id: string }[],
): boolean {
  if (!selection) {
    return false
  }
  if (sources.length === 0 && notes.length === 0) {
    return false
  }
  const sourcesOk =
    sources.length === 0 ||
    sources.every((s) => selection.sources[s.id] && selection.sources[s.id] !== 'off')
  const notesOk =
    notes.length === 0 ||
    notes.every((n) => selection.notes[n.id] && selection.notes[n.id] !== 'off')
  return sourcesOk && notesOk
}

/**
 * Convert the per-notebook selection state into build-context configs,
 * skipping notebooks with no active selections.
 */
export function selectionsToContextConfigs(
  selections: Record<string, NotebookSelection>
): NotebookContextConfig[] {
  const configs: NotebookContextConfig[] = []

  Object.entries(selections).forEach(([notebookId, selection]) => {
    const sourcesConfig = Object.entries(selection.sources)
      .filter(([, mode]) => mode !== 'off')
      .reduce<Record<string, string>>((acc, [sourceId, mode]) => {
        const normalizedId = sourceId.replace(/^source:/, '')
        acc[normalizedId] = mode === 'insights' ? 'insights' : 'full content'
        return acc
      }, {})

    const notesConfig = Object.entries(selection.notes)
      .filter(([, mode]) => mode !== 'off')
      .reduce<Record<string, string>>((acc, [noteId]) => {
        const normalizedId = noteId.replace(/^note:/, '')
        acc[normalizedId] = 'full content'
        return acc
      }, {})

    if (Object.keys(sourcesConfig).length === 0 && Object.keys(notesConfig).length === 0) {
      return
    }

    configs.push({
      notebookId,
      contextConfig: {
        sources: sourcesConfig,
        notes: notesConfig,
      },
    })
  })

  return configs
}
