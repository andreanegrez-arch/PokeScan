import { useRef, type PointerEvent } from 'react'

const STILL = window.matchMedia('(prefers-reduced-motion: reduce)')

/** The card's image, tilting toward the pointer with a holo glare on it. */
export function HoloCard({ src, alt }: { src: string; alt: string }) {
  const card = useRef<HTMLDivElement>(null)

  function tilt(event: PointerEvent) {
    const element = card.current
    if (!element || STILL.matches) return
    const box = element.getBoundingClientRect()
    const x = (event.clientX - box.left) / box.width
    const y = (event.clientY - box.top) / box.height
    element.style.setProperty('--rx', `${((0.5 - y) * 16).toFixed(2)}deg`)
    element.style.setProperty('--ry', `${((x - 0.5) * 16).toFixed(2)}deg`)
    element.style.setProperty('--gx', `${(x * 100).toFixed(1)}%`)
    element.style.setProperty('--gy', `${(y * 100).toFixed(1)}%`)
    element.classList.add('active')
  }

  function reset() {
    const element = card.current
    if (!element) return
    for (const name of ['--rx', '--ry', '--gx', '--gy']) element.style.removeProperty(name)
    element.classList.remove('active')
  }

  return (
    <div className="holo-card" ref={card} onPointerMove={tilt} onPointerLeave={reset}>
      <img src={src} alt={alt} />
      <span className="holo-glare" aria-hidden="true" />
    </div>
  )
}
