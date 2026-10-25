# Reentry Compass

A simple website for people coming home from prison in Miami-Dade. Type an address to see a map, how much rent and prices have gone up, and which buses are nearby (and what changed on them). Or enter the date you went in and see everything that changed since.

Covers three areas for now: **Little Havana, Downtown, Overtown.**
Built for Impact Miami 2.0 (Access + Civic Life).

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env                  # add keys if you have them (all optional)
python scripts/update_all_data.py     # pulls real data from the civic websites (needs internet)
python -m uvicorn main:app --reload
```

Open http://localhost:8000.

## Where the data comes from

| What | Source | How it's loaded |
| --- | --- | --- |
| Rent by ZIP, 2011 to latest | U.S. Census Bureau, American Community Survey (median gross rent) | `scripts/fetch_census_rent.py` |
| Prices (Miami CPI, gas, eggs, milk, ...) | U.S. Bureau of Labor Statistics | `scripts/fetch_bls_prices.py` |
| Bus stops, routes, and route lines | Miami-Dade Transit GTFS feed | `scripts/fetch_transit.py` |
| What changed on each bus route | County Better Bus route-by-route guide (Nov 2023) | hand-entered: `data/route_changes.json` |
| Timeline (minimum wage, rights, ID, phones, transit) | Official pages, each linked | hand-entered: `data/timeline.json` |

Until you run the update script the site shows "not loaded yet" instead of making numbers up.
Rent numbers are 5-year averages, so they trail today's asking rents. Item prices (gas, eggs, ...) are U.S. averages. The "all prices" number is for the Miami area.

## Keys (all optional)

- `GOOGLE_MAPS_API_KEY`: shows a Google Map. Without it the app uses an OpenStreetMap map. In Google Cloud, enable **Maps JavaScript API** and restrict the key to your site's URL.
- `OPENAI_API_KEY`: powers the "Explain it" button (English, Spanish, Haitian Creole) with function calling. Without it, a template explains the same numbers.
- `CENSUS_API_KEY`, `BLS_API_KEY`: higher limits for the data scripts.

## Deploy (Render)

Build: `pip install -r requirements.txt`. Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`.
Add the keys under Environment. Commit the files the update script creates in `/data` so the deployed site has them.

## Privacy

Addresses and dates are used to answer the request and are never saved or logged. Not legal advice.
