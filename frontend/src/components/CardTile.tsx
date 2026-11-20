import type { CardSummary } from '../api/client'
import { cardNumber, formatEuro } from '../format'
import { useLang } from '../i18n'
import { paths } from '../router'

/** A card in a list: image, name, set and price, linking to its details. */
export function CardTile({ card }: { card: CardSummary }) {
  const { t } = useLang()
  return (
    <a className="tile" href={paths.card(card.card_id)}>
      <span className="tile-image">
        {card.image_url ? (
          <img src={card.image_url} alt="" loading="lazy" />
        ) : (
          <span className="no-image">{t.noImage}</span>
        )}
        <span className="tile-shine" aria-hidden="true" />
      </span>
      <span className="tile-name">{card.name}</span>
      <span className="tile-set">
        {card.set_name} · {cardNumber(card)}
      </span>
      <span className={`tile-price${card.price_eur == null ? ' none' : ''}`}>
        {card.price_eur != null ? formatEuro(t, card.price_eur) : t.noPrice}
      </span>
    </a>
  )
}
