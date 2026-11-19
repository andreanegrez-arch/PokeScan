// Typed calls to the backend API.
// The types come from schema.d.ts, generated from the backend with `npm run gen:api`.
import type { components } from './schema'

type Schemas = components['schemas']
export type ScanResult = Schemas['ScanResult']
export type CardSummary = Schemas['CardSummary']
export type ConditionEstimate = Schemas['ConditionEstimate']
export type Centering = Schemas['Centering']
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

export async function scanCard(
  front: File,
  back: File | null,
  lang: Lang = 'it',
): Promise<ScanResult> {
  const form = new FormData()
  form.append('front', front)
  if (back) form.append('back', back)

  const response = await fetch(`${API_URL}/scan?lang=${lang}`, {
    method: 'POST',
    body: form,
  })
  if (!response.ok) throw await toApiError(response)
  return response.json()
}
