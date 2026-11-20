// Numbers and dates in the reader's language.
import type { CardSummary, CollectorCode } from './api/client'
import type { Strings } from './i18n'

/** The code that was looked up ('SVI 025/198'), not the raw OCR text around it. */
export function formatCode(code: CollectorCode | null) {
  if (!code) return ''
  const number = code.total ? `${code.number}/${code.total}` : code.number
  return code.set_code ? `${code.set_code} ${number}` : number
}

/** "25/102" as printed on the card; promos have no total. */
export function cardNumber(card: Pick<CardSummary, 'local_number' | 'set_total'>) {
  return card.set_total ? `${card.local_number}/${card.set_total}` : card.local_number
}

export function formatEuro(t: Strings, value: number, decimals?: number) {
  return new Intl.NumberFormat(t.locale, {
    style: 'currency',
    currency: 'EUR',
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value)
}

/** Millimeters to a tenth: what a photo can tell. */
export function formatMm(t: Strings, value: number) {
  return new Intl.NumberFormat(t.locale, {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(value)
}

export function formatPercent(t: Strings, value: number) {
  return new Intl.NumberFormat(t.locale, { maximumFractionDigits: 1 }).format(value) + '%'
}

/** API dates are days ("2026-11-19"): read and shown in UTC, so no time zone shifts them. */
export function parseDay(day: string) {
  return Date.parse(`${day}T00:00:00Z`)
}

export function formatDay(t: Strings, day: string | number, style: 'long' | 'short' = 'long') {
  const time = typeof day === 'number' ? day : parseDay(day)
  return new Intl.DateTimeFormat(t.locale, {
    day: 'numeric',
    month: 'short',
    year: style === 'long' ? 'numeric' : undefined,
    timeZone: 'UTC',
  }).format(time)
}

/** Fetch failures (backend not running) get a clearer message than "Failed to fetch". */
export function errorMessage(t: Strings, error: unknown) {
  if (error instanceof TypeError) return t.serverDown
  return t.error(error instanceof Error ? error.message : String(error))
}
