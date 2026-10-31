"""
VERSION: v6 - CheapCaribbean price-cache API (no HTML scraping).

The Beach Finder site loads its prices from a public JSON API. One request per trip length
returns every hotel's price for every departure date, so a full run is ~4 requests instead
of ~1,000 page scrapes.

    GET {API}/originhotels/DSM   hotel metadata (name, stars, all-inclusive flag, ...)
    GET {API}/DSM/{nights}       prices: priceCacheItems[].td = [{d: "YYYYMMDD", p: price}]

Responses are a JSON string holding base64(gzip(JSON)).
Run from the repo root:  python src/package_scraper.py
Writes: site/data/packages.json (deals) and site/data/history.json (daily price lows).
"""
import base64
import csv
import gzip
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "site", "data")
OUT_PATH = os.path.join(DATA_DIR, "packages.json")
HIST_PATH = os.path.join(DATA_DIR, "history.json")
API = "https://api.cheapcaribbean.com/cheapcaribbean/pricecache"
SEARCH_URL = "https://beachfinder.cheapcaribbean.com/destination-less/results"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://beachfinder.cheapcaribbean.com",
    "Referer": "https://beachfinder.cheapcaribbean.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/156.0 Safari/537.36",
}
HISTORY_DAYS = 120


# ---------------------------------------------------------------- config / io
def load_config():
    with open(os.path.join(ROOT_DIR, "config.toml"), "rb") as f:
        return tomllib.load(f)


def load_destinations():
    with open(os.path.join(ROOT_DIR, "destinations.csv"), newline="", encoding="utf-8") as f:
        return {r["code"].strip(): r["region"].strip() for r in csv.DictReader(f)}


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, payload, indent=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=indent, separators=None if indent else (",", ":"))
    os.replace(tmp, path)  # atomic


# -------------------------------------------------------------------- fetching
def _get(url, timeout=60):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _decode(body):
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    data = json.loads(body.decode("utf-8"))
    if isinstance(data, str):  # base64(gzip(json)) wrapped in a JSON string
        data = json.loads(gzip.decompress(base64.b64decode(data)).decode("utf-8"))
    return data


def fetch_api(path, config):
    """Try the API directly; if blocked and a ZenRows key exists, retry through ZenRows."""
    url = f"{API}/{path}"
    attempts = [("direct", url)]
    key = os.environ.get("ZENROWS_API_KEY")
    proxy = config.get("proxy", {})
    if key and proxy.get("enable", True):
        params = {"apikey": key, "url": url}  # plain JSON endpoint: no JS rendering needed
        if proxy.get("premium_proxy"):
            params["premium_proxy"] = "true"
        attempts.append(("zenrows", "https://api.zenrows.com/v1/?" + urllib.parse.urlencode(params)))

    for label, target in attempts:
        try:
            data = _decode(_get(target))
            print(f"  {path}: ok via {label}")
            return data
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                body = ""
            if key:
                body = body.replace(key, "***")
            print(f"  {path}: {label} failed HTTP {e.code} {body}")  # never print the URL (has key)
        except Exception as e:
            print(f"  {path}: {label} failed {type(e).__name__}")
    return None


# -------------------------------------------------------------------- building
def hotel_table(raw):
    cols = [c["n"] for c in raw[0]["ci"]]
    return {str(row[0]): dict(zip(cols, row)) for row in raw[0]["hi"]}


def search_link(origin, code, depart, nights):
    q = {"st": "destination-less", "o": origin, "n": nights, "sd": depart, "ed": depart,
         "d": code, "isDateRange": "true"}
    return SEARCH_URL + "?" + urllib.parse.urlencode(q)


