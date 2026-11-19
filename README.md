# PokeScan

Scan a Pokémon card with your phone and get its current value on Cardmarket,
its price history and an indicative estimate of its condition.

A portfolio project that covers both **software development** (React + FastAPI
web app, computer vision) and **data analysis** (daily price pipeline, SQL,
analysis notebooks).

> **Status:** skeleton. The API contract, database schema and frontend are in
> place; the endpoints return `501 Not Implemented` until they are built.

## How a scan works

The same artwork is often reprinted in several sets, so PokeScan identifies a
card from the **code printed at the bottom**, never from the artwork alone:

1. Find the card in the photo and straighten it (OpenCV).
2. Read the code with OCR: `SVI 025/198` on recent cards, `136/189` on older ones.
3. Look it up by set code + number, or by number + official set size.
4. If several sets have the same size, compare the printed set symbol.
5. Still undecided: show the candidates and let the user choose.
   No code readable: ask for a better photo (or a manual search by code).

With an optional photo of the back, PokeScan also estimates the condition:
centering (front and back) and whitening on the edges. It is an indicative
estimate on Cardmarket's scale, not a professional grade.

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
| Recognition | OpenCV, OCR (planned)                        |
| Analysis    | SQL, pandas, Jupyter                         |

## Project structure

```
backend/    FastAPI app, daily price job, tests
db/         PostgreSQL schema
frontend/   React + TypeScript app
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
uvicorn app.main:app --reload
```

Interactive API docs: <http://localhost:8000/docs>. Tests: `pytest`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>.

The TypeScript API types in `frontend/src/api/schema.d.ts` are generated from
the backend. After changing `backend/app/schemas.py`, start the backend and run
`npm run gen:api`.

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

`/scan` takes a `front` photo and an optional `back` photo. All endpoints
accept `lang=it|en`. Card ids are TCGdex ids (e.g. `swsh3-136`), prices are in
EUR, variants are `normal` and `reverse` (reverse holo).

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
- [ ] Card catalog import from TCGdex
- [ ] Search and card detail endpoints
- [ ] Card identification from the printed code (OCR + set symbol)
- [ ] Condition estimate: centering, edge whitening
- [ ] Price history chart in the frontend
- [ ] Analysis notebooks
- [ ] Deploy
