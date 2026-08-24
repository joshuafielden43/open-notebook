import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import {
  importLocale,
  isLanguageCode,
  resources,
  type LanguageCode,
} from './locales'

const loadedLocales = new Set<string>(['en-US'])

/** Ensure a locale's strings are registered before changeLanguage. */
export async function ensureLocaleLoaded(code: string): Promise<void> {
  if (!isLanguageCode(code) || loadedLocales.has(code)) {
    return
  }
  const translation = await importLocale(code as LanguageCode)
  i18n.addResourceBundle(code, 'translation', translation, true, true)
  loadedLocales.add(code)
}

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    // Non-English packs are added via ensureLocaleLoaded; allow partial boot.
    partialBundledLanguages: true,
    fallbackLng: 'en-US',
    interpolation: {
      escapeValue: false, // react already safes from xss
    },
    react: {
      useSuspense: false,
    },
    detection: {
      order: ['localStorage', 'navigator'],
      caches: ['localStorage'],
    },
  })
  .then(async () => {
    const detected = i18n.language
    if (detected && detected !== 'en-US' && isLanguageCode(detected)) {
      await ensureLocaleLoaded(detected)
      // Re-apply so components pick up the newly loaded bundle.
      await i18n.changeLanguage(detected)
    }
  })

export default i18n
