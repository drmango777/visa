# Reentry Compass

An AI agent for people in their first weeks after release in Miami-Dade. It finds where someone can afford to live **and** still make every check-in and curfew, catches conflicts between their obligations before they turn into violations, and logs those conflicts anonymously as evidence for local decision-makers.

Built for Impact Miami 2.0 (Agents for Access + Agents for Civic Life).

## What it does

1. You enter a check-in location, when it ends, a curfew, a budget, and a radius.
2. The agent ranks nearby neighborhoods by whether transit gets you home before curfew and whether rent fits.
3. It shows the ride home against the curfew line for every area, so a conflict is obvious at a glance.
4. It finds help nearby (clinics, libraries, housing that accepts records) and upcoming county decisions that affect those areas, with who decides and how to comment.
5. It drafts messages: a request to the officer to move a check-in, a note to a housing contact, a public comment.
6. Every conflict is logged as a pattern only (no names), building a picture of where the system's rules collide with real life.

## How it works

- **Backend:** Python + FastAPI (`main.py`)
- **Agent:** OpenAI function calling (`agent.py`). The model decides which tools to call and writes the plan in English, Spanish, or Haitian Creole.
- **Tools:** plain Python (`tools.py`): `find_areas`, `get_help_nearby`, `get_civic_alerts`, plus anonymous conflict logging.
- **Front end:** one HTML page (`static/index.html`). The API key never reaches the browser.
- **Backup mode:** if there's no API key or the API fails, the same tools run in a fixed order with template text, so the demo never breaks.

## Run it locally

```bash
pip install -r requirements.txt
cp .env.example .env        # then paste your OpenAI key into .env
uvicorn main:app --reload
```

Open http://localhost:8000 and click **Try the example: Marcus**.

## Deploy (Render, free)

1. Push this repo to GitHub.
2. On render.com: **New > Web Service**, pick the repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Under **Environment**, add `OPENAI_API_KEY` (and optionally `OPENAI_MODEL`).

## Data: switch from sample to real

The repo ships with **sample data** so the demo always works. To switch to real data, run this on your laptop (it needs internet):

```bash
python scripts/update_all_data.py
```

| Data | Real source | Script |
| --- | --- | --- |
| Apartment rent by ZIP | U.S. Census ACS median gross rent | `fetch_census_rent.py` |
| Clinics, libraries, shelters, counseling | OpenStreetMap | `fetch_places_osm.py` |
| Ride home vs. curfew | Miami-Dade Transit weekday schedule (GTFS), with walking, waiting, and transfers | `build_transit_from_gtfs.py` |
| Civic alerts | Update by hand from real county agendas | edit `data/civic_alerts.json` |

The app automatically uses the real files when they exist (`transit_real.json`, `help_real.json`) and says which sources it's using on the results page. Commit the new files in `/data` so the deployed site uses them too.

**Before running:** set the real address coordinates of each check-in office in `data/checkin_sites.json` (the sample ones are approximate). If the GTFS download fails, download the zip from Miami-Dade Transit yourself, save it as `data/gtfs.zip`, and run `python scripts/build_transit_from_gtfs.py`.

Still sample after the update: room rents (estimates), whether housing accepts people with records (no public data; verify with partners), and the first 5 rows of the conflict log.

## Safety

- Stores no names or case details. Conflict logs are patterns only.
- Not legal advice. Anything about release conditions should be confirmed with an officer or case manager.
- Never tells anyone how to vote or what position to take.
