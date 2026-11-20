import { useEffect, useState } from 'react'
import type { ConditionEstimate, Lang } from './api/client'
import { Ball } from './components/Ball'
import { Segmented } from './components/Segmented'
import { LangContext, STRINGS, useLang } from './i18n'
import { CardPage } from './pages/CardPage'
import { ScanPage, type ScanState } from './pages/ScanPage'
import { SearchPage } from './pages/SearchPage'
import { paths, useRoute } from './router'

const LANG_KEY = 'pokescan.lang'

function savedLang(): Lang {
  try {
    const saved = localStorage.getItem(LANG_KEY)
    if (saved === 'it' || saved === 'en') return saved
  } catch {
    // Storage blocked: fall back to the browser's language.
  }
  return navigator.language.startsWith('it') ? 'it' : 'en'
}

export default function App() {
  const route = useRoute()
  const [lang, setLang] = useState<Lang>(savedLang)
  const [scan, setScan] = useState<ScanState>({ front: null, back: null, result: null })
  // The nav link brings back the last search.
  const [lastQuery, setLastQuery] = useState('')
  const t = STRINGS[lang]

  useEffect(() => {
    document.documentElement.lang = lang
    try {
      localStorage.setItem(LANG_KEY, lang)
    } catch {
      // Not remembered: no harm.
    }
  }, [lang])

  const pageKey = route.page === 'card' ? `card:${route.id}` : route.page
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [pageKey])

  if (route.page === 'search' && route.q !== lastQuery) setLastQuery(route.q)

  let page
  switch (route.page) {
    case 'scan':
      page = <ScanPage scan={scan} setScan={setScan} />
      break
    case 'search':
      page = <SearchPage query={route.q} />
      break
    case 'card':
      page = <CardPage cardId={route.id} condition={scannedCondition(scan, route.id)} />
      break
    case 'missing':
      page = <Missing />
  }

  return (
    <LangContext value={{ lang, t }}>
      <header className="topbar">
        <div className="topbar-inner">
          <a className="brand" href={paths.scan}>
            <Ball className="logo" />
            PokeScan
          </a>
          <nav aria-label="PokeScan">
            <a href={paths.scan} aria-current={route.page === 'scan' ? 'page' : undefined}>
              {t.navScan}
            </a>
            <a
              href={paths.search(lastQuery)}
              aria-current={route.page === 'search' ? 'page' : undefined}
            >
              {t.navSearch}
            </a>
          </nav>
          <Segmented
            label={t.language}
            options={[
              { value: 'it', label: 'IT', title: 'Italiano' },
              { value: 'en', label: 'EN', title: 'English' },
            ]}
            value={lang}
            onChange={setLang}
          />
        </div>
      </header>
      <main>{page}</main>
      <footer className="footer">
        <p>{t.credits}</p>
        <p>{t.disclaimer}</p>
      </footer>
    </LangContext>
  )
}

/** The condition from the last scan, if it was of this card (or one of its candidates). */
function scannedCondition({ result }: ScanState, cardId: string): ConditionEstimate | null {
  if (!result?.condition) return null
  const ids = [result.match?.card_id, ...result.candidates.map((card) => card.card_id)]
  return ids.includes(cardId) ? result.condition : null
}

function Missing() {
  const { t } = useLang()
  return (
    <section className="page narrow">
      <p>{t.pageNotFound}</p>
      <a href={paths.scan}>{t.goHome}</a>
    </section>
  )
}
