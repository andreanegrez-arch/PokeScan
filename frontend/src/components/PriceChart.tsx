// The trend price history of one card: a line over time, hand-drawn in SVG.
//
// One series, so no legend: the section title names it. The y axis starts at
// zero (the area under the line would lie otherwise), the last price is
// labelled at the end of the line, and every value is also in the table.
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type RefObject } from 'react'
import { getPriceHistory, type PricePoint, type Variant } from '../api/client'
import { errorMessage, formatDay, formatEuro, parseDay } from '../format'
import { useLang, type Strings } from '../i18n'
import { useApi } from '../useApi'
import { Segmented } from './Segmented'

const RANGES = [30, 90, 365]
const HEIGHT = 240
const MARGIN = { top: 24, right: 16, bottom: 28 }
const DAY = 86_400_000
const TICK_SPACING = 96 // px between date labels, at least
const END_LABEL_WIDTH = 64 // px, about "1.234,56 €"

export function PriceChart({ cardId, variants }: { cardId: string; variants: Variant[] }) {
  const { t } = useLang()
  const [variant, setVariant] = useState<Variant>(variants[0])
  const [days, setDays] = useState(90)
  const { data, error, loading } = useApi(`${cardId}|${variant}|${days}`, (signal) =>
    getPriceHistory(cardId, variant, days, signal),
  )

  let body
  if (error) body = <p className="error">{errorMessage(t, error)}</p>
  else if (!data) body = <p className="muted">{t.loading}</p>
  else if (data.points.length === 0) body = <p className="muted">{t.noHistory}</p>
  else if (data.points.length === 1) {
    const [{ date, price }] = data.points
    body = <p className="muted">{t.onePoint(formatEuro(t, price), formatDay(t, date))}</p>
  } else body = <LineChart points={data.points} days={days} />

  return (
    <>
      <div className="filters">
        {variants.length > 1 && (
          <Segmented
            label={t.variant}
            options={variants.map((value) => ({ value, label: t.variants[value] }))}
            value={variant}
            onChange={setVariant}
          />
        )}
        <Segmented
          label={t.period}
          options={RANGES.map((value) => ({ value, label: t.ranges[value] }))}
          value={days}
          onChange={setDays}
        />
      </div>
      <div className={`chart-body${loading && data ? ' stale' : ''}`} aria-busy={loading}>
        {body}
      </div>
    </>
  )
}

