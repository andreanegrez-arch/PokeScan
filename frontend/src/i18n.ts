// The app's text in Italian and English. The language also picks the card
// names and the condition notes the API sends back.
import { createContext, useContext } from 'react'
import type { Condition, Lang } from './api/client'

const it = {
  locale: 'it-IT',
  tagline: 'Riconosce le carte Pokémon dal codice stampato e mostra il prezzo su Cardmarket.',
  navScan: 'Scansiona',
  navSearch: 'Cerca',
  language: 'Lingua',
  credits: 'Dati delle carte: TCGdex · Prezzi: listino Cardmarket, aggiornato ogni giorno',
  loading: 'Caricamento…',
  error: (message: string) => `Errore: ${message}`,
  serverDown: 'Il server non risponde. Il backend è avviato?',
  pageNotFound: 'Questa pagina non esiste.',
  goHome: 'Torna alla scansione',

  scanTitle: 'Scansiona una carta',
  scanIntro:
    'Fotografa il fronte: la carta viene riconosciuta dal codice stampato in basso. ' +
    'Aggiungi il retro per una stima delle condizioni.',
  front: 'Fronte',
  frontHint: 'Obbligatorio',
  backSide: 'Retro',
  backHint: 'Facoltativo, per le condizioni',
  choosePhoto: 'Scegli o scatta una foto',
  removePhoto: 'Rimuovi',
  photoOf: (side: string) => `Foto: ${side}`,
  photoTips:
    'Inquadra tutta la carta, su un piano scuro e opaco, con luce uniforme e senza riflessi.',
  scan: 'Scansiona',
  scanning: 'Scansione in corso…',
  matched: 'Carta riconosciuta:',
  ambiguous: (code: string) => `Il codice ${code} corrisponde a più carte. Qual è la tua?`,
  unreadable:
    'Non riesco a leggere il codice in basso sulla carta. Riprova con una foto più nitida ' +
    'e ben illuminata, oppure cerca la carta a mano.',
  notFound: (code: string) => `Il codice ${code} non corrisponde a nessuna carta del catalogo.`,
  searchByHand: 'Cerca a mano',

  searchTitle: 'Cerca una carta',
  searchLabel: 'Nome o codice della carta',
  searchPlaceholder: 'Pikachu, oppure SVI 025/198',
  searchHint: 'Per nome, oppure con il codice stampato in basso sulla carta.',
  searchedAsCode: (code: string) => `Cercato come codice: ${code}`,
  results: (count: number) => (count === 1 ? '1 carta' : `${count} carte`),
  noResults: 'Nessuna carta trovata.',
  noPrice: 'Prezzo n.d.',
  noImage: 'Immagine non disponibile',

  number: 'n.',
  rarity: 'Rarità',
  category: 'Categoria',
  hp: 'PS',
  types: 'Tipi',
  illustrator: 'Illustratore',
  released: 'Uscita',
  printedAs: 'Versioni',
  categories: { Pokemon: 'Pokémon', Trainer: 'Allenatore', Energy: 'Energia' } as Record<
    string,
    string
  >,
  // The catalog has the energy types in English.
  typeNames: {
    Colorless: 'Incolore',
    Darkness: 'Oscurità',
    Dragon: 'Drago',
    Fairy: 'Folletto',
    Fighting: 'Lotta',
    Fire: 'Fuoco',
    Grass: 'Erba',
    Lightning: 'Lampo',
    Metal: 'Metallo',
    Psychic: 'Psico',
    Water: 'Acqua',
  } as Record<string, string>,
  variants: {
    normal: 'Normale',
    reverse: 'Reverse holo',
    holo: 'Holo',
    firstEdition: 'Prima edizione',
    wPromo: 'Promo W',
  } as Record<string, string>,
  cardNotFound: 'Carta non trovata.',

  prices: 'Prezzi Cardmarket',
  priceGuideOf: (date: string) => `Listino del ${date}`,
  trend: 'Trend',
  low: 'Minimo',
  avg7: 'Media 7 giorni',
  avg30: 'Media 30 giorni',
  noPrices: 'Questa carta non ha un prezzo su Cardmarket.',
  sharedWith:
    'Lo stesso prodotto Cardmarket è collegato anche a queste carte, quindi i prezzi ' +
    'potrebbero riferirsi a una di loro:',

  history: 'Prezzo trend nel tempo',
  variant: 'Versione',
  period: 'Periodo',
  ranges: { 30: '30 giorni', 90: '3 mesi', 365: '1 anno' } as Record<number, string>,
  noHistory: 'Nessun prezzo trend registrato in questo periodo.',
  onePoint: (price: string, date: string) =>
    `Per ora c'è un solo giorno di prezzi: ${price} il ${date}. ` +
    'Lo storico cresce di un giorno alla volta.',
  chartLabel: (days: number, from: string, to: string, last: string) =>
    `Grafico del prezzo trend: ${days} giorni dal ${from} al ${to}, ultimo prezzo ${last}. ` +
    'Usa le frecce per scorrere i giorni.',
  showTable: 'Mostra i dati in tabella',
  date: 'Data',
  price: 'Prezzo',

  conditionTitle: 'Condizioni dalle tue foto',
  indicative: 'Stima indicativa',
  noEstimate: 'Nessuna stima complessiva',
  frontCentering: 'Centratura fronte',
  backCentering: 'Centratura retro',
  leftRight: 'sinistra-destra',
  topBottom: 'alto-basso',
  whitening: 'Sbiancatura dei bordi (retro)',
}

