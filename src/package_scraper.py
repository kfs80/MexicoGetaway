"""
VERSION: v5 - CheapCaribbean parser via ZenRows, rotating coverage, merged results.

Run from the repo root:  python src/package_scraper.py
Writes:                  site/data/packages.json   (deployed with the site)
Debug HTML samples:      debug/                    (uploaded as a workflow artifact)
"""
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from bs4 import BeautifulSoup

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(ROOT_DIR, "site", "data", "packages.json")
DEBUG_DIR = os.path.join(ROOT_DIR, "debug")
BASE_URL = "https://www.cheapcaribbean.com/"
PRICE_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")
MIN_PLAUSIBLE_PP = 200  # ignore "$50 deposit"-style numbers


# ---------------------------------------------------------------- config / io
def load_config():
    with open(os.path.join(ROOT_DIR, "config.toml"), "rb") as f:
        return tomllib.load(f)


def load_destinations():
    with open(os.path.join(ROOT_DIR, "destinations.csv"), newline="", encoding="utf-8") as f:
        return {row["code"].strip(): row["region"].strip() for row in csv.DictReader(f)}


def load_previous():
    try:
        with open(OUT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("packages", []), int(data.get("cursor", 0))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return [], 0


def save_output(payload):
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, OUT_PATH)  # atomic: never leaves a half-written file


# -------------------------------------------------------------------- fetching
def fetch_html(target_url, config):
    """Fetch a page, through ZenRows when [proxy].enable is true. Returns text or None."""
    proxy = config.get("proxy", {})
    if proxy.get("enable", True):
        api_key = os.environ.get("ZENROWS_API_KEY")
        if not api_key:
            raise SystemExit("ZENROWS_API_KEY secret is missing.")
        params = {
            "apikey": api_key,
            "url": target_url,
            "js_render": "true",
            "antibot": "true",
        }
        if proxy.get("wait_for"):
            params["wait_for"] = proxy["wait_for"]
        if proxy.get("premium_proxy"):
            params["premium_proxy"] = "true"
        if proxy.get("country"):
            params["proxy_country"] = proxy["country"]
        url = "https://api.zenrows.com/v1/?" + urllib.parse.urlencode(params)
    else:
        url = target_url

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        # Never print the exception text/URL: the URL contains the API key.
        print(f"  fetch failed: HTTP {e.code}")
    except Exception as e:
        print(f"  fetch failed: {type(e).__name__}")
    return None


# --------------------------------------------------------------------- parsing
def extract_price_pp(card):
    """Lowest plausible $ amount in a price-ish element (skips struck-through 'was' prices)."""
    found = []
    for el in card.find_all(class_=lambda c: c and "price" in c.lower()):
        for m in PRICE_RE.finditer(el.get_text(" ", strip=True)):
            val = float(m.group(1).replace(",", ""))
            if val >= MIN_PLAUSIBLE_PP:
                found.append(val)
    if not found:
        for m in PRICE_RE.finditer(card.get_text(" ", strip=True)):
            val = float(m.group(1).replace(",", ""))
            if val >= MIN_PLAUSIBLE_PP:
                found.append(val)
    return int(min(found)) if found else None


def extract_stars(card):
    for el in card.find_all(attrs={"data-rating": True}):
        try:
            val = float(el["data-rating"])
            if 0 < val <= 5:
                return val
        except ValueError:
            pass
    m = re.search(r"(\d(?:\.\d)?)\s*[- ]?star", card.get_text(" ", strip=True), re.I)
    if m and 0 < float(m.group(1)) <= 5:
        return float(m.group(1))
    return None  # unknown, NOT a guessed 4.0


def parse_cheapcaribbean(html, code, region, dep, ret, nights, config, target_url):
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.find_all("div", class_=lambda c: c and "hotel-card" in c.lower())
    filters = config.get("resort_filters", {})
    adults = config["party"]["adults"]
    today = datetime.now(timezone.utc).date().isoformat()
    packages = []

    for card in cards:
        try:
            name_el = card.find(["h2", "h3", "h4"])
            if not name_el:
                continue
            price_pp = extract_price_pp(card)
            if price_pp is None:
                continue
            stars = extract_stars(card)
            text = card.get_text(" ", strip=True).lower()
            amenities = []
            if re.search(r"all[\s-]*inclusive", text):
                amenities.append("All-Inclusive")
            if re.search(r"beach[\s-]*front", text):
                amenities.append("Beachfront")
            if re.search(r"adults?[\s-]*only", text):
                amenities.append("Adults Only")

            if stars is not None and stars < filters.get("min_star_rating", 0):
                continue
            if filters.get("require_all_inclusive") and "All-Inclusive" not in amenities:
                continue
            if filters.get("adults_only") and "Adults Only" not in amenities:
                continue

            link = card.find("a", href=True)
            url = urllib.parse.urljoin(BASE_URL, link["href"]) if link else target_url

            packages.append({
                "resort": name_el.get_text(strip=True),
                "region": region,
                "code": code,
                "stars": stars,              # None = could not be parsed
                "user_rating": None,         # not on search cards; needs a detail-page lookup
                "amenities": amenities,
                "depart_date": dep.isoformat(),
                "return_date": ret.isoformat(),
                "nights": nights,
                "airline": None,             # usually hidden behind a click
                "price_pp": price_pp,        # assumes the card shows a per-person price
                "price_total": price_pp * adults,
                "provider": "CheapCaribbean",
                "url": url,
                "last_seen": today,
            })
        except Exception as e:
            print(f"  skipped a card: {type(e).__name__}")
    return packages, len(cards)


