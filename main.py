"""Reentry Compass web server.

Run locally:   python -m uvicorn main:app --reload
Then open:     http://localhost:8000

Privacy: addresses and dates are used to answer the request and are never saved or logged by this app.
"""

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()  # reads keys from .env if it exists

import agent  # noqa: E402  (import after load_dotenv so keys are set)
import tools  # noqa: E402

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Reentry Compass")

RADII = (0.5, 1.0, 2.0)


@app.get("/api/config")
def config():
    return {
        "maps_key": os.getenv("GOOGLE_MAPS_API_KEY", ""),
        "areas": [{"id": a["id"], "name": a["name"], "lat": a["lat"], "lon": a["lon"], "zips": a["zips"]}
                  for a in tools.AREAS],
        "data": tools.data_status(),
        "today": date.today().isoformat(),
    }


@app.get("/api/geocode")
def geocode(q: str = Query(..., min_length=3, max_length=200)):
    try:
        return tools.geocode(q)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception:
        raise HTTPException(502, "The address lookup isn't answering right now. Pick an area below instead.")


@app.get("/api/nearby")
def nearby(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
           zip: str = Query("", max_length=10), radius: float = 1.0):
    if radius not in RADII:
        raise HTTPException(400, "Pick 0.5, 1, or 2 miles.")
    return tools.nearby(lat, lon, zip[:5], radius)


@app.get("/api/since")
def since(date_: str = Query(..., alias="date"), area: str = "", zip: str = Query("", max_length=10)):
    if area and area not in tools.AREA_BY_ID:
        raise HTTPException(400, "Pick one of the three areas.")
    try:
        return tools.changes_since(date_, area or None, zip[:5])
    except ValueError as e:
        raise HTTPException(400, str(e))


class ExplainRequest(BaseModel):
    kind: str = Field(pattern="^(area|since)$")
    language: str = "English"
    lat: float | None = None
    lon: float | None = None
    zip: str = ""
    radius_miles: float = 1.0
    since: str = ""
    area_id: str = ""


@app.post("/api/explain")
def explain(req: ExplainRequest):
    if req.language not in ("English", "Español", "Kreyòl ayisyen"):
        raise HTTPException(400, "Unsupported language.")
    if req.kind == "area":
        if req.lat is None or req.lon is None or req.radius_miles not in RADII:
            raise HTTPException(400, "Missing place.")
    elif not req.since:
        raise HTTPException(400, "Missing date.")
    try:
        return agent.explain(req.model_dump(), req.language)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/health")
def health():
    return {"ok": True, "ai_agent": bool(os.getenv("OPENAI_API_KEY")), "model": agent.MODEL,
            "data": tools.data_status()}


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
