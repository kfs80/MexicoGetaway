"""
Optional guest-rating lookup using the Google Places API (New). Needs GOOGLE_PLACES_API_KEY.
Without the key everything here is a no-op and ratings simply stay unknown.

Results are cached per hotel in site/data/ratings.json and refreshed every `refresh_days`,
so a run only spends a limited number of lookups (`max_lookups_per_run`).
"""
import json
import os
import re
import urllib.error
import urllib.request
from datetime import date

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "site", "data", "ratings.json")
URL = "https://places.googleapis.com/v1/places:searchText"
STOP = {"all", "inclusive", "resort", "resorts", "spa", "hotel", "hotels", "by", "the", "and", "adults",
        "only", "beach", "club", "suites", "suite", "golf", "casino", "a", "of", "de", "la", "el", "at"}


class QuotaStop(Exception):
    pass


def load():
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(cache):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, separators=(",", ":"))


def tokens(s):
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP}


def same_hotel(ours, theirs):
    """Strict: the shorter name's distinctive words must all appear in the longer one.
    (Stops 'Riu Palace Peninsula' matching 'Riu Palace Pacifico'.)"""
    a, b = tokens(ours), tokens(theirs)
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    return len(small) >= 2 and small <= big


def lookup(name, region, key):
    body = json.dumps({"textQuery": f"{name}, {region}"}).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": "places.displayName,places.rating,places.userRatingCount",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"  ratings: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]}")
        if e.code in (401, 403, 429):
            raise QuotaStop()
        return None
    except Exception as e:
        print(f"  ratings: {type(e).__name__}")
        return None
    for pl in data.get("places", []):
        pname = (pl.get("displayName") or {}).get("text", "")
        if pl.get("rating") and same_hotel(name, pname):
            return {"matched": True, "rating": pl["rating"], "count": pl.get("userRatingCount", 0), "name": pname}
    return {"matched": False}


def refresh(cache, wanted, cfg, today):
    """wanted: [(hotel_code, name, region)] in priority order. Returns number of lookups made."""
    key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not key or not cfg.get("enabled", True):
        return 0
    budget, used = cfg.get("max_lookups_per_run", 80), 0
    for code, name, region in wanted:
        e = cache.get(code)
        if e and (today - date.fromisoformat(e["checked"])).days < cfg.get("refresh_days", 60):
            continue
        if used >= budget:
            break
        try:
            res = lookup(name, region, key)
        except QuotaStop:
            print("  ratings: stopping for this run (quota/permission)")
            break
        used += 1
        if res is not None:
            cache[code] = {"checked": today.isoformat(), **res}
    return used


def rating10(entry, min_reviews):
    """Google's 5-point rating on a 10-point scale, or None if unknown/too few reviews."""
    if entry and entry.get("matched") and entry.get("count", 0) >= min_reviews:
        return round(entry["rating"] * 2, 1), entry["count"]
    return None, None
