import { useEffect, useRef, useState } from 'react'

type Loaded<T> = { key: string; data?: T; error?: unknown }

/**
 * Loads `load()` whenever `key` changes (null: load nothing).
 *
 * While a new key loads, the last data is kept, so a page can show it dimmed
 * instead of jumping to a spinner. A request overtaken by a newer key is aborted.
 */
export function useApi<T>(key: string | null, load: (signal: AbortSignal) => Promise<T>) {
  const [loaded, setLoaded] = useState<Loaded<T> | null>(null)
  const latestLoad = useRef(load)

  useEffect(() => {
    latestLoad.current = load
  })

  useEffect(() => {
    if (key === null) return
    const controller = new AbortController()
    latestLoad.current(controller.signal).then(
      (data) => setLoaded({ key, data }),
      (error) => {
        if (!controller.signal.aborted) setLoaded((last) => ({ key, data: last?.data, error }))
      },
    )
    return () => controller.abort()
  }, [key])

  const current = loaded?.key === key
  return {
    data: key === null ? undefined : loaded?.data,
    error: current ? loaded?.error : undefined,
    loading: key !== null && !current,
  }
}
