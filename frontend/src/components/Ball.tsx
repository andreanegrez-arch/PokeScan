/**
 * PokeScan's emblem: a capture ball whose button is a scanner's lens.
 * Drawn here, no official artwork. Colors come from the theme (--ball-*).
 */
export function Ball({ className }: { className?: string }) {
  return (
    <svg className={`ball${className ? ` ${className}` : ''}`} viewBox="0 0 64 64" aria-hidden="true">
      <path className="ball-top" d="M4 32a28 28 0 0 1 56 0z" />
      <path className="ball-bottom" d="M4 32h56a28 28 0 0 1-56 0z" />
      <circle className="ball-shine" cx="21" cy="17" r="5" />
      <path className="ball-band" d="M4 32h56" />
      <circle className="ball-outline" cx="32" cy="32" r="28" />
      <circle className="ball-button" cx="32" cy="32" r="9" />
      <circle className="ball-lens" cx="32" cy="32" r="4.5" />
    </svg>
  )
}
