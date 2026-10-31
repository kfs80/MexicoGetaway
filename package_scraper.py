"""
VERSION: v3 - ZenRows Proxy Adapter with Flexible Dates
"""
import os
import sys
import json
import urllib.parse
import urllib.request
from datetime import datetime
import tomli

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_config():
    with open(os.path.join(ROOT_DIR, "config.toml"), "rb") as f:
        return tomli.load(f)

def fetch_html(target_url, config):
    """Fetches HTML, routing through ZenRows if enabled in config."""
    proxy_cfg = config.get("proxy", {})
    
    if proxy_cfg.get("enable"):
        api_key = os.environ.get("ZENROWS_API_KEY")
        if not api_key:
            raise ValueError("Proxy is enabled in config, but ZENROWS_API_KEY secret is missing.")
            
        # Wrap the target URL for ZenRows
        # 'js_render=true' and 'antibot=true' are critical for OTAs
        url = (f"https://api.zenrows.com/v1/?apikey={api_key}"
               f"&url={urllib.parse.quote(target_url)}"
               f"&js_render=true&antibot=true")
               
        print(f"Routing request through ZenRows for: {target_url[:50]}...")
    else:
        url = target_url
        print(f"Direct request (High risk of 403) to: {target_url[:50]}...")

    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    try:
        resp = urllib.request.urlopen(req, timeout=45) # ZenRows JS rendering can take 15-30s
        return resp.read().decode('utf-8')
    except Exception as e:
        print(f"Failed to fetch {target_url}: {e}")
        return None

def main():
    print("Starting Mexico Resort Tracker (ZenRows Edition)...")
    config = load_config()
    
    # Test against CheapCaribbean to verify ZenRows bypasses the Cloudflare block
    test_url = "https://www.cheapcaribbean.com/"
    html = fetch_html(test_url, config)
    
    if html:
        print(f"\n[✅ SUCCESS] ZenRows fetched {len(html)} bytes of HTML!")
        if "CheapCaribbean" in html or "vacation" in html.lower():
            print("[✅ VERIFIED] The HTML content is the actual website, not a Cloudflare block.")
    else:
        print("\n[❌ FAILED] Could not retrieve the page.")

if __name__ == "__main__":
    main()
