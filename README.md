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
card from the **code printed at the bottom**, and uses the artwork to check
it within the edition the code points to (see
[Recognition by illustration](#recognition-by-illustration)):

1. Find the card in the photo and straighten it (OpenCV: edges, the largest
   four-sided shape with a card's proportions, perspective warp).
2. Read the bottom strip with OCR (RapidOCR, on the CPU): `025/198` on most
   cards, `SWSH020` on promos. Nothing found: the card may be upside down, so
   read it again turned around.
3. Look it up by number + official set size. A code no card has may be a
   misreading (`5107/236` for 107/236, a symbol read as a 5), so corrected
   versions are tried too. So are digits read without their slash, as the
   small print of old cards often is (`1102` for 1/102 or 11/102, `702` for
   7/102), after every code read with one.
4. Several sets have that size (e.g. 136/189 exists in *Darkness Ablaze* and
   *Astral Radiance*): keep the card whose set code is printed next to the
   number (`SVI` on recent cards), then the one whose name is printed at the
   top. A corrected code is kept only if the set code or the name agrees with
   it. The first real phone photo showed why: a Chinese card's code, misread
   as `161/5`, was "corrected" to 1/5, and two unrelated cards were offered.
5. Where editions of that size have fingerprints of their illustrations, the
   card's illustration is compared with them all, unless the set code or the
   name already settled it: a clear match confirms the number, or corrects it
   (Raichu's 14/102 read as `4/102` was offered as Charizard), and picks the
   edition when two share the size (`/102`: Base Set or Triumphant). No code
   read at all: the illustration is looked for in every edition, last.
6. Still undecided: show the candidates and let the user choose.
   Nothing recognized: ask for a better photo (or a manual search by code).
7. A card was found: estimate its condition (see [Condition](#condition)).
   The same when the code is read but no card has it (a Japanese or Chinese
   card, say): the card isn't in the catalog, yet its condition can be
   measured all the same.

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
[`analysis/data/scan_evaluation.csv`](analysis/data/scan_evaluation.csv)
(measured before recognition by illustration).

### Recognition by illustration

On real photos of old cards, OCR misreads the small print of the code: 44
phone photos of Italian Base Set cards (16 holos, most photographed twice,
front and back) gave the right card only half the time. Raichu's `14/102`
read as `4/102` was offered as Charizard; `11/102` read as `1102`, or nothing
read at all. So the card's illustration is compared with the illustrations of
the edition the code points to:

- **Fingerprints, not pictures.** For each card of an edition,
  `python -m jobs.build_fingerprints` downloads its English picture from
  pokemontcg.io into a temporary folder, fingerprints the artwork box and
  deletes the picture. A fingerprint is up to 500 ORB keypoints (OpenCV: no
  model to download). Done for every edition pokemontcg.io has pictures of,
  from Base Set to Mega Evolution, promos, POP series, McDonald's sets and
  trainer kits included: 174 editions, 20,658 cards, 19,438 illustrations,
  408 MB in `backend/fingerprints/` (one SQLite file per edition), from
  15 GB of pictures. The cards pokemontcg.io has no picture of (most trainer
  kits, McDonald's 2014, 2015, 2017 and 2018, the newest Scarlet & Violet
  and Mega Evolution promos: about 770) have none, and are still found by
  their code.
- **The artwork box only**, a little inside its frame, measured per era
  (Wizards, e-Card, EX, Diamond & Pearl and Platinum, HeartGold &
  SoulSilver, Black & White and XY, Evolutions' old frame, Sun & Moon on) and
  per kind of card (Pokémon, Trainer, Energy; LEGEND halves). A full-art card
  (ex, GX, V, illustration rares, full-art Trainers) has the illustration
  everywhere, under its text: its box is its era's, the part of the picture
  with the least text on it, between the name and the attacks. Reprints in
  an old frame (Celebrations' and the 30th anniversary's Classic Collection)
  take the box of the illustration of the same name they agree with most.
  No printed text, so one fingerprint serves
  every language, linked to each printing (language, edition, number, name
  from that language's official list). Cards printed only in Japanese, Korean
  or Chinese, with their own editions and numbers, will have fingerprints of
  their own; names in a language a card was never printed in will be marked
  as not official. The files have room for both. TCGdex has Italian names
  for Base Set, Jungle and most editions from Black & White on (14,176 of
  the 20,658 cards, none from EX to HeartGold & SoulSilver): the other
  Italian printings are left out rather than translated.
- **One fingerprint per illustration.** A reprint (Base Set 2, Legendary
  Collection) links its printing to the illustration of the card it reprints
  when the two pictures agree on 80 keypoints or more; the number and set
  size read then pick the printing. A holo and its non-holo (Jungle, Fossil,
  Neo, e-Card) agree on far fewer (7 to 60) and keep a fingerprint each: on
  simulated photos of such pairs, the photo prefers its own printing (84-91%,
  never the other one), where one shared fingerprint recognized the
  illustration in 23-86% of them. Two illustrations of the same Pokémon
  never agreed on more than 4 keypoints (343 pairs).
- **The lines, not the shading.** Before the keypoints, shading broader
  than 6 px is taken out of the box: a holo background is dark on the
  picture and light on a photo catching the foil, which left Raichu's
  keypoints with nothing in common.
- **Matched by position too.** Photo and picture are straightened to the
  same size, so matching keypoints must agree on one transform close to the
  identity (RANSAC). A match is kept with at least 12 keypoints in agreement
  and twice as many as the next illustration; 20 when nothing was read and
  every edition is searched. On the 44 photos, the right illustration had 16
  to 144, any other at most 6, and cards of other sets at most 4.
- **The editions come from the set size read**, all those printed with it:
  `/102` is Base Set or Triumphant, `/64` Jungle or Neo Revelation, `/132`
  Gym Heroes or Gym Challenge, `H32` Aquapolis's or Skyridge's holos; the
  illustration decides. Or from the user (`set_id` on `/scan`). Only when no
  code is read are all editions searched: 0.8 s a search on average (1.4 s
  for 95% of photos), thanks to the shortlist below. A scan that reads no
  code takes 4.6 s (median, 26 simulated photos with the code covered)
  against 1.1 s with one read, mostly OCR trying every way to read it.
- **500 keypoints** per illustration: on the 44 photos, 400 left Raichu
  undecided twice, 500 to 800 recognized every card. Base Set's file: 1.7 MB
  at 400, 1.9 MB at 500, 2.3 MB at 600, 3.0 MB at 800.
- **A shortlist first.** Comparing a photo keypoint by keypoint with each
  of 19,438 illustrations would take minutes. So each keypoint is also
  given a visual word: the nearest of 2,048 reference descriptors
  (`backend/fingerprints/vocabulary.bin`, 64 kB, drawn from the Wizards
  era's descriptors), and the quarter of the box it is in. An index of
  those words scores every illustration at once (tf-idf, cosine), and only
  the 100 best are compared in full. Chosen on 100 simulated photos against
  the Wizards era (1,523 illustrations): the right illustration ranked first
  for 95% of them and never below 7th, for 63 ms a search. 4,096 words in
  ninths of the box ranked it first more often (99%) but once 913th;
  4,096 words without the quarters, 93% and once 112th; 1,024, 73%.
  Fingerprints are read from their file only when compared, the last 4,000
  kept in memory. The backend starts in 1.5 s and reads the files' indexes
  at the first scan (4.6 s for the 178 files); it then takes about 350 MB
  (working set), 600-700 MB of private memory with the OCR models and
  4,000 fingerprints loaded.

`python -m jobs.build_fingerprints <editions> --check 12` also turns 12
cards per edition into simulated phone photos (perspective, uneven light,
blur, noise, glare, JPEG) and recognizes them against every edition's
fingerprints. On the Wizards era and Triumphant (16 editions, 192 photos):
189 right with the set size known, 188 searching every edition, none wrong.
On the other 158 editions (1,882 photos, against all 19,438 illustrations):

| Era | Photos | Set size known: right / wrong / undecided | Every edition searched: right / wrong / undecided |
|---|---:|---:|---:|
| Wizards-era promos | 33 | 100% / 0 / 0 | 88% / 0 / 4 |
| EX | 320 | 99.0% / 0 / 3 | 87% / 1 / 41 |
| Diamond & Pearl, Platinum | 204 | 98.5% / 0 / 3 | 96% / 0 / 8 |
| HeartGold & SoulSilver | 60 | 100% / 0 / 0 | 100% / 0 / 0 |
| Black & White | 180 | 98.3% / 0 / 3 | 94% / 0 / 10 |
| XY | 204 | 96.6% / 0 / 7 | 91% / 3 / 15 |
| Sun & Moon | 228 | 97.8% / 0 / 5 | 92% / 0 / 18 |
| Sword & Shield | 329 | 96.0% / 0 / 13 | 91% / 1 / 27 |
| Scarlet & Violet | 216 | 91.2% / 0 / 19 | 82% / 1 / 38 |
| Mega Evolution | 108 | 91.7% / 0 / 9 | 87% / 0 / 14 |
| **All** | **1,882** | **96.7% / 0 / 62** | **90.4% / 6 / 175** |

With the set size known, no photo was taken for a wrong card. Searching
every edition, most undecided photos are of illustrations printed again in
another era's frame (Potion, Energy Search, basic Energies, Evolutions'
reprints of Base Set), which agree with both printings: no edition is picked
rather than a wrong one. Of the 6 wrong, 4 are that same illustration in
another edition (Evolutions' Kakuna, Poliwag and Rattata taken for Base
Set's, EX Potion for e-Card's); Flaaffy's promo was taken for Galarian
Zapdos', both stamped with the same big Evolving Skies logo over the
illustration; and one Ninetales for another. Full-art, illustration and
secret rares (317 photos): 93.7% right with the set size known, against
97.3% for the other cards, none wrong. Simulated photos
are kinder than real ones (no sleeves, no fingers, flat cards), and only Base
Set has been tested on real photos so far.

Measured on the same 44 photos, each change alone and both (the thresholds
were chosen on these photos too, so expect less on new ones):

| Outcome | Before | Number rules | Illustration | Both |
|---|---:|---:|---:|---:|
| Right card identified | 50.0% | 84.1% | 100% | 100% |
| Several candidates, the right one missing | 13.6% | 11.4% | 0% | 0% |
| Code read, but no card has it | 0% | 4.5% | 0% | 0% |
| No code read | 36.4% | 0% | 0% | 0% |
| Wrong card identified | 0% | 0% | 0% | 0% |
| Median time per scan (laptop CPU) | 2.97 s | 1.42 s | 2.93 s | 1.56 s |

The number rules alone settle the codes read without a slash, when the name
read at the top agrees; they can't fix a digit read wrong (`4/102` for 14/102
or 6/102). The illustration settles all of them. Scans get faster because a
code read early saves reading the photo in other ways.

### Condition

With a photo of the back too, PokeScan estimates the condition on
Cardmarket's scale (Mint to Poor). It is an **indicative estimate from
photos, not a professional grade**: it measures centering and whitening on
the edges, not bends, creases or scratches on the surface.

- **Centering**, front and back: how wide the printed border is on each
  side, e.g. 55/45 left-right. The outline used for recognition is only
  roughly right, so the card's four edges are found first and the card is
  straightened from where they meet. Of the edges near each side, the card's
  is the one alike on all four sides (from the table to the border, same hue
  inside): the first real photo had a keyboard just above the card, whose
  edge was taken for the card's. On each side the border runs from the
  card's edge to where its color stops for at least about 1 mm, so text
  printed on the border (the copyright line) doesn't end it. Where a holo
  border's color drifts with the light on one side, that side's border runs
  to the next edge instead. A
  result is dropped when it doesn't look like one printed border: left +
  right far from top + bottom, or sides of different colors. Besides the
  ratio, PokeScan says by how much one border is wider, in mm (e.g. right
  border 0.6 mm wider).
- **Border ends that don't show**: on a silver card with a silver frame (the
  Metal type in the Scarlet & Violet and Mega Evolution eras) the light can
  make the frame look just like the border on one side, with no line to
  measure. For a card found in the catalog, PokeScan then aligns the photo
  with TCGdex's picture of the card, on points of its artwork and text
  (SIFT), and carries the border's end over from the picture. Off-center
  printing moves the whole print, so the picture shows where that line is
  printed on this very card. The picture's own cut is never used, as it
  isn't always centered either (recent sets have digital pictures, a pixel
  or two off; older ones, scans of real cards): the cut is measured on the
  photo. The line carried over must match the photo within 0.15 mm on the
  sides where it shows, one across and one down; else no centering.
- **The back's edge on a black table**: in dim light, the dark blue border's
  edge can be too faint and blurred to find. It is looked for again over a
  wider span, keeping only edges with the back's blue inside (the first real
  photo had the frame around the artwork taken for the card's edge).
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
the back on a **dark, plain, matte surface, with good light**: on a light
one the edge can't be told apart from whitening, and PokeScan says so
instead of grading; on a black one in dim light its dark blue barely shows.

How well the centering works, from the same evaluation (front only, on the
261 cards identified that have a plain border). The photo alone: the
synthetic photos are made from TCGdex's pictures, which would align with
themselves perfectly.

| | Synthetic photos |
|---|---:|
| Centering measured | 92.0% |
| Error, median | 1.0 points |
| Error, 90th percentile | 2.3 points |
| Error over 5 points | 5 cards (2.1%) |

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
  fingerprints/  illustrations' fingerprints, one SQLite file per edition
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
# optional, the fingerprints are in the repository (174 editions, 408 MB).
# To make them again, name the editions (TCGdex ids) in release order, so a
# reprint finds the illustration it reprints: ~15 GB of pictures for all of
# them, each deleted once fingerprinted, ~4.5 h. --skip-built leaves the
# editions already made, so a run stopped halfway picks up where it was:
# python -m jobs.build_fingerprints base1 base2 base3 ... me06 --skip-built
# python -m jobs.build_fingerprints --vocabulary      # visual words, from the files made
# python -m jobs.build_fingerprints --reindex         # give every file those words
# python -m jobs.build_fingerprints --check-photos DIR  # recognize test photos kept by --photos DIR
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

To study real phone photos, start the backend with `SCAN_ARCHIVE_DIR` set (see
`backend/.env.example`): each scan is kept in a folder of its own, with the
photos and the result. The folder is inside `backend/.cache/`, which git
ignores.

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
the card (or the candidates) and its `condition`. An optional `set_id`
(e.g. `base1`) is the edition, when the user knows it: its illustrations are
compared whatever set size is read. All endpoints
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
| [pokemontcg.io](https://pokemontcg.io) | English pictures of the cards, Wizards era to Mega Evolution, to fingerprint their illustrations (pictures deleted once done). For the newest Mega Evolution editions (me02.5 to me06, the 30th anniversary set and its Classic Collection) its lists give pictures at images.scrydex.com | When an edition's fingerprints are made |

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
- [x] Recognition by illustration (fingerprints per edition): every edition
      pokemontcg.io has pictures of, Wizards era to Mega Evolution
- [ ] Fingerprints of the cards pokemontcg.io has no picture of (trainer
      kits, some promos and McDonald's sets); choosing the edition by hand
      in the app
- [ ] Settle the remaining ties by comparing the set symbol
- [ ] Measure recognition and condition on more real phone photos (so far 44
      photos of Base Set cards; whitening has only been tested on drawn card
      backs)
- [ ] Analysis notebooks
- [ ] Deploy
