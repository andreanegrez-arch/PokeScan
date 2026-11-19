// A small hash router: the app is static files, so routes live after the #.
//   #/                 scan
//   #/search?q=pika    search
//   #/card/sv01-025    card details
import { useEffect, useMemo, useSyncExternalStore } from 'react'

export type Route =
  | { page: 'scan' }
  | { page: 'search'; q: string }
  | { page: 'card'; id: string }
  | { page: 'missing' }

export const paths = {
  scan: '#/',
  search: (q = '') => (q ? `#/search?${new URLSearchParams({ q })}` : '#/search'),
  card: (id: string) => `#/card/${encodeURIComponent(id)}`,
}

export function parseRoute(hash: string): Route {
  const [path, query = ''] = hash.replace(/^#/, '').split('?', 2)
  const parts = path.split('/').filter(Boolean)
  if (parts.length === 0) return { page: 'scan' }
  if (parts[0] === 'search' && parts.length === 1) {
    return { page: 'search', q: new URLSearchParams(query).get('q') ?? '' }
  }
  if (parts[0] === 'card' && parts.length === 2) {
    return { page: 'card', id: decodeURIComponent(parts[1]) }
  }
  return { page: 'missing' }
}

function subscribe(onChange: () => void) {
  window.addEventListener('hashchange', onChange)
  return () => window.removeEventListener('hashchange', onChange)
}

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash)
  return useMemo(() => parseRoute(hash), [hash])
}

/** The browser tab's title for the page shown. */
export function useTitle(title: string | undefined) {
  useEffect(() => {
    document.title = title ? `${title} · PokeScan` : 'PokeScan'
  }, [title])
}

/** `replace`: don't add a history entry (e.g. while typing a search). */
export function navigate(path: string, { replace = false } = {}) {
  if (replace) {
    history.replaceState(null, '', path)
    // replaceState doesn't fire hashchange: tell the listeners ourselves.
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  } else {
    window.location.hash = path
  }
}
