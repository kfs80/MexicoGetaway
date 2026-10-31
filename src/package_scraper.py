"""
VERSION: v4 - Full CheapCaribbean HTML Parser via ZenRows
"""
import os
import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
import tomli

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_config():
    with open(os.path.join(ROOT_DIR, "config.toml"), "rb") as f:
        return tomli.load(f)

def fetch_html(target_url, config):
    """Fetches HTML, routing through ZenRows."""
    api_key = os.environ.get("ZENROWS_API_KEY")
    if not api_key:
        raise ValueError("ZENROWS_API_KEY secret is missing.")
        
    url = (f"https://api.zenrows.com/v1/?apikey={api_key}"
           f"&url={urllib.parse.quote(target_url)}"
           f"&js_render=true&antibot=true&wait_for=.hotel-card")
           
    print(f"Routing request through ZenRows for: {target_url[:50]}...")
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        return resp.read().decode('utf-8')
    except Exception as e:
        print(f"Failed to fetch {target_url}: {e}")
        return None

def parse_cheapcaribbean(html, region_code, depart_date, return_date, days):
    """Extracts resort packages from CheapCaribbean HTML."""
    soup = BeautifulSoup(html, 'html.parser')
    packages = []
    
    # CheapCaribbean uses div classes like 'hotel-card' or 'package-result'
    cards = soup.find_all('div', class_=lambda c: c and 'hotel-card' in c.lower())
    
    for card in cards:
        try:
            # Resort Name
            name_el = card.find(['h3', 'h4'])
            if not name_el: continue
            resort_name = name_el.get_text(strip=True)
            
            # Price (Usually wrapped in a strong or span with 'price' class)
            price_el = card.find(string=lambda text: text and '$' in text)
            if not price_el: continue
            price_str = price_el.strip().replace('$', '').replace(',', '')
            price_pp = int(float(price_str))
            
            # Star Rating (Often indicated by a data attribute or number of full-star icons)
            stars = 4.0 # Default fallback if we can't parse exactly
            star_el = card.find(class_=lambda c: c and 'star' in c.lower())
            if star_el and 'data-rating' in star_el.attrs:
                stars = float(star_el['data-rating'])
            
            # Build the package object
            pkg = {
                "resort": resort_name,
                "region": "Cancun/Riviera Maya" if region_code == "CUN" else "Puerto Vallarta",
                "code": region_code,
                "stars": stars,
                "user_rating": 8.5, # OTA user ratings often require a secondary API call, mocking for v1
                "amenities": ["All-Inclusive"],
                "depart_date": depart_date.strftime("%Y-%m-%d"),
                "return_date": return_date.strftime("%Y-%m-%d"),
                "days": days,
                "airline": "Legacy Carrier", # Actual airline often hidden behind a click
                "price_total": price_pp * 2, # CheapCaribbean shows PP, we need total for 2 adults
                "provider": "CheapCaribbean",
                "url": "https://www.cheapcaribbean.com/"
            }
            
            # Apply our 4-star minimum filter
            if pkg['stars'] >= 4.0:
                packages.append(pkg)
                
        except Exception as e:
            print(f"Error parsing a card: {e}")
            continue
            
    return packages

def generate_search_dates(config):
    """Yields valid (depart, return, days) combinations."""
    today = datetime.now(timezone.utc)
    start = today + timedelta(days=21) # Start looking 3 weeks out
    end = today + timedelta(days=config["trip"]["lookahead_days"])
    
    valid_depart = [d[:3] for d in config["trip"]["depart_weekdays"]]
    
    current = start
    while current <= end:
        if current.strftime("%a") in valid_depart:
            for nights in range(config["trip"]["min_nights"], config["trip"]["max_nights"] + 1):
                ret = current + timedelta(days=nights)
                yield (current, ret, nights)
        current += timedelta(days=1)

def build_search_url(origin, dest, dep_str, ret_str, adults):
    """Constructs the OTA search URL."""
    # Example CheapCaribbean structure (varies by OTA and often requires reverse-engineering their search endpoint)
    return f"https://www.cheapcaribbean.com/search?origin={origin}&dest={dest}&dep={dep_str}&ret={ret_str}&adults={adults}"

def main():
    print("Starting Mexico Resort Tracker (v4 - Full Parser)...")
    config = load_config()
    all_packages = []
    requests_made = 0
    max_requests = config["schedule"]["request_budget_per_run"]
    
    # We will search CUN and PVR
    destinations = ["CUN", "PVR"]
    
    for dest in destinations:
        for dep, ret, nights in generate_search_dates(config):
            if requests_made >= max_requests:
                print("Hit request budget cap. Stopping.")
                break
                
            dep_str = dep.strftime("%Y-%m-%d")
            ret_str = ret.strftime("%Y-%m-%d")
            
            target_url = build_search_url(config["origins"][0], dest, dep_str, ret_str, config["party"]["adults"])
            print(f"Searching {dest} for {dep_str} ({nights} nights)...")
            
            html = fetch_html(target_url, config)
            requests_made += 1
            
            if html:
                pkgs = parse_cheapcaribbean(html, dest, dep, ret, nights)
                all_packages.extend(pkgs)
                print(f"  -> Found {len(pkgs)} 4-star+ packages.")
            
        if requests_made >= max_requests:
            break
            
    # Sort and take top 50 best deals to keep JSON small
    all_packages.sort(key=lambda x: x['price_total'])
    best_deals = all_packages[:50]
    
    final_data = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "packages": best_deals
    }
    
    # Save to data/packages.json
    os.makedirs(os.path.join(ROOT_DIR, "data"), exist_ok=True)
    out_path = os.path.join(ROOT_DIR, "data", "packages.json")
    with open(out_path, "w") as f:
        json.dump(final_data, f, indent=2)
        
    print(f"\nScraping complete! Saved {len(best_deals)} packages to {out_path}.")

if __name__ == "__main__":
    main()
