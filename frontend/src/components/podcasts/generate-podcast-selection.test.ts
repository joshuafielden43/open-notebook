import { describe, expect, it } from 'vitest'

import {
  isFullNotebookSelection,
  type NotebookSelection,
} from './generate-podcast-selection'

describe('isFullNotebookSelection', () => {
  const sources = [{ id: 'source:a' }, { id: 'source:b' }]
  const notes = [{ id: 'note:1' }]

  it('is true when every source and note is included', () => {
    const selection: NotebookSelection = {
      sources: { 'source:a': 'full', 'source:b': 'insights' },
      notes: { 'note:1': 'full' },
    }
    expect(isFullNotebookSelection(selection, sources, notes)).toBe(true)
  })

  it('is false when any source is off', () => {
    const selection: NotebookSelection = {
      sources: { 'source:a': 'full', 'source:b': 'off' },
      notes: { 'note:1': 'full' },
    }
    expect(isFullNotebookSelection(selection, sources, notes)).toBe(false)
  })

  it('is false when selection is missing', () => {
    expect(isFullNotebookSelection(undefined, sources, notes)).toBe(false)
  })

  it('is false when inventory is empty', () => {
    const selection: NotebookSelection = { sources: {}, notes: {} }
    expect(isFullNotebookSelection(selection, [], [])).toBe(false)
  })
})
