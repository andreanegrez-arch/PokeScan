# PokeScan

Scan a Pokémon card with your phone and get its current value on Cardmarket,
its price history and an indicative estimate of its condition.

A portfolio project that covers both **software development** (React + FastAPI
web app, computer vision) and **data analysis** (daily price pipeline, SQL,
analysis notebooks).

> **Status:** the app works end to end: the card catalog and the daily price
> history are collected, and the web app scans, searches and shows each card
> with its prices, price history and condition. Still to come: the analysis
> notebooks and the deploy.

## How a scan works

The same artwork is often reprinted in several sets, so PokeScan identifies a
card from the **code printed at the bottom**, never from the artwork alone:

1. Find the card in the photo and straighten it (OpenCV: edges, the largest
   four-sided shape with a card's proportions, perspective warp).
2. Read the bottom strip with OCR (RapidOCR, on the CPU): `025/198` on most
   cards, `SWSH020` on promos. Nothing found: the card may be upside down, so
   read it again turned around.
3. Look it up by number + official set size.
4. Several sets have that size (e.g. 136/189 exists in *Darkness Ablaze* and
   *Astral Radiance*): keep the card whose set code is printed next to the
   number (`SVI` on recent cards), then the one whose name is printed at the
   top.
5. Still undecided: show the candidates and let the user choose.
   No code readable: ask for a better photo (or a manual search by code).
6. A card was found: estimate its condition (see [Condition](#condition)).

### How well it works

Measured with `python -m jobs.evaluate_scan` on 298 random catalog cards (all
eras, English and Italian):

| Outcome | Clean images | Synthetic photos |
|---|---:|---:|
| Right card identified | 96.6% | 95.6% |
| Several candidates, the right one among them (the user picks) | 1.7% | 0.7% |
| Wrong card identified | 0.7% | 0.7% |
| Several candidates, the right one missing | 0.0% | 0.3% |
| Code read, but no card has it | 0.3% | 0.0% |
| No code read (asks for a better photo) | 0.7% | 2.7% |
| Median time per scan, front centering included (laptop CPU) | 1.72 s | 1.51 s |

*Clean images* are TCGdex's scans of the cards. *Synthetic photos* put them on
a random background with a tilt (sometimes sideways or upside down),
perspective, uneven light, blur, noise and JPEG compression. Real photos also
have glare, sleeves and fingers, so expect lower numbers until it is measured
on real photos. Remaining errors: partly read codes (`1/ 9`), sets of the same
size where the name doesn't settle it (e.g. the two halves of a Trainer Kit
hold the same card), and Italian cards of sets TCGdex has no Italian names
for. One row per card, with the centering measured on both, is in
[`analysis/data/scan_evaluation.csv`](analysis/data/scan_evaluation.csv).

### Condition

With a photo of the back too, PokeScan estimates the condition on
Cardmarket's scale (Mint to Poor). It is an **indicative estimate from
photos, not a professional grade**: it measures centering and whitening on
the edges, not bends, creases or scratches on the surface.

- **Centering**, front and back: how wide the printed border is on each
  side, e.g. 55/45 left-right. The outline used for recognition is only
  roughly right, so the card's four edges are found first and the card is
  straightened from where they meet. On each side the border runs from the
  card's edge to where its color stops for at least about 1 mm, so text
  printed on the border (the copyright line) doesn't end it. A result is
  dropped when it doesn't look like one printed border: left + right far
  from top + bottom, or sides of different colors.
- **Whitening**, on the back: its dark blue border turns white where the
  edges and corners are worn. PokeScan measures the share of white pixels in
  a 1 mm band along the edge, for each corner and side, and names the worn
  ones.
- **Grade**: the best grade that every measure allows. These are PokeScan's
  own thresholds, not Cardmarket's (which has no numeric ones):

| Grade | Front centering, worse side | Back centering | Most worn corner or side | Whole edge |
|---|---|---|---|---|
| Mint | 55/45 | 60/40 | 1% | 0.5% |
| Near Mint | 60/40 | 70/30 | 6% | 1.5% |
| Excellent | 70/30 | 80/20 | 20% | 4% |
| Good | any | any | any | 10% |
| Light Played | any | any | any | 20% |
| Played | any | any | any | 35% |
| Poor | | | | more |

Without the back photo, `/scan` gives the front's centering only. Photograph
the back on a **dark, plain surface**: on a light one the edge can't be told
apart from whitening, and PokeScan says so instead of grading.

How well the centering works, from the same evaluation (front only, on the
255 cards identified that have a plain border):

| | Synthetic photos |
|---|---:|
| Centering measured | 90.6% |
| Error, median | 1.0 points |
| Error, 90th percentile | 2.3 points |
| Error over 5 points | 5 cards (2.2%) |

The error is the difference from the centering measured on the clean image,
on the worse of left-right and top-bottom: 1 point is 51/49 instead of
50/50, a few hundredths of a millimeter of border. When the borders don't add up,
PokeScan gives no centering rather than a wrong one. Cards without a plain
border (full art) are not measured. Whitening has only been tested on drawn
card backs (`backend/tests/test_condition.py`): TCGdex has no photos of
card backs, so it still has to be measured on real ones.

## The app

Three screens, in Italian or English. The language also picks the card names
and the condition notes.

- **Scan**: a photo of the front, and optionally of the back, taken with the
  camera or uploaded. On a computer, the webcam opens inside the page, with a
  card-shaped frame and a check of the shot before using it. On a phone, the
  button opens the camera app, which takes sharper photos than a video frame.
  A recognized card opens straight away. When the code fits several cards, the app lists them to
  pick from. When the code can't be read, it offers the search.
- **Search**: by name, or by the printed code (`SVI 025/198`). The query is in
  the URL, so a search can be bookmarked or shared.
- **Card**: details and Cardmarket prices: trend, low, and 7- and 30-day
  averages, for the normal and reverse holo versions. Below them, the trend
  price over 30 days, 3 months or 1 year, and, after a scan, the condition from
  the photos.

The price chart is plain SVG, with no chart library:

- one series, with the y axis starting at zero;
- the last price labelled at the end of the line;
- a crosshair that follows the pointer or the arrow keys;
- every value also available in a table.

The history grows by one day each time the price job runs. With fewer than two
days saved, the card page shows the single price instead of a chart.

There is no router library either. Routes live after the `#` (`#/search?q=…`,
`#/card/sv01-025`), so the build is plain static files.

## Architecture

```
React app (camera) ──► FastAPI ──► PostgreSQL ◄── daily price job ◄── Cardmarket price guide
                          │             ▲
                          │             └──────── catalog import ◄── TCGdex (cards, images,
                          └─ recognition (OpenCV + OCR)                set codes, symbols)

analysis/ (SQL + Jupyter) ──► PostgreSQL
```

## Stack

| Layer       | Technology                                   |
|-------------|----------------------------------------------|
| Frontend    | React, TypeScript, Vite                      |
| Backend     | Python, FastAPI, Pydantic                    |
| Database    | PostgreSQL                                   |
| Recognition | OpenCV, RapidOCR (PaddleOCR models, ONNX)    |
| Analysis    | SQL, pandas, Jupyter                         |

## Project structure

```
backend/    FastAPI app, recognition, catalog import, daily price job, tests
db/         PostgreSQL schema
frontend/   React + TypeScript app: scan, search, card page with price chart
analysis/   SQL queries and Jupyter notebooks
```

## Getting started

Requirements: Python 3.12+, Node.js 22+, PostgreSQL (or Docker).

### Database

Install PostgreSQL 18 (on Windows: `winget install -e --id PostgreSQL.PostgreSQL.18 --interactive`)
and remember the password you choose for the `postgres` user.
Or use Docker instead: `docker compose up -d`, and in `backend/.env` use the
Docker values shown in `.env.example`.

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # then set DATABASE_PASSWORD in .env
python -m app.init_db           # creates the database and the tables
python -m jobs.import_catalog   # card catalog from TCGdex (first run: ~15 min)
uvicorn app.main:app --reload
```

Interactive API docs: <http://localhost:8000/docs>. Tests: `pytest`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>, with the backend running. The app calls the API
at `/api`, and Vite forwards it to the backend at `http://localhost:8000`. To
call an API somewhere else, copy `.env.example` to `.env.local` and set
`VITE_API_URL`. Checks: `npm run build` (types and build) and `npm run lint`.

**From a phone**, on the same Wi-Fi as the computer:

1. Run `npm run dev:lan` instead of `npm run dev`. Vite prints a `Network`
   address such as `http://192.168.1.7:5173`.
2. Open that address on the phone.

Only Vite is reachable from the network. The backend stays on localhost, behind
Vite's forwarding, so it needs no CORS change. The page is plain http, which is
enough for the phone's "Take photo" button, since it opens the camera app. The
webcam window used on computers needs https or localhost. If the phone can't
connect, check that Windows Firewall allows Node.js on the current network.

The TypeScript API types in `frontend/src/api/schema.d.ts` are generated from
the backend. After changing `backend/app/schemas.py`, start the backend and run
`npm run gen:api`.

### Catalog import

Imports every Pokémon TCG set and card from [TCGdex](https://tcgdex.dev), in
English and Italian. Pokémon TCG Pocket (digital only) is left out.

| Table | Content |
|---|---|
| `sets` | Series, printed set code (`SVI`), official size (the `198` in `025/198`), symbol, release date. |
| `set_translations` | Set name and logo per language. |
| `cards` | Number in the set, rarity, category, types, HP, variants, illustrator, and `cardmarket_id`, which joins to the price tables. |
| `card_translations` | Card name and image per language. |

TCGdex gives the Cardmarket id only on each card's own page, so the first run
reads about 21,000 pages. It saves every 500 cards: if interrupted, run it
again and it resumes. Later runs read only new cards, plus (once a week) cards
still without a Cardmarket id. Running it weekly is enough: new sets come out
every few months.

```bash
cd backend
python -m jobs.import_catalog
```

To schedule it weekly on Windows (Monday 07:00, log in `backend/import_catalog.log`):

```powershell
$backend = "C:\path\to\PokeScan\backend"
$action = New-ScheduledTaskAction -Execute "$backend\.venv\Scripts\pythonw.exe" `
  -Argument "-m jobs.import_catalog --log-file import_catalog.log" -WorkingDirectory $backend
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "07:00"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "PokeScan - catalogo TCGdex" -Action $action `
  -Trigger $trigger -Settings $settings
```

First import: 206 sets and 21,432 cards (14,640 with Italian names); 96% of
the cards have a Cardmarket id. **Known limitation:** about 1,800 cards share
their Cardmarket id with another card, mostly TCGdex mapping errors in older
sets (e.g. the regular and the full-art Trevenant EX of *Primal Clash* point to
the same product). The price of such a card is uncertain until those ids are
cross-checked with Cardmarket's product list.

Prices of one card, e.g. the latest daily price list joined to the catalog:

```sql
SELECT c.card_id, t.name, p.trend, p.trend_holo AS trend_reverse
FROM cards c
JOIN card_translations t ON t.card_id = c.card_id AND t.lang = 'it'
JOIN cardmarket_prices_daily p ON p.id_product = c.cardmarket_id
WHERE c.card_id = 'swsh3-136'
  AND p.day = (SELECT max(snapshot_date) FROM price_guide_runs);
```

### Daily price job

Reads Cardmarket's price guide (about 80,000 Pokémon products) and stores
**only what changed** since the previous day:

| Table / view | Content |
|---|---|
| `cardmarket_prices` | A row when a product first appears, when any of its prices changes, or when it is no longer listed (`listed = false`). |
| `price_guide_runs` | One row per saved day, even without changes, with how many rows it added. |
| `cardmarket_prices_daily` | View that rebuilds the full price list of every saved day. Use it for analysis. |

Every day not saved is lost, so schedule it daily. It is safe to run more
than once: a day already saved, or older than the latest saved, is skipped.

```bash
cd backend
python -m jobs.snapshot_prices --dry-run   # download and check only
python -m jobs.snapshot_prices             # download and save
```

On Windows, schedule it with Task Scheduler from PowerShell (adjust the path).

- It runs at 06:00, 12:00 and 18:00: a day already saved is skipped, so the
  later runs only matter if an earlier one failed (e.g. no network).
- `-StartWhenAvailable` runs it as soon as possible if the PC was off; the
  file stays valid all day, so a late run still saves the right day.
- `pythonw` opens no window; the log goes to `backend/snapshot_prices.log`.

```powershell
$backend = "C:\path\to\PokeScan\backend"
$action = New-ScheduledTaskAction -Execute "$backend\.venv\Scripts\pythonw.exe" `
  -Argument "-m jobs.snapshot_prices --log-file snapshot_prices.log" -WorkingDirectory $backend
$triggers = "06:00", "12:00", "18:00" | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ }
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName "PokeScan - prezzi Cardmarket" -Action $action `
  -Trigger $triggers -Settings $settings
```

## API

| Method | Path                          | Description                                          |
|--------|-------------------------------|------------------------------------------------------|
| POST   | `/scan`                       | Identify a card from its code; estimate its condition |
| GET    | `/cards/search?q=`            | Manual search by code (`SVI 025`) or name            |
| GET    | `/cards/{card_id}`            | Card details and latest Cardmarket prices            |
| GET    | `/cards/{card_id}/prices`     | Daily price history for one variant                  |

`/scan` takes a `front` photo and an optional `back` photo, and answers with
the card (or the candidates) and its `condition`. All endpoints
accept `lang=it|en`. Card ids are TCGdex ids (e.g. `swsh3-136`), prices are in
EUR, variants are `normal` and `reverse` (reverse holo).

**Search** reads the query as a card code when it is one, using the same rules
the scan will use:

| Query | Matches |
|---|---|
| `DAA 136`, `SVI 025/198`, `svi 25` | set code + number (leading zeros and spacing don't matter) |
| `136/189` | number + official set size: may match several sets (here *Darkness Ablaze* and *Astral Radiance*), newest first |
| `TG05/TG30`, `SWSH020` | subset and promo numbers |
| `Pikachu`, `ricerca accademica` | names in Italian or English: exact names first, then newest sets |

Names, set names and images come in the requested language, falling back to
English. Card details include `prices.shared_with`: the other cards linked to
the same Cardmarket product (see the known limitation of the catalog), whose
prices are therefore uncertain. If PostgreSQL is down, the endpoints answer
`503`.

## Data sources

| Source | Used for | Updated |
|--------|----------|---------|
| [Cardmarket price guide](https://news.cardmarket.com/en/Magic/were-making-the-price-guide-and-product-catalogue-available-for-download) | Daily prices (EUR) of every Pokémon product | Once a night, around 02:50 Italian time |
| [TCGdex](https://tcgdex.dev) | Catalog: cards, sets, set codes and symbols, images (Italian included), Cardmarket product ids | Cards when sets are released; its own prices around midnight |

The daily job stores Cardmarket's price guide; the catalog links each card to
its Cardmarket product id, so prices and cards join with a single SQL `JOIN`.

## Roadmap

- [x] Skeleton: API contract, database schema, frontend
- [x] Daily price snapshot job (Cardmarket price guide)
- [x] Card catalog import from TCGdex
- [x] Search and card detail endpoints
- [x] Card identification from the printed code (OCR, set code, card name)
- [x] Condition estimate: centering, edge whitening
- [x] Web app: scan, search, card page with prices, price history chart and condition
- [ ] Settle the remaining ties by comparing the set symbol or the artwork
- [ ] Measure recognition and condition on real phone photos (whitening has
      only been tested on drawn card backs)
- [ ] Analysis notebooks
- [ ] Deploy
