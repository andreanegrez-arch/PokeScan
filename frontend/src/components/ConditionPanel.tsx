import type { Centering, Condition, ConditionEstimate } from '../api/client'
import { formatPercent } from '../format'
import { CONDITION_NAMES, STRINGS, useLang } from '../i18n'

// The API's notes start with the centering, which is shown above them already.
const CENTERING_LABELS = Object.values(STRINGS).flatMap((s) => [s.frontCentering, s.backCentering])

const SCALE: [Condition, string][] = [
  ['mint', 'MT'],
  ['near_mint', 'NM'],
  ['excellent', 'EX'],
  ['good', 'GD'],
  ['light_played', 'LP'],
  ['played', 'PL'],
  ['poor', 'PO'],
]

/** The condition estimated from the scan photos, on Cardmarket's scale. */
export function ConditionPanel({ condition }: { condition: ConditionEstimate }) {
  const { t } = useLang()
  const { estimate, front_centering, back_centering, edge_whitening_pct } = condition
  const notes = condition.notes.filter(
    (note) => !CENTERING_LABELS.some((label) => note.startsWith(label)),
  )
  const centering = ({ left_pct, top_pct }: Centering) => {
    const left = Math.round(left_pct)
    const top = Math.round(top_pct)
    return (
      <>
        <span>
          {left}/{100 - left} {t.leftRight}
        </span>
        <span>
          {top}/{100 - top} {t.topBottom}
        </span>
      </>
    )
  }

  return (
    <section className="panel" aria-labelledby="condition-title">
      <div className="panel-head">
        <h2 id="condition-title">{t.conditionTitle}</h2>
        <span className="badge">{t.indicative}</span>
      </div>

      {estimate ? (
        <p className="grade">{CONDITION_NAMES[estimate]}</p>
      ) : (
        <p className="muted">{t.noEstimate}</p>
      )}
      {estimate && (
        <ol className="grade-scale" aria-hidden="true">
          {SCALE.map(([name, short]) => (
            <li key={name} className={name === estimate ? 'current' : undefined}>
              {short}
            </li>
          ))}
        </ol>
      )}

      <dl className="facts condition-facts">
        {front_centering && (
          <div>
            <dt>{t.frontCentering}</dt>
            <dd>{centering(front_centering)}</dd>
          </div>
        )}
        {back_centering && (
          <div>
            <dt>{t.backCentering}</dt>
            <dd>{centering(back_centering)}</dd>
          </div>
        )}
        {edge_whitening_pct != null && (
          <div>
            <dt>{t.whitening}</dt>
            <dd>{formatPercent(t, edge_whitening_pct)}</dd>
          </div>
        )}
      </dl>

      {notes.length > 0 && (
        <ul className="notes">
          {notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </section>
  )
}
