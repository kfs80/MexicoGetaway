"""
VERSION: v7 - price-cache API, monthly picks, date calendars, price history, ratings, alerts.

    GET {API}/originhotels/DSM   hotel metadata (name, stars, all-inclusive flag, ...)
    GET {API}/DSM/{nights}       prices: priceCacheItems[].td = [{d: "YYYYMMDD", p: price}]

Responses are a JSON string holding base64(gzip(JSON)).
Run from the repo root:  python src/package_scraper.py
Writes site/data/{packages,history,ratings,alert_state}.json and alerts_out/ (for the workflow).
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
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ratings  # noqa: E402

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "site", "data")
OUT_PATH = os.path.join(DATA_DIR, "packages.json")
HIST_PATH = os.path.join(DATA_DIR, "history.json")
STATE_PATH = os.path.join(DATA_DIR, "alert_state.json")
ALERT_DIR = os.path.join(ROOT_DIR, "alerts_out")
API = "https://api.cheapcaribbean.com/cheapcaribbean/pricecache"
SEARCH_URL = "https://beachfinder.cheapcaribbean.com/destination-less/results"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://beachfinder.cheapcaribbean.com",
    "Referer": "https://beachfinder.cheapcaribbean.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/156.0 Safari/537.36",
}
HISTORY_DAYS = 90        # keep this much price history
HISTORY_IDLE_DAYS = 14   # drop a history series not refreshed in this long


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
        params = {"apikey": key, "url": url}
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
    """Every qualifying (resort, departure date) price for one trip length."""
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
                "user_rating": None,
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


def best_per_month(pkgs):
    """Per (month, nights, resort): the cheapest date, plus a day-of-month price calendar."""
    best, cal = {}, {}
    for p in pkgs:
        k = (p["depart_date"][:7], p["nights"], p["hotel_code"])
        day = int(p["depart_date"][8:10])
        if k not in cal:
            y, m = int(k[0][:4]), int(k[0][5:7])
            cal[k] = [None] * monthrange(y, m)[1]
        cal[k][day - 1] = p["price_total"]
        if k not in best or p["price_total"] < best[k]["price_total"]:
            best[k] = p
    for k, p in best.items():
        p["month"] = k[0]
        p["cal"] = cal[k]
    return best


def select_pool(best, excluded, pool):
    groups = {}
    for (month, nights, code), p in best.items():
        if code in excluded:
            continue
        groups.setdefault((month, nights), []).append(p)
    out = []
    for g in groups.values():
        g.sort(key=lambda p: p["price_total"])
        out.extend(g[:pool])
    return out


# -------------------------------------------------------------------- history
def update_history(final, today):
    """One point per day per (resort, nights, month): that month's cheapest price."""
    hist = load_json(HIST_PATH, {})
    cutoff = (today - timedelta(days=HISTORY_DAYS)).isoformat()
    idle = (today - timedelta(days=HISTORY_IDLE_DAYS)).isoformat()
    hist = {k: v for k, v in hist.items() if k.count("|") == 2 and v and v[-1][0] >= idle}
    for p in final:
        k = f"{p['hotel_code']}|{p['nights']}|{p['month']}"
        series = [pt for pt in hist.get(k, []) if pt[0] != today.isoformat() and pt[0] >= cutoff]
        series.append([today.isoformat(), p["price_total"]])
        hist[k] = series
    return hist


def annotate_history(final, hist, today):
    for p in final:
        series = hist.get(f"{p['hotel_code']}|{p['nights']}|{p['month']}", [])
        p["hist_days"] = len(series)
        p["hist_min"] = min((pt[1] for pt in series), default=None)
        ref = None  # the point closest to 7 days ago, if it is 5-10 days old
        for d, price in series:
            age = (today - date.fromisoformat(d)).days
            if 5 <= age <= 10 and (ref is None or abs(age - 7) < abs(ref[0] - 7)):
                ref = (age, price)
        p["chg_7d"] = p["price_total"] - ref[1] if ref else None


# --------------------------------------------------------------------- alerts
def rule_matches(rule, p):
    if rule.get("months") and p["month"] not in rule["months"]:
        return False
    if rule.get("codes") and p["code"] not in rule["codes"]:
        return False
    if rule.get("nights") and p["nights"] not in rule["nights"]:
        return False
    if rule.get("min_stars") and (p["stars"] or 0) < rule["min_stars"]:
        return False
    tests = []
    if "max_total" in rule:
        tests.append(p["price_total"] <= rule["max_total"])
    if "max_per_night" in rule:
        tests.append(p["price_total"] / p["nights"] <= rule["max_per_night"])
    if "min_pct_below" in rule:
        tests.append(p["pct_below"] >= rule["min_pct_below"])
    return bool(tests) and all(tests)  # a rule with no price test never fires


