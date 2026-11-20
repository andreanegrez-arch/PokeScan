import { useState, type Dispatch, type FormEvent, type SetStateAction } from 'react'
import { scanCard, type ScanResult } from '../api/client'
import { Ball } from '../components/Ball'
import { CardTile } from '../components/CardTile'
import { ConditionPanel } from '../components/ConditionPanel'
import { PhotoPicker } from '../components/PhotoPicker'
import { ScanProgress } from '../components/ScanProgress'
import { errorMessage, formatCode } from '../format'
import { useLang } from '../i18n'
import { navigate, parseRoute, paths, useTitle } from '../router'

/** Kept by the app, so the photos and result are still there after visiting a card. */
export type ScanState = { front: File | null; back: File | null; result: ScanResult | null }

// How long the ball shows the card caught before opening it.
const CAUGHT_MS = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 1100

export function ScanPage({
  scan,
  setScan,
}: {
  scan: ScanState
  setScan: Dispatch<SetStateAction<ScanState>>
}) {
  const { lang, t } = useLang()
  const [phase, setPhase] = useState<'idle' | 'scanning' | 'caught'>('idle')
  const [error, setError] = useState<string | null>(null)
  const busy = phase !== 'idle'
  useTitle(t.scanTitle)

  async function handleScan(event: FormEvent) {
    event.preventDefault()
    if (!scan.front) return
    setPhase('scanning')
    setError(null)
    try {
      const result = await scanCard(scan.front, scan.back, lang)
      if (result.match) {
        setPhase('caught')
        await new Promise((done) => setTimeout(done, CAUGHT_MS))
      }
      setScan((current) => ({ ...current, result }))
      // Straight to the card, unless the user went somewhere else meanwhile.
      if (result.match && parseRoute(window.location.hash).page === 'scan') {
        navigate(paths.card(result.match.card_id))
      }
    } catch (e) {
      setError(errorMessage(t, e))
    } finally {
      setPhase('idle')
    }
  }

  return (
    <section className="page narrow">
      <header className="hero">
        <div className="hero-text">
          <h1>{t.scanTitle}</h1>
          <p>{t.scanIntro}</p>
        </div>
        <Ball className="hero-ball" />
      </header>

      {busy && scan.front ? (
        <ScanProgress photo={scan.front} caught={phase === 'caught'} />
      ) : (
        <form className="scan-form" onSubmit={handleScan}>
          <div className="pickers">
            <PhotoPicker
              side={t.front}
              hint={t.frontHint}
              kind="front"
              photo={scan.front}
              disabled={busy}
              onChange={(front) => setScan((current) => ({ ...current, front, result: null }))}
            />
            <PhotoPicker
              side={t.backSide}
              hint={t.backHint}
              kind="back"
              photo={scan.back}
              disabled={busy}
              onChange={(back) => setScan((current) => ({ ...current, back, result: null }))}
            />
          </div>
          <p className="fine-print">{t.photoTips}</p>
          <button type="submit" className="primary scan-button" disabled={!scan.front}>
            <Ball className="button-ball" />
            {t.scan}
          </button>
        </form>
      )}

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!busy && scan.result && <ScanOutcome result={scan.result} />}
      {!busy && scan.result?.condition && <ConditionPanel condition={scan.result.condition} />}
    </section>
  )
}

function ScanOutcome({ result }: { result: ScanResult }) {
  const { t } = useLang()
  const code = formatCode(result.code)
  switch (result.status) {
    case 'matched':
      return (
        result.match && (
          <section className="outcome" aria-live="polite">
            <p>{t.matched}</p>
            <div className="tiles single">
              <CardTile card={result.match} />
            </div>
          </section>
        )
      )
    case 'ambiguous':
      return (
        <section className="outcome" aria-live="polite">
          <p>{t.ambiguous(code)}</p>
          <div className="tiles">
            {result.candidates.map((card) => (
              <CardTile key={card.card_id} card={card} />
            ))}
          </div>
        </section>
      )
    case 'unreadable':
    case 'not_found':
      return (
        <section className="outcome missed" aria-live="polite">
          <p>{result.status === 'unreadable' ? t.unreadable : t.notFound(code)}</p>
          {result.status === 'not_found' && <p className="fine-print">{t.otherSets}</p>}
          <a className="button" href={paths.search(code)}>
            {t.searchByHand}
          </a>
        </section>
      )
  }
}
