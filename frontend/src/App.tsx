import { useState } from 'react'
import {
  scanCard,
  type CardSummary,
  type Centering,
  type ConditionEstimate,
  type ScanResult,
} from './api/client'

const euro = new Intl.NumberFormat('it-IT', { style: 'currency', currency: 'EUR' })

const CONDITION_LABELS: Record<NonNullable<ConditionEstimate['estimate']>, string> = {
  mint: 'Mint',
  near_mint: 'Near Mint',
  excellent: 'Excellent',
  good: 'Good',
  light_played: 'Light Played',
  played: 'Played',
  poor: 'Poor',
}

export default function App() {
  const [front, setFront] = useState<File | null>(null)
  const [back, setBack] = useState<File | null>(null)
  const [result, setResult] = useState<ScanResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleScan() {
    if (!front) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      setResult(await scanCard(front, back))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <main>
      <h1>PokeScan</h1>
      <p>Fotografa una carta per scoprire il suo valore su Cardmarket.</p>

      <PhotoPicker label="Fronte (obbligatorio)" photo={front} onChange={setFront} />
      <PhotoPicker label="Retro (facoltativo, per le condizioni)" photo={back} onChange={setBack} />

      <button type="button" onClick={handleScan} disabled={!front || loading}>
        {loading ? 'Scansione in corso…' : 'Scansiona'}
      </button>

      {error && (
        <p className="error" role="alert">
          Errore: {error}
        </p>
      )}

      {result && <ScanOutcome result={result} />}
      {result?.condition && <ConditionPanel condition={result.condition} />}
    </main>
  )
}

function PhotoPicker({
  label,
  photo,
  onChange,
}: {
  label: string
  photo: File | null
  onChange: (photo: File | null) => void
}) {
  return (
    <label className="picker">
      {photo ? photo.name : label}
      <input
        type="file"
        accept="image/*"
        capture="environment"
        onChange={(e) => onChange(e.target.files?.[0] ?? null)}
      />
    </label>
  )
}

function ScanOutcome({ result }: { result: ScanResult }) {
  switch (result.status) {
    case 'matched':
      return result.match ? <CardMatch card={result.match} /> : null
    case 'ambiguous':
      return (
        <section className="candidates">
          <p>Il codice {result.code?.raw} corrisponde a più espansioni. Qual è la tua?</p>
          {result.candidates.map((card) => (
            <CardMatch key={card.card_id} card={card} />
          ))}
        </section>
      )
    case 'unreadable':
      return (
        <p>
          Non riesco a leggere il codice in basso sulla carta. Riprova con una foto più nitida e
          ben illuminata.
        </p>
      )
    case 'not_found':
      return <p>Il codice {result.code?.raw} non corrisponde a nessuna carta conosciuta.</p>
  }
}

function CardMatch({ card }: { card: CardSummary }) {
  return (
    <article className="card">
      {card.image_url && <img src={card.image_url} alt={card.name} />}
      <h2>{card.name}</h2>
      <p>
        {card.set_name} · n. {card.local_number}
      </p>
      <p className="price">{card.price_eur != null ? euro.format(card.price_eur) : 'Prezzo n.d.'}</p>
    </article>
  )
}

function ConditionPanel({ condition }: { condition: ConditionEstimate }) {
  return (
    <section className="card">
      <h2>Condizioni (stima indicativa)</h2>
      {condition.estimate && <p className="price">{CONDITION_LABELS[condition.estimate]}</p>}
      {condition.front_centering && (
        <p>Centratura fronte: {formatCentering(condition.front_centering)}</p>
      )}
      {condition.back_centering && (
        <p>Centratura retro: {formatCentering(condition.back_centering)}</p>
      )}
      {condition.notes.length > 0 && (
        <ul>
          {condition.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </section>
  )
}

function formatCentering({ left_pct, top_pct }: Centering) {
  const left = Math.round(left_pct)
  const top = Math.round(top_pct)
  return `${left}/${100 - left} orizzontale, ${top}/${100 - top} verticale`
}