# ------------------------------------------------------------------ candidates
def build_candidates(config, codes):
    """All (code, depart, return, nights), ordered by date with destinations interleaved."""
    today = datetime.now(timezone.utc).date()
    start = today + timedelta(days=config["trip"].get("min_lead_days", 21))
    end = today + timedelta(days=config["trip"]["lookahead_days"])
    weekdays = {d[:3] for d in config["trip"]["depart_weekdays"]}
    out = []
    cur = start
    while cur <= end:
        if cur.strftime("%a") in weekdays:
            for nights in range(config["trip"]["min_nights"], config["trip"]["max_nights"] + 1):
                for code in codes:
                    out.append((code, cur, cur + timedelta(days=nights), nights))
        cur += timedelta(days=1)
    return out


def build_search_url(origin, dest, dep, ret, adults):
    # UNVERIFIED: this URL shape is a placeholder. Open CheapCaribbean in a browser, run a
    # real search, and copy the actual URL/query format from the address bar or network tab.
    return (f"{BASE_URL}search?origin={origin}&dest={dest}"
            f"&dep={dep.isoformat()}&ret={ret.isoformat()}&adults={adults}")


# ------------------------------------------------------------------ merge/trim
def trim(pkgs, per_resort, cap):
    pkgs = sorted(pkgs, key=lambda p: p["price_total"])
    counts, out = {}, []
    for p in pkgs:
        k = (p["code"], p["resort"])
        if counts.get(k, 0) >= per_resort:
            continue
        counts[k] = counts.get(k, 0) + 1
        out.append(p)
    return out[:cap]


def main():
    print("Starting Mexico Resort Tracker (v5)...")
    config = load_config()
    dests = load_destinations()
    sched = config["schedule"]
    budget = sched["request_budget_per_run"]
    deadline = time.monotonic() + sched.get("run_time_limit_minutes", 25) * 60
    pause = config.get("providers", {}).get("pause_seconds", 3.0)
    origin = config["origins"][0]
    adults = config["party"]["adults"]

    cands = build_candidates(config, list(dests))
    if not cands:
        raise SystemExit("No candidate search dates; check [trip] in config.toml.")

    prev, cursor = load_previous()
    today = datetime.now(timezone.utc).date()
    stale = sched.get("stale_after_days", 14)
    prev = [p for p in prev
            if p["depart_date"] >= today.isoformat()
            and (today - date.fromisoformat(p.get("last_seen", today.isoformat()))).days <= stale]

    start = cursor % len(cands)
    batch = [cands[(start + i) % len(cands)] for i in range(min(budget, len(cands)))]
    print(f"{len(cands)} candidate searches; running {len(batch)} starting at #{start}.")

    searched, new_pkgs = set(), []
    attempted = ok = zero_card_samples = 0
    for code, dep, ret, nights in batch:
        if time.monotonic() > deadline:
            print("Run time limit reached. Stopping.")
            break
        target = build_search_url(origin, code, dep, ret, adults)
        print(f"Searching {code} {dep} ({nights} nights)...")
        html = fetch_html(target, config)
        attempted += 1
        if html:
            ok += 1
            pkgs, n_cards = parse_cheapcaribbean(html, code, dests[code], dep, ret, nights, config, target)
            searched.add((code, dep.isoformat(), ret.isoformat()))
            new_pkgs.extend(pkgs)
            print(f"  -> {n_cards} cards, {len(pkgs)} kept.")
            if n_cards == 0 and zero_card_samples < 2:
                os.makedirs(DEBUG_DIR, exist_ok=True)
                with open(os.path.join(DEBUG_DIR, f"zero_cards_{code}_{dep}.html"), "w", encoding="utf-8") as f:
                    f.write(html)
                zero_card_samples += 1
        time.sleep(pause)

    if attempted and ok == 0:
        # Keep the previous good data instead of overwriting it with nothing.
        raise SystemExit("Every request failed. Leaving packages.json untouched.")

    # Replace old results for the exact searches we just redid, keep everything else.
    kept = [p for p in prev if (p["code"], p["depart_date"], p["return_date"]) not in searched]
    merged = {}
    for p in kept + new_pkgs:
        key = (p["code"], p["resort"], p["depart_date"], p["return_date"], p["provider"])
        if key not in merged or p["price_total"] < merged[key]["price_total"]:
            merged[key] = p
    final = trim(list(merged.values()), config["output"]["max_per_resort"], config["output"]["max_packages"])

    save_output({
        "generated": datetime.now(timezone.utc).isoformat(),
        "cursor": (start + attempted) % len(cands),
        "stats": {"requests": attempted, "ok": ok, "new_packages": len(new_pkgs)},
        "packages": final,
    })
    print(f"Done. {attempted} requests ({ok} ok), {len(final)} packages saved to {OUT_PATH}.")


if __name__ == "__main__":
    sys.exit(main())
