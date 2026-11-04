"""Sail-by web server.

Run locally:   python -m uvicorn main:app --reload
Then open:     http://localhost:8000

Privacy: addresses and dates are used to answer the request and are never saved or logged by this app.
"""

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

load_dotenv()  # reads keys from .env if it exists

import agent  # noqa: E402  (import after load_dotenv so keys are set)
import tools  # noqa: E402

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Sail-by")
app.add_middleware(GZipMiddleware, minimum_size=1000)

RADII = (0.5, 1.0, 2.0)


@app.get("/api/config")
def config():
    return {
        "maps_key": os.getenv("GOOGLE_MAPS_API_KEY", ""),
        "areas": [{"id": a["id"], "name": a["name"], "lat": a["lat"], "lon": a["lon"], "zips": a["zips"]}
                  for a in tools.AREAS],
        "data": tools.data_status(),
        "place_kinds": tools.PLACE_KINDS,
        "translations": tools.TRANSLATIONS,
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


class AskRequest(BaseModel):
    message: str = Field(min_length=3, max_length=500)
    language: str = "English"
    history: list[dict] = []
    context: dict | None = None


@app.post("/api/ask")
def ask(req: AskRequest):
    if req.language not in ("English", "Español", "Kreyòl ayisyen"):
        raise HTTPException(400, "Unsupported language.")
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(503, "The AI helper isn't turned on yet. The other pages still work.")
    try:
        return agent.ask(req.message, req.language, req.history[-8:], req.context)
    except Exception as e:
        print(f"ask failed: {type(e).__name__}: {str(e)[:200]}")
        raise HTTPException(502, f"The AI helper had a problem ({type(e).__name__}). Try asking again, or use the other pages.")


@app.get("/api/curfew")
def curfew_endpoint(lat1: float = Query(..., ge=-90, le=90), lon1: float = Query(..., ge=-180, le=180),
                    lat2: float = Query(..., ge=-90, le=90), lon2: float = Query(..., ge=-180, le=180),
                    end: str = Query(...), curfew: str = Query(...), lang: str = Query("en", pattern="^(en|es|ht)$")):
    try:
        return tools.curfew_check(lat1, lon1, lat2, lon2, tools.parse_time(end), tools.parse_time(curfew), lang)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/lines")
def lines():
    return tools.route_lines()


@app.get("/api/health")
def health():
    return {"ok": True, "ai_agent": bool(os.getenv("OPENAI_API_KEY")), "model": agent.MODEL,
            "data": tools.data_status()}


# ---------- pages, and the files search engines and link previews look for ----------

PAGES = ("/", "/privacy", "/terms")


def site_url(request: Request):
    """The site's public address, for links that must be absolute (share image, sitemap).
    Set SITE_URL (for example https://sail-by.onrender.com) if the host reports the wrong one."""
    env = os.getenv("SITE_URL", "").rstrip("/")
    if env:
        return env
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return f"{proto}://{request.headers.get('host', request.url.netloc)}"


def page(name: str, request: Request, status: int = 200):
    """Serve an HTML page, filling in the site's address where it says %SITE_URL%."""
    html = (STATIC / name).read_text(encoding="utf-8").replace("%SITE_URL%", site_url(request))
    return HTMLResponse(html, status_code=status)


@app.get("/")
def home(request: Request):
    return page("index.html", request)


@app.get("/privacy")
def privacy_page(request: Request):
    return page("privacy.html", request)


@app.get("/terms")
def terms_page(request: Request):
    return page("terms.html", request)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC / "favicon.png", media_type="image/png")


@app.get("/apple-touch-icon.png", include_in_schema=False)
def apple_icon():
    return FileResponse(STATIC / "apple-touch-icon.png", media_type="image/png")


@app.get("/llms.txt", include_in_schema=False)
def llms_txt():
    return FileResponse(STATIC / "llms.txt", media_type="text/plain; charset=utf-8")


@app.get("/robots.txt", include_in_schema=False)
def robots(request: Request):
    return PlainTextResponse(f"User-agent: *\nAllow: /\nDisallow: /api/\n\nSitemap: {site_url(request)}/sitemap.xml\n")


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request):
    base, today = site_url(request), date.today().isoformat()
    urls = "".join(f"  <url><loc>{base}{p}</loc><lastmod>{today}</lastmod></url>\n" for p in PAGES)
    return Response(f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}</urlset>\n',
                    media_type="application/xml")


@app.exception_handler(StarletteHTTPException)
async def not_found(request: Request, exc: StarletteHTTPException):
    """A real page for people who land on a wrong address. The API keeps answering in JSON."""
    if exc.status_code == 404 and not request.url.path.startswith("/api/"):
        return page("404.html", request, 404)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


app.mount("/static", StaticFiles(directory=STATIC), name="static")
