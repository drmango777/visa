"""Reentry Compass web server.

Run locally:   uvicorn main:app --reload
Then open:     http://localhost:8000
"""

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()  # reads OPENAI_API_KEY from .env if it exists

import agent  # noqa: E402  (import after load_dotenv so the key is set)
import tools  # noqa: E402

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Reentry Compass")


class Profile(BaseModel):
    first_name: str = Field("", max_length=40)
    language: str = "English"
    checkin_site: str = "doral"
    obligation_end: str = "19:30"
    curfew: str = "21:00"
    budget: int = Field(900, ge=100, le=10000)
    radius_miles: int = 10
    needs: list[str] = []


@app.get("/api/options")
def options():
    return {
        "sites": [{"id": s["id"], "name": s["name"]} for s in tools.SITES],
        "languages": ["English", "Español", "Kreyòl ayisyen"],
        "radii": [5, 10, 20],
    }


@app.post("/api/plan")
def plan(profile: Profile):
    if profile.radius_miles not in (5, 10, 20):
        raise HTTPException(400, "Radius must be 5, 10, or 20 miles.")
    if profile.checkin_site not in tools.SITE_BY_ID:
        raise HTTPException(400, "Pick a check-in location from the list.")
    try:
        tools.parse_time(profile.obligation_end)
        tools.parse_time(profile.curfew)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return agent.run(profile.model_dump())


@app.get("/api/evidence")
def evidence():
    return tools.evidence_summary()


@app.get("/api/health")
def health():
    import os
    return {"ok": True, "ai_agent": bool(os.getenv("OPENAI_API_KEY")), "model": agent.MODEL}


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
