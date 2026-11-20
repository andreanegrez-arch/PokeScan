import { useState } from 'react'

/**
 * The first of the image URLs that loads: TCGdex lists a few images it
 * doesn't have, so on an error the next one is tried. `src` is null once
 * none is left (show a placeholder). Give the component a `key` per card, so
 * that another card starts again from its first URL.
 */
export function useImageFallback(...urls: (string | null | undefined)[]) {
  const sources = urls.filter((url): url is string => Boolean(url))
  const [failed, setFailed] = useState(0)
  return {
    src: sources[failed] ?? null,
    onError: () => setFailed((count) => count + 1),
  }
}
