import { useEffect, useState, type FormEvent } from 'react'
import { searchCards } from '../api/client'
import { CardTile } from '../components/CardTile'
import { errorMessage } from '../format'
import { useLang } from '../i18n'
import { navigate, paths, useTitle } from '../router'
import { useApi } from '../useApi'

const DEBOUNCE_MS = 300
const LIMIT = 60

/** Search by name or printed code. The query lives in the URL, so results can be shared. */
export function SearchPage({ query }: { query: string }) {
  const { lang, t } = useLang()
  const [text, setText] = useState(query)
  const [followed, setFollowed] = useState(query)
  useTitle(query.trim() || t.searchTitle)

  // The query changed from outside (back button, nav link): show it in the box.
  if (query !== followed) {
    setFollowed(query)
    setText(query)
  }

  // Typing updates the URL once the user pauses.
  useEffect(() => {
    if (text === query) return
    const timer = setTimeout(() => navigate(paths.search(text), { replace: true }), DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [text, query])

  const q = query.trim()
  const { data, error, loading } = useApi(q.length >= 2 ? `${lang}|${q}` : null, (signal) =>
    searchCards(q, lang, LIMIT, signal),
  )

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    navigate(paths.search(text), { replace: true })
  }

  return (
    <section className="page">
      <h1>{t.searchTitle}</h1>
      <form role="search" className="search-form" onSubmit={handleSubmit}>
        <label htmlFor="search-input">{t.searchLabel}</label>
        <input
          id="search-input"
          type="search"
          value={text}
          placeholder={t.searchPlaceholder}
          autoComplete="off"
          spellCheck={false}
          autoFocus
          onChange={(event) => setText(event.target.value)}
        />
        <p className="fine-print">{t.searchHint}</p>
      </form>

      {q.length >= 2 && (
        <div className={loading && data ? 'stale' : undefined} aria-busy={loading}>
          {error ? (
            <p className="error" role="alert">
              {errorMessage(t, error)}
            </p>
          ) : !data ? (
            <p className="muted">{t.loading}</p>
          ) : (
            <>
              <p className="muted" aria-live="polite">
                {data.results.length === 0 ? t.noResults : t.results(data.results.length)}
                {data.code && ` · ${t.searchedAsCode(data.code.raw)}`}
              </p>
              <div className="tiles">
                {data.results.map((card) => (
                  <CardTile key={card.card_id} card={card} />
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  )
}