def run_alerts(best_pkgs, config, today):
    acfg = config.get("alerts", {})
    if not acfg.get("enabled") or not acfg.get("rules"):
        return
    drop = acfg.get("realert_drop_pct", 5) / 100
    cap = acfg.get("max_per_rule", 8)
    state = load_json(STATE_PATH, {})
    this_month = today.strftime("%Y-%m")
    state = {k: v for k, v in state.items() if k.split("|")[-1] >= this_month}  # drop past months

    lines, total_new, top, low = [], 0, [], None
    for rule in acfg["rules"]:
        hits = sorted((p for p in best_pkgs if rule_matches(rule, p)), key=lambda p: p["price_total"])
        fresh, used = [], set()
        for p in hits:
            key = f"{p['hotel_code']}|{p['month']}"  # one alert per resort per month (cheapest option)
            if key in used:
                continue
            used.add(key)
            old = state.get(key)
            if old is None or p["price_total"] <= old * (1 - drop):
                fresh.append(p)
                state[key] = p["price_total"]
        if not fresh:
            continue
        total_new += len(fresh)
        lines.append(f"## {rule.get('name', 'Deal alert')}\n")
        for p in fresh[:cap]:
            fl = "https://www.google.com/travel/flights?q=" + urllib.parse.quote(
                f"Flights to {p['code']} from {config['origins'][0]} on {p['depart_date']} through {p['return_date']}")
            below = f" · {p['pct_below']}% below typical" if p["pct_below"] >= 5 else ""
            lines.append(f"- **{p['resort']}** ({p['region']}, {p['stars']}★): **${p['price_total']:,}** for "
                         f"{config['party']['adults']} · {p['depart_date']} to {p['return_date']} "
                         f"({p['nights']} nights){below} · [book]({p['url']}) · [flights]({fl})")
            low = p["price_total"] if low is None else min(low, p["price_total"])
            if len(top) < 3:
                top.append(f"{p['resort']}: ${p['price_total']:,} ({p['depart_date']}, {p['nights']} nights)")
        if len(fresh) > cap:
            lines.append(f"- ...and {len(fresh) - cap} more matches on the site.")
        lines.append("")

    save_json(STATE_PATH, state)
    if not total_new:
        print("Alerts: nothing new.")
        return
    os.makedirs(ALERT_DIR, exist_ok=True)
    with open(os.path.join(ALERT_DIR, "title.txt"), "w", encoding="utf-8") as f:
        f.write(f"Deal alert: {total_new} new or lower-priced deal(s), best ${low:,}")
    with open(os.path.join(ALERT_DIR, "alert.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(os.path.join(ALERT_DIR, "ntfy.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(top))
    print(f"Alerts: {total_new} new deal(s) written to alerts_out/.")


# ----------------------------------------------------------------------- main
def main():
    print("Mexico Resort Tracker v7")
    config = load_config()
    code_to_region = load_destinations()
    origin = config["origins"][0]
    today = datetime.now(timezone.utc).date()
    pause = config.get("providers", {}).get("pause_seconds", 3.0)
    pool = config["output"]["per_month_pool"]

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

    best = best_per_month(all_pkgs)

    # Ratings: look up the resorts currently in contention, then drop known-low-rated ones.
    rcfg = config.get("ratings", {})
    cache = ratings.load()
    wanted, seen = [], set()
    for p in sorted(select_pool(best, set(), pool), key=lambda p: p["price_total"]):
        if p["hotel_code"] not in seen:
            seen.add(p["hotel_code"])
            wanted.append((p["hotel_code"], p["resort"], p["region"]))
    used = ratings.refresh(cache, wanted, rcfg, today)
    if used:
        ratings.save(cache)
        print(f"  ratings: {used} lookups")
    min_rating = config.get("resort_filters", {}).get("min_user_rating")
    excluded = set()
    for p in best.values():
        r, cnt = ratings.rating10(cache.get(p["hotel_code"]), rcfg.get("min_reviews", 100))
        p["user_rating"], p["rating_count"] = r, cnt
        if min_rating and r is not None and r < min_rating:
            excluded.add(p["hotel_code"])

    final = sorted(select_pool(best, excluded, pool), key=lambda p: (p["month"], p["price_total"]))
    hist = update_history(final, today)
    annotate_history(final, hist, today)
    run_alerts([p for p in best.values() if p["hotel_code"] not in excluded], config, today)

    save_json(OUT_PATH, {
        "generated": datetime.now(timezone.utc).isoformat(),
        "stats": {"night_lengths_ok": ok, "qualifying_prices": len(all_pkgs), "excluded_low_rated": len(excluded)},
        "packages": final,
    })
    save_json(HIST_PATH, hist)
    print(f"Done. {len(final)} deals saved ({len(all_pkgs)} qualifying prices scanned, "
          f"{len(excluded)} low-rated resorts hidden).")


if __name__ == "__main__":
    sys.exit(main())
