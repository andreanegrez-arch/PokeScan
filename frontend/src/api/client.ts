// Typed calls to the backend API.
// The types come from schema.d.ts, generated from the backend with `npm run gen:api`.
import type { components } from './schema'

type Schemas = components['schemas']
export type ScanResult = Schemas['ScanResult']
export type CardSummary = Schemas['CardSummary']
export type CardDetail = Schemas['CardDetail']
export type CardPrices = Schemas['CardPrices']
export type VariantPrices = Schemas['VariantPrices']
export type ConditionEstimate = Schemas['ConditionEstimate']
export type Condition = NonNullable<ConditionEstimate['estimate']>
export type Centering = Schemas['Centering']
export type SearchResult = Schemas['SearchResult']
export type PriceHistory = Schemas['PriceHistory']
export type PricePoint = Schemas['PricePoint']
export type Variant = PriceHistory['variant']
export type Lang = 'it' | 'en'

const API_URL: string = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function toApiError(response: Response): Promise<ApiError> {
  // FastAPI errors look like {"detail": "..."}; validation errors carry a list instead.
  const body = await response.json().catch(() => null)
  const detail = typeof body?.detail === 'string' ? body.detail : response.statusText
  return new ApiError(response.status, detail)
}

async function get<T>(path: string, params: Record<string, string | number>, signal?: AbortSignal) {
  const query = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]))
  const response = await fetch(`${API_URL}${path}?${query}`, { signal })
  if (!response.ok) throw await toApiError(response)
  return (await response.json()) as T
}

/** `lang`: language of the card names and of the condition notes. */
export async function scanCard(
  front: File,
  back: File | null,
  lang: Lang = 'it',
  signal?: AbortSignal,
): Promise<ScanResult> {
  const form = new FormData()
  form.append('front', front)
  if (back) form.append('back', back)

  const response = await fetch(`${API_URL}/scan?lang=${lang}`, {
    method: 'POST',
    body: form,
    signal,
  })
  if (!response.ok) throw await toApiError(response)
  return response.json()
}

/** Cards by name, or by the code printed on them ("SVI 025/198", "25/102"). */
export function searchCards(q: string, lang: Lang, limit = 30, signal?: AbortSignal) {
  return get<SearchResult>('/cards/search', { q, lang, limit }, signal)
}

export function getCard(cardId: string, lang: Lang, signal?: AbortSignal) {
  return get<CardDetail>(`/cards/${encodeURIComponent(cardId)}`, { lang }, signal)
}

/** Daily Cardmarket trend prices of the last `days` days, oldest first. */
export function getPriceHistory(cardId: string, variant: Variant, days: number, signal?: AbortSignal) {
  return get<PriceHistory>(
    `/cards/${encodeURIComponent(cardId)}/prices`,
    { variant, days },
    signal,
  )
}
