import { useEffect, useRef, useState } from 'react'
import { useLang } from '../i18n'
import { Ball } from './Ball'

const STEP_MS = 1100

/**
 * While the photo is scanned: the photo under a sweeping scan line, and a
 * ball wobbling as if something were being caught. `caught`: the card was
 * recognized, the ball clicks shut.
 *
 * The steps shown are what the scan does, in its order, one after the other
 * on a timer: the server answers only once, at the end.
 */
export function ScanProgress({ photo, caught }: { photo: File; caught: boolean }) {
  const { t } = useLang()
  const [step, setStep] = useState(0)
  const image = useRef<HTMLImageElement>(null)

  useEffect(() => {
    const url = URL.createObjectURL(photo)
    if (image.current) image.current.src = url
    return () => URL.revokeObjectURL(url)
  }, [photo])

  useEffect(() => {
    if (caught) return
    const timer = setInterval(
      () => setStep((current) => Math.min(current + 1, t.scanSteps.length - 1)),
      STEP_MS,
    )
    return () => clearInterval(timer)
  }, [caught, t.scanSteps.length])

  return (
    <section className={`scanning${caught ? ' caught' : ''}`} aria-live="polite" aria-busy={!caught}>
      <div className="scan-photo">
        <img ref={image} alt={t.photoOf(t.front)} />
        <span className="scan-line" aria-hidden="true" />
      </div>
      <div className="scan-status">
        <span className="ball-stage" aria-hidden="true">
          <Ball className="wobble" />
          <span className="spark s1" />
          <span className="spark s2" />
          <span className="spark s3" />
        </span>
        <p className="scan-step">{caught ? t.caught : t.scanSteps[step]}</p>
      </div>
    </section>
  )
}