function LineChart({ points, days }: { points: PricePoint[]; days: number }) {
  const { t } = useLang()
  const box = useRef<HTMLDivElement>(null)
  const width = useWidth(box)
  const [active, setActive] = useState<number | null>(null)

  const series = useMemo(
    () => points.map((point) => ({ ...point, time: parseDay(point.date) })),
    [points],
  )
  const last = series.length - 1
  const yTicks = niceTicks(Math.max(...series.map((point) => point.price)))
  const step = yTicks[1]
  const decimals = Number.isInteger(step) ? 0 : Math.max(2, Math.ceil(-Math.log10(step) - 1e-9))
  const yLabels = yTicks.map((tick) => formatEuro(t, tick, decimals))
  // Room for the longest price label, about 7 px a character at 12 px.
  const left = 12 + 7 * Math.max(...yLabels.map((label) => label.length))

  const plotWidth = Math.max(0, width - left - MARGIN.right)
  const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom
  const start = series[0].time
  const span = series[last].time - start || 1
  const x = (time: number) => left + ((time - start) / span) * plotWidth
  const y = (price: number) => MARGIN.top + plotHeight * (1 - price / yTicks[yTicks.length - 1])

  const line = series.map((p, i) => `${i ? 'L' : 'M'}${x(p.time).toFixed(1)},${y(p.price).toFixed(1)}`)
  const baseline = y(0)
  const area = `${line.join('')}L${x(series[last].time)},${baseline}L${left},${baseline}Z`
  const dateTicks = dayTicks(start, series[last].time, Math.floor(plotWidth / TICK_SPACING) + 1)

  function pointAt(clientX: number) {
    const bounds = box.current?.getBoundingClientRect()
    if (!bounds) return null
    const time = start + ((clientX - bounds.left - left) / plotWidth) * span
    let nearest = 0
    for (let i = 1; i <= last; i++) {
      if (Math.abs(series[i].time - time) < Math.abs(series[nearest].time - time)) nearest = i
    }
    return nearest
  }

  function handleKey(event: KeyboardEvent) {
    const moves: Record<string, number> = {
      ArrowLeft: (active ?? last) - 1,
      ArrowRight: (active ?? last) + 1,
      Home: 0,
      End: last,
    }
    if (!(event.key in moves)) return
    event.preventDefault()
    setActive(Math.min(last, Math.max(0, moves[event.key])))
  }

  const end = series[last]
  // The last price's label sits left of the dot, above or below it: on the side
  // the line under the label mostly isn't.
  const underLabel = series.filter((p) => x(end.time) - x(p.time) < END_LABEL_WIDTH)
  const higher = underLabel.filter((p) => p.price > end.price).length
  const endLabelBelow = higher > underLabel.length - 1 - higher
  const shown = active === null ? null : series[active]
  // The tooltip carries the value then, and the crosshair would cross the label.
  const hideEndLabel = shown !== null && x(end.time) - x(shown.time) < END_LABEL_WIDTH

  return (
    <>
      <div
        ref={box}
        className="chart"
        tabIndex={0}
        role="group"
        aria-label={t.chartLabel(
          days,
          formatDay(t, series[0].date),
          formatDay(t, end.date),
          formatEuro(t, end.price),
        )}
        onPointerMove={(event) => setActive(pointAt(event.clientX))}
        onPointerLeave={() => setActive(null)}
        onFocus={() => setActive((current) => current ?? last)}
        onBlur={() => setActive(null)}
        onKeyDown={handleKey}
      >
        {width > 0 && (
          <svg width={width} height={HEIGHT} aria-hidden="true">
            {yTicks.map((tick, i) => (
              <g key={tick}>
                <line
                  className={tick === 0 ? 'axis' : 'grid'}
                  x1={left}
                  x2={left + plotWidth}
                  y1={y(tick)}
                  y2={y(tick)}
                />
                <text className="tick" x={left - 8} y={y(tick)} textAnchor="end" dy="0.35em">
                  {yLabels[i]}
                </text>
              </g>
            ))}
            {dateTicks.map((time, i) => (
              <text
                key={time}
                className="tick"
                x={x(time)}
                y={HEIGHT - 8}
                textAnchor={i === 0 ? 'start' : i === dateTicks.length - 1 ? 'end' : 'middle'}
              >
                {formatDay(t, time, 'short')}
              </text>
            ))}

            <path className="series-area" d={area} />
            <path className="series-line" d={line.join('')} />

            {shown && (
              <line
                className="crosshair"
                x1={x(shown.time)}
                x2={x(shown.time)}
                y1={MARGIN.top}
                y2={baseline}
              />
            )}
            <circle className="series-dot" cx={x(end.time)} cy={y(end.price)} r={4} />
            {shown && shown !== end && (
              <circle className="series-dot" cx={x(shown.time)} cy={y(shown.price)} r={4} />
            )}
            {!hideEndLabel && (
              <text
                className="end-label"
                x={x(end.time)}
                y={y(end.price) + (endLabelBelow ? 20 : -12)}
                textAnchor="end"
              >
                {formatEuro(t, end.price)}
              </text>
            )}
          </svg>
        )}
        {shown && (
          <Tooltip t={t} point={shown} x={x(shown.time)} width={width} />
        )}
      </div>

      <details className="table-view">
        <summary>{t.showTable}</summary>
        <table>
          <thead>
            <tr>
              <th scope="col">{t.date}</th>
              <th scope="col">{t.price}</th>
            </tr>
          </thead>
          <tbody>
            {[...series].reverse().map((point) => (
              <tr key={point.date}>
                <td>{formatDay(t, point.date)}</td>
                <td>{formatEuro(t, point.price)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </>
  )
}

function Tooltip({
  t,
  point,
  x,
  width,
}: {
  t: Strings
  point: PricePoint
  x: number
  width: number
}) {
  // Beside the crosshair, on the side with more room, so it never hides the point.
  const side = x > width / 2 ? { right: width - x + 10 } : { left: x + 10 }
  return (
    <div className="tooltip" style={{ ...side, top: MARGIN.top }} aria-live="polite">
      <strong>{formatEuro(t, point.price)}</strong>
      <span>{formatDay(t, point.date)}</span>
    </div>
  )
}

/** Round steps from zero to above `max`: 0, 0.5, 1, 1.5 or 0, 20, 40, 60. */
function niceTicks(max: number, count = 4) {
  const rough = (max > 0 ? max : 1) / count
  const magnitude = 10 ** Math.floor(Math.log10(rough))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= rough)!
  const steps = Math.max(1, Math.ceil(max / step - 1e-9))
  return Array.from({ length: steps + 1 }, (_, i) => Number((i * step).toPrecision(12)))
}

/** Up to `count` evenly spread days from `start` to `end`, both included. */
function dayTicks(start: number, end: number, count: number) {
  const span = Math.round((end - start) / DAY)
  const ticks = Math.min(Math.max(count, 2), span + 1)
  if (ticks < 2) return [start]
  return Array.from({ length: ticks }, (_, i) => start + Math.round((span * i) / (ticks - 1)) * DAY)
}

function useWidth(ref: RefObject<HTMLElement | null>) {
  const [width, setWidth] = useState(0)
  useEffect(() => {
    if (!ref.current) return
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [ref])
  return width
}