export type Strings = typeof it

const en: Strings = {
  locale: 'en-GB',
  tagline: 'Recognizes Pokémon cards from their printed code and shows the Cardmarket price.',
  navScan: 'Scan',
  navSearch: 'Search',
  language: 'Language',
  credits: 'Card data: TCGdex · Prices: Cardmarket price guide, updated daily',
  loading: 'Loading…',
  error: (message: string) => `Error: ${message}`,
  serverDown: 'The server is not answering. Is the backend running?',
  pageNotFound: 'This page does not exist.',
  goHome: 'Back to scanning',

  scanTitle: 'Scan a card',
  scanIntro:
    'Take a photo of the front: the card is recognized from the code printed at the bottom. ' +
    'Add the back for a condition estimate.',
  front: 'Front',
  frontHint: 'Required',
  backSide: 'Back',
  backHint: 'Optional, for the condition',
  choosePhoto: 'Choose or take a photo',
  removePhoto: 'Remove',
  photoOf: (side: string) => `Photo: ${side}`,
  photoTips: 'Fit the whole card, on a dark matte surface, with even light and no glare.',
  scan: 'Scan',
  scanning: 'Scanning…',
  matched: 'Card recognized:',
  ambiguous: (code: string) => `Code ${code} fits several cards. Which one is yours?`,
  unreadable:
    "I can't read the code at the bottom of the card. Try a sharper, well-lit photo, " +
    'or search for the card by hand.',
  notFound: (code: string) => `Code ${code} matches no card in the catalog.`,
  searchByHand: 'Search by hand',

  searchTitle: 'Search for a card',
  searchLabel: 'Card name or code',
  searchPlaceholder: 'Pikachu, or SVI 025/198',
  searchHint: 'By name, or with the code printed at the bottom of the card.',
  searchedAsCode: (code: string) => `Searched as a code: ${code}`,
  results: (count: number) => (count === 1 ? '1 card' : `${count} cards`),
  noResults: 'No cards found.',
  noPrice: 'No price',
  noImage: 'No image yet',

  number: 'no.',
  rarity: 'Rarity',
  category: 'Category',
  hp: 'HP',
  types: 'Types',
  illustrator: 'Illustrator',
  released: 'Released',
  printedAs: 'Printed as',
  categories: { Pokemon: 'Pokémon', Trainer: 'Trainer', Energy: 'Energy' },
  typeNames: {},
  variants: {
    normal: 'Normal',
    reverse: 'Reverse holo',
    holo: 'Holo',
    firstEdition: 'First edition',
    wPromo: 'W Promo',
  },
  cardNotFound: 'Card not found.',

  prices: 'Cardmarket prices',
  priceGuideOf: (date: string) => `Price guide of ${date}`,
  trend: 'Trend',
  low: 'Low',
  avg7: '7-day average',
  avg30: '30-day average',
  noPrices: 'This card has no Cardmarket price.',
  sharedWith:
    'The same Cardmarket product is linked to these cards too, so the prices may be ' +
    'those of one of them:',

  history: 'Trend price over time',
  variant: 'Version',
  period: 'Period',
  ranges: { 30: '30 days', 90: '3 months', 365: '1 year' },
  noHistory: 'No trend price saved in this period.',
  onePoint: (price: string, date: string) =>
    `So far there is one day of prices: ${price} on ${date}. ` +
    'The history grows by one day at a time.',
  chartLabel: (days: number, from: string, to: string, last: string) =>
    `Trend price chart: ${days} days from ${from} to ${to}, last price ${last}. ` +
    'Use the arrow keys to move through the days.',
  showTable: 'Show the data as a table',
  date: 'Date',
  price: 'Price',

  conditionTitle: 'Condition from your photos',
  indicative: 'Indicative estimate',
  noEstimate: 'No overall estimate',
  frontCentering: 'Front centering',
  backCentering: 'Back centering',
  leftRight: 'left-right',
  topBottom: 'top-bottom',
  whitening: 'Edge whitening (back)',
}

export const STRINGS: Record<Lang, Strings> = { it, en }

/** Cardmarket's own names for its condition scale, the same in every language. */
export const CONDITION_NAMES: Record<Condition, string> = {
  mint: 'Mint',
  near_mint: 'Near Mint',
  excellent: 'Excellent',
  good: 'Good',
  light_played: 'Light Played',
  played: 'Played',
  poor: 'Poor',
}

export const LangContext = createContext<{ lang: Lang; t: Strings }>({ lang: 'it', t: it })

export function useLang() {
  return useContext(LangContext)
}
