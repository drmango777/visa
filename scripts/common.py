"""Shared helpers for the data scripts. Standard library only."""

import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "cache"
USER_AGENT = "Sail-by/2.0 (Impact Miami hackathon project)"

# Load .env so optional API keys work without exporting them by hand.
_env = ROOT / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def load_areas():
    with open(DATA / "areas.json", encoding="utf-8") as f:
        return json.load(f)


def miles_between(lat1, lon1, lat2, lon2):
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def http(url, params=None, data=None, headers=None, timeout=60, tries=3):
    """GET (or POST if data is given). Returns bytes. Retries on network errors."""
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, safe=":,*")
    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})
    body = data.encode() if isinstance(data, str) else data
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            # 4xx means the request itself is wrong; retrying won't help.
            if 400 <= e.code < 500 and e.code != 429:
                raise
            last = e
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(2 * (attempt + 1))
    raise last


def write_json(name, obj):
    path = DATA / name
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)
    print(f"  wrote data/{name} ({path.stat().st_size // 1024} KB)")


def today():
    return date.today().isoformat()
