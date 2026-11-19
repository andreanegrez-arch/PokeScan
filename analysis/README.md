# Analysis

Exploratory analysis of the price history collected by the backend.
Query the `cardmarket_prices_daily` view (one full price list per day) and
join it to the catalog through `cards.cardmarket_id`. The underlying
`cardmarket_prices` table stores only the changes.

- `sql/` – hand-written SQL queries, one file per question.
- `notebooks/` – Jupyter notebooks that load query results with pandas and
  turn them into charts and written conclusions.

## Questions to answer

- How do prices of a new set evolve in the first weeks after release?
- Which features (rarity, Pokémon, set age) best explain a card's price?
- How volatile are prices, and does volatility depend on the price level?
