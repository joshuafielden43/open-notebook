import { enUS } from './en-US'
import type { TranslationShape } from './en-US'

export type TranslationKeys = TranslationShape

export type LanguageCode =
  | 'en-US'
  | 'tr-TR'
  | 'ca-ES'
  | 'zh-CN'
  | 'zh-TW'
  | 'pt-BR'
  | 'ja-JP'
  | 'it-IT'
  | 'fr-FR'
  | 'ru-RU'
  | 'bn-IN'
  | 'es-ES'
  | 'de-DE'
  | 'pl-PL'

export type Language = {
  code: LanguageCode
  label: string
}

export const languages: Language[] = [
  { code: 'en-US', label: 'English' },
  { code: 'tr-TR', label: 'Türkçe' },
  { code: 'ca-ES', label: 'Català' },
  { code: 'zh-CN', label: '简体中文' },
  { code: 'zh-TW', label: '繁體中文' },
  { code: 'pt-BR', label: 'Português' },
  { code: 'ja-JP', label: '日本語' },
  { code: 'it-IT', label: 'Italiano' },
  { code: 'fr-FR', label: 'Français' },
  { code: 'ru-RU', label: 'Русский' },
  { code: 'bn-IN', label: 'বাংলা' },
  { code: 'es-ES', label: 'Español' },
  { code: 'de-DE', label: 'Deutsch' },
  { code: 'pl-PL', label: 'Polski' },
]

/** Only en-US is bundled in the main chunk; other locales load on demand. */
export const resources = {
  'en-US': { translation: enUS },
} as const

const localeLoaders: Record<
  Exclude<LanguageCode, 'en-US'>,
  () => Promise<TranslationKeys>
> = {
  'tr-TR': () => import('./tr-TR').then((m) => m.trTR),
  'ca-ES': () => import('./ca-ES').then((m) => m.caES),
  'zh-CN': () => import('./zh-CN').then((m) => m.zhCN),
  'zh-TW': () => import('./zh-TW').then((m) => m.zhTW),
  'pt-BR': () => import('./pt-BR').then((m) => m.ptBR),
  'ja-JP': () => import('./ja-JP').then((m) => m.jaJP),
  'it-IT': () => import('./it-IT').then((m) => m.itIT),
  'fr-FR': () => import('./fr-FR').then((m) => m.frFR),
  'ru-RU': () => import('./ru-RU').then((m) => m.ruRU),
  'bn-IN': () => import('./bn-IN').then((m) => m.bnIN),
  'es-ES': () => import('./es-ES').then((m) => m.esES),
  'de-DE': () => import('./de-DE').then((m) => m.deDE),
  'pl-PL': () => import('./pl-PL').then((m) => m.plPL),
}

export function isLanguageCode(code: string): code is LanguageCode {
  return code === 'en-US' || code in localeLoaders
}

export async function importLocale(code: LanguageCode): Promise<TranslationKeys> {
  if (code === 'en-US') {
    return enUS
  }
  return localeLoaders[code]()
}

/** Load every supported locale (parity tests and offline tooling). */
export async function importAllLocales(): Promise<
  Record<LanguageCode, TranslationKeys>
> {
  const entries = await Promise.all(
    languages.map(async ({ code }) => [code, await importLocale(code)] as const),
  )
  return Object.fromEntries(entries) as Record<LanguageCode, TranslationKeys>
}

export { enUS }