def build_packages(items, hotels, code_to_region, nights, config, today):
    trip, filt = config["trip"], config.get("resort_filters", {})
    adults = config["party"]["adults"]
    per_person = config.get("pricing", {}).get("unit", "per_person") == "per_person"
    mult = adults if per_person else 1
    origin = config["origins"][0]
    weekdays = {d[:3] for d in trip["depart_weekdays"]}
    first = today + timedelta(days=trip.get("min_lead_days", 21))
    last = today + timedelta(days=trip["lookahead_days"])
    out = []

    for it in items:
        code = it.get("d")
        if code not in code_to_region:
            continue
        h = hotels.get(str(it["hotel"]["hc"]))
        if not h:
            continue
        stars = h.get("Hotel_Class")
        if filt.get("min_star_rating") and (stars is None or stars < filt["min_star_rating"]):
            continue
        if filt.get("require_all_inclusive") and not h.get("All_Inclusive"):
            continue
        if filt.get("adults_only") and not h.get("Adults_only"):
            continue

        rows = []
        for t in it.get("td", []):
            try:
                dep = datetime.strptime(t["d"], "%Y%m%d").date()
            except (KeyError, ValueError):
                continue
            if first <= dep <= last and dep.strftime("%a") in weekdays and t.get("p"):
                rows.append((dep, float(t["p"])))
        if not rows:
            continue

        med = statistics.median(p for _, p in rows)  # this resort's typical price for this length
        for dep, p in rows:
            out.append({
                "resort": h.get("Hotel_Name") or it.get("i", {}).get("alt", "Unknown"),
                "hotel_code": str(it["hotel"]["hc"]),
                "region": code_to_region[code],
                "code": code,
                "stars": stars,
                "user_rating": None,  # not in this data
                "amenities": [a for a, on in (("All-Inclusive", h.get("All_Inclusive")),
                                              ("Adults Only", h.get("Adults_only"))) if on],
                "depart_date": dep.isoformat(),
                "return_date": (dep + timedelta(days=nights)).isoformat(),
                "nights": nights,
                "airline": None,
                "price_pp": round(p / (1 if per_person else adults)),
                "price_total": round(p * mult),
                "typical_total": round(med * mult),
                "pct_below": round(100 * (1 - p / med)) if med else 0,
                "provider": "CheapCaribbean",
                "url": search_link(origin, code, dep.strftime("%Y-%m-%d"), nights),
                "last_seen": today.isoformat(),
            })
    return out


def trim(pkgs, per_resort_nights, cap):
    pkgs = sorted(pkgs, key=lambda p: p["price_total"])
    seen, out = {}, []
    for p in pkgs:
        k = (p["hotel_code"], p["nights"])
        if seen.get(k, 0) >= per_resort_nights:
            continue
        seen[k] = seen.get(k, 0) + 1
        out.append(p)
    return out[:cap]


def update_history(pkgs_all, today):
    """One point per day per (resort, nights): that day's cheapest price."""
    hist = load_json(HIST_PATH, {})
    cutoff = (today - timedelta(days=HISTORY_DAYS)).isoformat()
    lows = {}
    for p in pkgs_all:
        k = f"{p['hotel_code']}|{p['nights']}"
        lows[k] = min(lows.get(k, 10**9), p["price_total"])
    for k, price in lows.items():
        series = [pt for pt in hist.get(k, []) if pt[0] != today.isoformat() and pt[0] >= cutoff]
        series.append([today.isoformat(), price])
        hist[k] = series
    return hist


def main():
    print("Mexico Resort Tracker v6 (price-cache API)")
    config = load_config()
    code_to_region = load_destinations()
    origin = config["origins"][0]
    today = datetime.now(timezone.utc).date()
    pause = config.get("providers", {}).get("pause_seconds", 3.0)

    raw_hotels = fetch_api(f"originhotels/{origin}", config)
    if not raw_hotels:
        raise SystemExit("Could not load hotel list. Leaving packages.json untouched.")
    hotels = hotel_table(raw_hotels)

    all_pkgs, ok = [], 0
    for n in range(config["trip"]["min_nights"], config["trip"]["max_nights"] + 1):
        time.sleep(pause)
        data = fetch_api(f"{origin}/{n}", config)
        if not data:
            continue
        ok += 1
        items = data[0]["OriginData"]["priceCacheItems"]
        pk = build_packages(items, hotels, code_to_region, n, config, today)
        print(f"  {n} nights: {len(items)} price items -> {len(pk)} qualifying date prices")
        all_pkgs.extend(pk)

    if ok == 0:
        raise SystemExit("Every price request failed. Leaving packages.json untouched.")

    hist = update_history(all_pkgs, today)
    final = trim(all_pkgs, config["output"]["max_per_resort_nights"], config["output"]["max_packages"])
    for p in final:  # attach history context
        series = hist.get(f"{p['hotel_code']}|{p['nights']}", [])
        p["hist_days"] = len(series)
        p["hist_min"] = min((pt[1] for pt in series), default=None)

    save_json(OUT_PATH, {
        "generated": datetime.now(timezone.utc).isoformat(),
        "stats": {"night_lengths_ok": ok, "qualifying_prices": len(all_pkgs)},
        "packages": final,
    }, indent=1)
    save_json(HIST_PATH, hist)
    print(f"Done. {len(final)} deals saved ({len(all_pkgs)} qualifying prices scanned).")


if __name__ == "__main__":
    sys.exit(main())
