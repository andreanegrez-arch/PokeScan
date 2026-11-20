import type { ReactNode } from 'react'
import {
  ApiError,
  getCard,
  type CardDetail,
  type CardPrices,
  type ConditionEstimate,
  type Variant,
  type VariantPrices,
} from '../api/client'
import { Ball } from '../components/Ball'
import { ConditionPanel } from '../components/ConditionPanel'
import { HoloCard } from '../components/HoloCard'
import { PriceChart } from '../components/PriceChart'
import { cardNumber, errorMessage, formatDay, formatEuro } from '../format'
import { useLang } from '../i18n'
import { paths, useTitle } from '../router'
import { useApi } from '../useApi'
import { useImageFallback } from '../useImageFallback'

const VARIANTS: Variant[] = ['normal', 'reverse']

/** One card: details, Cardmarket prices, price history and, after a scan, its condition. */
export function CardPage({
  cardId,
  condition,
}: {
  cardId: string
  condition: ConditionEstimate | null
}) {
  const { lang, t } = useLang()
  const { data, error, loading } = useApi(`${lang}|${cardId}`, (signal) =>
    getCard(cardId, lang, signal),
  )
  // Data of the card shown before is kept only while the same card reloads (language change).
  const card = data?.card_id === cardId ? data : undefined
  useTitle(card?.name)

  if (error) {
    const notFound = error instanceof ApiError && error.status === 404
    return (
      <section className="page narrow">
        <p className={notFound ? undefined : 'error'} role="alert">
          {notFound ? t.cardNotFound : errorMessage(t, error)}
        </p>
        <a href={paths.search()}>{t.navSearch}</a>
      </section>
    )
  }
  if (!card) {
    return (
      <section className="page">
        <p className="muted">{t.loading}</p>
      </section>
    )
  }

  const priced = VARIANTS.filter((variant) => card.prices?.[variant]?.trend != null)

  return (
    <article className={`page card-page${loading ? ' stale' : ''}`}>
      <div className="card-art">
        <CardArt key={card.card_id} card={card} />
      </div>

      <div className="card-info">
        <header>
          <h1>{card.name}</h1>
          <p className="lead">
            {card.set_name} · {t.number} {cardNumber(card)}
            {card.set_code && ` · ${card.set_code}`}
          </p>
        </header>
        <Facts card={card} />
        <Prices prices={card.prices} />
        {condition && <ConditionPanel condition={condition} />}
      </div>

      {priced.length > 0 && (
        <section className="panel chart-panel" aria-labelledby="history-title">
          <h2 id="history-title">{t.history}</h2>
          <PriceChart key={card.card_id} cardId={card.card_id} variants={priced} />
        </section>
      )}
    </article>
  )
}

/** The card's image or, when TCGdex has none (or lists one it doesn't have), a card-shaped placeholder. */
function CardArt({ card }: { card: CardDetail }) {
  const { t } = useLang()
  const image = useImageFallback(card.image_url, card.image_fallback_url)
  if (image.src) return <HoloCard src={image.src} alt={card.name} onError={image.onError} />
  return (
    <div className="card-placeholder" role="img" aria-label={t.noImage}>
      <Ball className="placeholder-ball" />
      <strong>{t.noImage}</strong>
      <span>{t.noImageNote}</span>
    </div>
  )
}

function Facts({ card }: { card: CardDetail }) {
  const { t } = useLang()
  const types =
    card.types.length > 0 ? (
      <span className="types">
        {card.types.map((type) => (
          <span key={type} className={`type-chip type-${type.toLowerCase()}`}>
            {t.typeNames[type] ?? type}
          </span>
        ))}
      </span>
    ) : null
  const facts: [string, ReactNode][] = [
    [t.category, card.category && (t.categories[card.category] ?? card.category)],
    [t.hp, card.hp != null ? String(card.hp) : null],
    [t.types, types],
    [t.rarity, card.rarity],
    [t.illustrator, card.illustrator],
    [t.released, card.release_date && formatDay(t, card.release_date)],
    [t.printedAs, card.variants.map((v) => t.variants[v] ?? v).join(', ') || null],
  ]
  return (
    <dl className="facts">
      {facts.map(
        ([label, value]) =>
          value && (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ),
      )}
    </dl>
  )
}

function Prices({ prices }: { prices: CardPrices | null }) {
  const { t } = useLang()
  return (
    <section className="panel" aria-labelledby="prices-title">
      <div className="panel-head">
        <h2 id="prices-title">{t.prices}</h2>
        {prices && <span className="muted small">{t.priceGuideOf(formatDay(t, prices.updated_at))}</span>}
      </div>
      {!prices && <p className="muted">{t.noPrices}</p>}
      {prices &&
        VARIANTS.map((variant) => {
          const values = prices[variant]
          return values && <VariantRow key={variant} name={t.variants[variant]} values={values} />
        })}
      {prices && prices.shared_with.length > 0 && (
        <div className="warning" role="note">
          <p>{t.sharedWith}</p>
          <ul>
            {prices.shared_with.map((id) => (
              <li key={id}>
                <a href={paths.card(id)}>{id}</a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

function VariantRow({ name, values }: { name: string; values: VariantPrices }) {
  const { t } = useLang()
  const price = (value: number | null) => (value != null ? formatEuro(t, value) : '—')
  return (
    <div className="variant">
      <h3>{name}</h3>
      <dl className="stats">
        <div className="stat main">
          <dt>{t.trend}</dt>
          <dd>{price(values.trend)}</dd>
        </div>
        <div className="stat">
          <dt>{t.low}</dt>
          <dd>{price(values.low)}</dd>
        </div>
        <div className="stat">
          <dt>{t.avg7}</dt>
          <dd>{price(values.avg7)}</dd>
        </div>
        <div className="stat">
          <dt>{t.avg30}</dt>
          <dd>{price(values.avg30)}</dd>
        </div>
      </dl>
    </div>
  )
}
